"""
Internal API for TradeOne aggregator — Provider C (BondBazaar).

Enabled only when INTERNAL_API_ENABLED=true.
All endpoints require header: x-internal-key (constant-time comparison).
Rate limited: 60 requests/minute per IP.
Never logs the key value.

Endpoints:
  POST /internal/v1/users/provision
  GET  /internal/v1/users/{email}/profile
  GET  /internal/v1/users/{email}/holdings
  GET  /internal/v1/users/{email}/summary

Holdings JSON is identical in structure to GET /open/v1/holdings (same serializer).
"""

import datetime
import hmac
import secrets
import time
from collections import defaultdict
from typing import Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models.entities import Holding, User, SecondaryOrder, PrimaryApplication, PriceHistory
from app.services.portfolio_provisioner import provision_user_portfolio
from app.services.serializer import (
    serialize_customer,
    serialize_holdings_list,
    rupees_to_paise,
    rate_to_bps,
    get_current_ist_iso,
)
from app.shared_identity import normalize_email, generate_identity, full_name_from_claims

router = APIRouter(prefix="/internal/v1", tags=["Internal – TradeOne"])

DEMO_EMAILS = {"aarav.mehta@example.com", "priya.nair@example.com"}

# ── Rate limiter (in-process, per IP, 60/min) ────────────────────────────────
_rate_store: dict = defaultdict(list)
_RATE_LIMIT = 60
_RATE_WINDOW = 60  # seconds


def _check_rate_limit(request: Request) -> None:
    if not getattr(settings, "INTERNAL_API_ENABLED", False):
        raise HTTPException(status_code=404, detail="Not found")

    ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    window_start = now - _RATE_WINDOW
    calls = _rate_store[ip] = [t for t in _rate_store[ip] if t > window_start]
    if len(calls) >= _RATE_LIMIT:
        raise HTTPException(
            status_code=429,
            detail="Rate limit exceeded: 60 requests/minute.",
            headers={"Retry-After": "60"},
        )
    _rate_store[ip].append(now)


def _verify_internal_key(x_internal_key: Optional[str] = Header(None)) -> None:
    """Constant-time key comparison. Never logs the key."""
    if not getattr(settings, "INTERNAL_API_ENABLED", False):
        raise HTTPException(status_code=404, detail="Not found")
    if x_internal_key is None:
        raise HTTPException(status_code=401, detail="Missing x-internal-key header.")
    expected = settings.INTERNAL_API_KEY.encode("utf-8")
    provided = x_internal_key.encode("utf-8")
    if not hmac.compare_digest(expected, provided):
        raise HTTPException(status_code=401, detail="Invalid internal API key.")


def _get_or_provision_user(email: str, db: Session, full_name: Optional[str] = None) -> User:
    """Return existing user or provision a new one with 0 holdings and starting funds."""
    norm = normalize_email(email)
    user = db.query(User).filter(User.email == norm).first()
    if not user:
        identity = generate_identity(norm)
        display_name = full_name_from_claims(full_name, norm)
        user = User(
            email=norm,
            full_name=display_name,
            mobile=identity["mobile"],
            customer_id=identity["customer_id"],
            pan=identity["pan"],
            pan_masked=identity["pan_masked"],
            demat_account=identity["demat_account"],
            demat_account_masked=identity["demat_account_masked"],
            bank_name=identity["bank_name"],
            bank_account_masked=identity["bank_account_masked"],
            bank_ifsc=identity["bank_ifsc"],
            wallet_balance=float(getattr(settings, "STARTING_FUNDS", 1_000_000.0)),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        # Assign starter portfolio ONLY if explicitly enabled
        if getattr(settings, "SEED_STARTER_PORTFOLIO", False):
            provision_user_portfolio(db, user)
            db.refresh(user)
    return user


def _has_backing_trade(db: Session, user: User, instrument_id: int) -> bool:
    """Checks if holding is backed by real executed delivery trades or primary allotments."""
    is_demo = user.email in DEMO_EMAILS
    if is_demo and getattr(settings, "DEMO_MODE", True):
        return True

    # Real executed trades only (SETTLED or EXECUTED, not pending limit orders)
    trade = db.query(SecondaryOrder).filter(
        SecondaryOrder.user_id == user.id,
        SecondaryOrder.instrument_id == instrument_id,
        SecondaryOrder.status.in_(["SETTLED", "EXECUTED"]),
    ).first()
    if trade:
        return True

    # Real primary allotment
    allotment = db.query(PrimaryApplication).filter(
        PrimaryApplication.user_id == user.id,
        PrimaryApplication.status == "ALLOTTED",
    ).first()
    return allotment is not None


# ── Dependency that chains both guards ───────────────────────────────────────
def _internal_guard(
    request: Request,
    x_internal_key: Optional[str] = Header(None),
) -> None:
    _check_rate_limit(request)
    _verify_internal_key(x_internal_key)


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/users/provision", dependencies=[Depends(_internal_guard)])
async def provision_user(
    body: dict,
    db: Session = Depends(get_db),
):
    """
    Provision a new user (or return existing). Idempotent.
    New users get profile, shared-identity values, STARTING_FUNDS, and 0 holdings.
    """
    email = body.get("email", "").strip()
    if not email:
        raise HTTPException(status_code=422, detail="email is required.")
    full_name = body.get("fullName") or body.get("full_name")
    user = _get_or_provision_user(email, db, full_name)
    return {
        "status": "ok",
        "provisioned": True,
        "customerId": user.customer_id,
        "email": user.email,
    }


@router.get("/users/{email}/profile", dependencies=[Depends(_internal_guard)])
async def get_user_profile(
    email: str,
    db: Session = Depends(get_db),
):
    """Returns public profile (same shape as GET /open/v1/customer)."""
    user = _get_or_provision_user(email, db)
    return serialize_customer(user)


@router.get("/users/{email}/holdings", dependencies=[Depends(_internal_guard)])
async def get_user_holdings(
    email: str,
    limit: int = 20,
    cursor: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """
    Returns holdings in exactly the same JSON:API structure as GET /open/v1/holdings.
    - Normalizes email (lowercase, trim).
    - Looks up exact user only; never falls back to default/demo user.
    - If user does not exist: 404 USER_NOT_FOUND.
    - If user exists but has no holdings: 200 with data: [] and totals of 0.
    - Delivery holdings only (net of sells, not pending limit orders).
    """
    clean_email = normalize_email(email)
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    # Seeded demo accounts only keep data in demo mode
    if clean_email in DEMO_EMAILS and not getattr(settings, "DEMO_MODE", True):
        return serialize_holdings_list([], limit=limit, cursor=cursor, next_cursor=None)

    raw_holdings = (
        db.query(Holding)
        .filter(Holding.user_id == user.id, Holding.units > 0)
        .order_by(Holding.id)
        .all()
    )

    # Filter to real delivery holdings backed by executed trades / allotments
    real_holdings = [h for h in raw_holdings if _has_backing_trade(db, user, h.instrument_id)]

    if cursor:
        ref = db.query(Holding).filter(Holding.holding_id == cursor).first()
        if ref:
            real_holdings = [h for h in real_holdings if h.id > ref.id]

    next_cursor = None
    if len(real_holdings) > limit:
        next_cursor = real_holdings[limit - 1].holding_id
        page = real_holdings[:limit]
    else:
        page = real_holdings

    return serialize_holdings_list(page, limit=limit, cursor=cursor, next_cursor=next_cursor)


@router.get("/users/{email}/summary", dependencies=[Depends(_internal_guard)])
async def get_user_summary(
    email: str,
    db: Session = Depends(get_db),
):
    """
    Returns portfolio summary: invested, current value, day change, holding count,
    as_of, and timestamp of the last executed trade for that user.
    - Normalizes email; exact lookup only.
    - 404 USER_NOT_FOUND if user does not exist.
    """
    clean_email = normalize_email(email)
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        raise HTTPException(status_code=404, detail="USER_NOT_FOUND")

    # If demo user but demo mode is disabled, no holdings
    if clean_email in DEMO_EMAILS and not getattr(settings, "DEMO_MODE", True):
        real_holdings = []
    else:
        raw_holdings = (
            db.query(Holding)
            .filter(Holding.user_id == user.id, Holding.units > 0)
            .all()
        )
        real_holdings = [h for h in raw_holdings if _has_backing_trade(db, user, h.instrument_id)]

    total_invested_rupees = sum(h.avg_purchase_price * h.units for h in real_holdings)
    total_current_rupees = sum(h.instrument.clean_price * h.units for h in real_holdings)
    total_accrued_rupees = sum(h.instrument.accrued_interest * h.units for h in real_holdings)

    total_invested_paise = rupees_to_paise(total_invested_rupees)
    total_current_paise = rupees_to_paise(total_current_rupees)
    total_accrued_paise = rupees_to_paise(total_accrued_rupees)
    unrealised_pnl_paise = total_current_paise - total_invested_paise

    day_change_rupees = 0.0
    for h in real_holdings:
        ph = (
            db.query(PriceHistory)
            .filter(PriceHistory.instrument_id == h.instrument_id)
            .order_by(PriceHistory.date.desc())
            .first()
        )
        if ph and ph.clean_price:
            day_change_rupees += (h.instrument.clean_price - ph.clean_price) * h.units
    day_change_paise = rupees_to_paise(day_change_rupees)

    # Last executed trade timestamp
    last_order = (
        db.query(SecondaryOrder)
        .filter(
            SecondaryOrder.user_id == user.id,
            SecondaryOrder.status.in_(["SETTLED", "EXECUTED"]),
        )
        .order_by(SecondaryOrder.created_at.desc())
        .first()
    )
    last_trade_at = (
        last_order.created_at.isoformat()
        if (last_order and last_order.created_at)
        else None
    )
    as_of = get_current_ist_iso()

    return {
        "data": {
            "type": "portfolio_summary",
            "id": user.customer_id,
            "attributes": {
                "customerId": user.customer_id,
                "email": user.email,
                "holdingCount": len(real_holdings),
                "invested": round(total_invested_rupees, 2),
                "investedPaise": total_invested_paise,
                "totalInvestedPaise": total_invested_paise,
                "currentValue": round(total_current_rupees, 2),
                "currentValuePaise": total_current_paise,
                "totalCurrentValuePaise": total_current_paise,
                "dayChange": round(day_change_rupees, 2),
                "dayChangePaise": day_change_paise,
                "totalAccruedInterestPaise": total_accrued_paise,
                "unrealisedPnlPaise": unrealised_pnl_paise,
                "walletBalancePaise": rupees_to_paise(user.wallet_balance),
                "asOf": as_of,
                "as_of": as_of,
                "lastTradeAt": last_trade_at,
                "last_trade_at": last_trade_at,
                "providerCode": settings.PROVIDER_CODE,
                "brokerName": settings.BROKER_NAME,
                "dpName": settings.DP_NAME,
                "dpId": settings.DP_ID,
            },
        },
        "meta": {"generatedAt": as_of},
    }
