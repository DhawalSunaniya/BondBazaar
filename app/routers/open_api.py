import asyncio
import datetime
from datetime import date, timedelta
from typing import Optional, Dict, Any, List
from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entities import (
    User, Holding, SecondaryOrder, WalletTransaction, Instrument,
    ApiConsent, ApiClient, ApiCallLog, SystemState
)
from app.schemas.open_api import (
    LinkTokenRequest, LinkTokenResponse,
    ExchangeTokenRequest, ExchangeTokenResponse,
    RevokeConsentRequest, RenewConsentRequest
)
from app.services.auth_service import (
    authenticate_api_client, create_link_token_record,
    exchange_public_token_for_access, authenticate_bearer_token, hash_secret
)
from app.services.serializer import (
    serialize_customer, serialize_holdings_list, serialize_instrument,
    serialize_instruments_list, serialize_transactions_list, serialize_wallet,
    serialize_error, IST, rupees_to_paise, rate_to_bps, get_current_ist_iso
)
from app.config import settings

router = APIRouter(prefix="/open/v1", tags=["Open API"])

# In-memory rate limiting: {token_hash: [timestamps]}
RATE_LIMIT_STORE: Dict[str, List[datetime.datetime]] = {}

def check_rate_limit_and_system(request: Request, db: Session, token_hash: Optional[str] = None):
    # Check simulated outage
    state = db.query(SystemState).first()
    if state:
        if state.simulate_outage:
            outage_limit = state.outage_until
            if outage_limit and outage_limit.tzinfo is not None:
                outage_limit = outage_limit.astimezone(datetime.timezone.utc).replace(tzinfo=None)
            if outage_limit and datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None) > outage_limit:
                state.simulate_outage = False
                db.commit()
            else:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail={"errors": [{"code": "SERVICE_UNAVAILABLE", "detail": "BondBazaar Open API is temporarily undergoing scheduled maintenance."}]}
                )

    if token_hash:
        now = datetime.datetime.now(IST)
        window_start = now - timedelta(minutes=1)
        calls = RATE_LIMIT_STORE.get(token_hash, [])
        # Filter calls in last 60 seconds
        recent = [t for t in calls if t > window_start]
        if len(recent) >= 30:
            RATE_LIMIT_STORE[token_hash] = recent
            response = Response(status_code=status.HTTP_429_TOO_MANY_REQUESTS)
            response.headers["Retry-After"] = "60"
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail={"errors": [{"code": "RATE_LIMIT_EXCEEDED", "detail": "Rate limit exceeded (30 requests/minute). Please retry after 60 seconds."}]},
                headers={"Retry-After": "60"}
            )
        recent.append(now)
        RATE_LIMIT_STORE[token_hash] = recent

async def apply_slow_mode_if_enabled(db: Session):
    state = db.query(SystemState).first()
    if state and state.slow_mode:
        await asyncio.sleep(5.0)

def log_api_call(db: Session, request: Request, status_code: int, client_id: Optional[str] = None, consent_id: Optional[str] = None):
    try:
        log_entry = ApiCallLog(
            client_id=client_id,
            consent_id=consent_id,
            endpoint=str(request.url.path),
            method=request.method,
            status_code=status_code,
            ip_address=request.client.host if request.client else "127.0.0.1"
        )
        db.add(log_entry)
        db.commit()
    except Exception:
        pass

@router.post("/link/token", response_model=LinkTokenResponse)
async def create_link_token(
    payload: LinkTokenRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    client = authenticate_api_client(db, authorization)
    if not client:
        log_api_call(db, request, 401)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"errors": [{"code": "INVALID_CLIENT", "detail": "Invalid or missing Basic authentication credentials."}]}
        )

    check_rate_limit_and_system(request, db)

    # Resolve host
    host_url = str(request.base_url).rstrip("/")
    result = create_link_token_record(
        db, client, payload.scopes, payload.redirect_uri, payload.webhook_url, host_url
    )
    log_api_call(db, request, 200, client_id=client.client_id)
    return result

@router.post("/link/exchange", response_model=ExchangeTokenResponse)
async def exchange_link_token(
    payload: ExchangeTokenRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    client = authenticate_api_client(db, authorization)
    if not client:
        log_api_call(db, request, 401)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"errors": [{"code": "INVALID_CLIENT", "detail": "Invalid or missing Basic authentication credentials."}]}
        )

    check_rate_limit_and_system(request, db)

    success, data, error = exchange_public_token_for_access(db, client, payload.publicToken)
    if not success or not data:
        log_api_call(db, request, 400, client_id=client.client_id)
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"errors": [{"code": "INVALID_PUBLIC_TOKEN", "detail": error or "Could not exchange public token."}]}
        )

    log_api_call(db, request, 200, client_id=client.client_id, consent_id=data["consentId"])
    return data

@router.post("/consent/revoke")
async def revoke_consent(
    payload: RevokeConsentRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    consent = db.query(ApiConsent).filter(ApiConsent.consent_id == payload.consentId).first()
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"errors": [{"code": "CONSENT_NOT_FOUND", "detail": "Consent ID not found."}]}
        )

    consent.status = "REVOKED"
    db.commit()
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return {"meta": {"message": "Consent successfully revoked.", "consentId": consent.consent_id}}

@router.post("/consent/renew")
async def renew_consent(
    payload: RenewConsentRequest,
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    consent = db.query(ApiConsent).filter(ApiConsent.consent_id == payload.consentId).first()
    if not consent:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"errors": [{"code": "CONSENT_NOT_FOUND", "detail": "Consent ID not found."}]}
        )

    client = db.query(ApiClient).filter(ApiClient.client_id == consent.client_id).first()
    host_url = str(request.base_url).rstrip("/")
    import json
    scopes = json.loads(consent.scopes) if consent.scopes else ["holdings", "profile"]
    
    result = create_link_token_record(
        db, client, scopes, client.allowed_redirect_uris.split(",")[0], consent.webhook_url, host_url
    )
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return {
        "linkUrl": result["linkUrl"],
        "linkToken": result["linkToken"],
        "expiresAt": result["expiresAt"]
    }

def get_authorized_user_and_consent(
    request: Request,
    authorization: Optional[str] = Header(None),
    db: Session = Depends(get_db)
):
    token_hash = hash_secret(authorization.split(" ", 1)[1].strip()) if authorization and " " in authorization else None
    check_rate_limit_and_system(request, db, token_hash)
    
    user, consent, error_code = authenticate_bearer_token(db, authorization)
    if error_code == "CONSENT_REVOKED":
        log_api_call(db, request, 403, consent_id=consent.consent_id if consent else None)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"errors": [{"code": "CONSENT_REVOKED", "detail": "Consent was revoked by the user."}]}
        )
    elif error_code == "CONSENT_EXPIRED":
        log_api_call(db, request, 403, consent_id=consent.consent_id if consent else None)
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={"errors": [{"code": "CONSENT_EXPIRED", "detail": "Consent has expired. Please run the link flow again."}]}
        )
    elif error_code == "INVALID_TOKEN" or not user:
        log_api_call(db, request, 401)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail={"errors": [{"code": "INVALID_TOKEN", "detail": "Invalid or missing Bearer access token."}]}
        )

    return user, consent

@router.get("/customer")
async def get_customer(
    request: Request,
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return serialize_customer(user)

@router.get("/holdings")
async def get_holdings(
    request: Request,
    limit: int = Query(20, ge=1, le=100),
    cursor: Optional[str] = Query(None),
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    
    query = db.query(Holding).filter(Holding.user_id == user.id).order_by(Holding.id.asc())
    
    # Cursor pagination: cursor is the ID of the last seen holding
    if cursor:
        try:
            last_id = int(cursor)
            query = query.filter(Holding.id > last_id)
        except ValueError:
            pass

    holdings = query.limit(limit + 1).all()
    has_next = len(holdings) > limit
    returned_holdings = holdings[:limit]
    next_cursor = str(returned_holdings[-1].id) if (has_next and returned_holdings) else None

    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return serialize_holdings_list(returned_holdings, limit=limit, cursor=cursor, next_cursor=next_cursor)

@router.get("/transactions")
async def get_transactions(
    request: Request,
    from_date: Optional[str] = Query(None, alias="from"),
    to_date: Optional[str] = Query(None, alias="to"),
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    query = db.query(SecondaryOrder).filter(SecondaryOrder.user_id == user.id)

    if from_date:
        try:
            fd = date.fromisoformat(from_date)
            query = query.filter(SecondaryOrder.settlement_date >= fd)
        except ValueError:
            pass
    if to_date:
        try:
            td = date.fromisoformat(to_date)
            query = query.filter(SecondaryOrder.settlement_date <= td)
        except ValueError:
            pass

    orders = query.order_by(SecondaryOrder.settlement_date.desc()).all()
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return serialize_transactions_list(orders)

@router.get("/income/upcoming")
async def get_upcoming_income(
    request: Request,
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    today = date.today()

    events = []
    from app.services.bond_math import get_frequency_count, generate_cash_flow_dates

    for h in holdings:
        inst = h.instrument
        m = get_frequency_count(inst.coupon_frequency)
        cf_dates = [d for d in generate_cash_flow_dates(inst.issue_date, inst.maturity_date, inst.coupon_frequency) if d >= today]
        
        c_amt_per_unit = (inst.face_value * inst.coupon_rate / m) if m > 0 else 0.0

        for d in cf_dates[:6]: # next 6 cash flow events per instrument
            is_mat = (d == inst.maturity_date)
            if m > 0:
                events.append({
                    "type": "income_event",
                    "id": f"inc_c_{h.id}_{d.isoformat()}",
                    "attributes": {
                        "isin": inst.isin,
                        "instrumentName": inst.name,
                        "eventType": "COUPON",
                        "eventDate": d.isoformat(),
                        "units": h.units,
                        "payoutPerUnitPaise": rupees_to_paise(c_amt_per_unit),
                        "expectedAmountPaise": rupees_to_paise(c_amt_per_unit * h.units)
                    }
                })
            if is_mat:
                events.append({
                    "type": "income_event",
                    "id": f"inc_m_{h.id}_{d.isoformat()}",
                    "attributes": {
                        "isin": inst.isin,
                        "instrumentName": inst.name,
                        "eventType": "REDEMPTION",
                        "eventDate": d.isoformat(),
                        "units": h.units,
                        "payoutPerUnitPaise": rupees_to_paise(inst.face_value),
                        "expectedAmountPaise": rupees_to_paise(inst.face_value * h.units)
                    }
                })

    events.sort(key=lambda x: x["attributes"]["eventDate"])
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return {
        "data": events,
        "meta": {
            "generatedAt": get_current_ist_iso(),
            "count": len(events)
        }
    }

@router.get("/wallet")
async def get_wallet(
    request: Request,
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    txs = db.query(WalletTransaction).filter(WalletTransaction.user_id == user.id).order_by(WalletTransaction.created_at.desc()).all()
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return serialize_wallet(user, txs)

@router.get("/instruments/{isin}")
async def get_instrument_by_isin(
    isin: str,
    request: Request,
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    inst = db.query(Instrument).filter(Instrument.isin == isin).first()
    if not inst:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"errors": [{"code": "INSTRUMENT_NOT_FOUND", "detail": f"Instrument with ISIN {isin} not found."}]}
        )
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return {"data": serialize_instrument(inst)["attributes"], "meta": {"generatedAt": get_current_ist_iso()}}

@router.get("/instruments")
async def get_instruments(
    request: Request,
    type: Optional[str] = Query(None),
    auth_data = Depends(get_authorized_user_and_consent),
    db: Session = Depends(get_db)
):
    await apply_slow_mode_if_enabled(db)
    user, consent = auth_data
    query = db.query(Instrument).filter(Instrument.is_active == True)
    if type:
        query = query.filter(Instrument.instrument_type == type.upper())
    
    insts = query.all()
    log_api_call(db, request, 200, client_id=consent.client_id, consent_id=consent.consent_id)
    return serialize_instruments_list(insts)
