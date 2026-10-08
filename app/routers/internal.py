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
from app.models.entities import Holding, User
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
    """Return existing user or provision a new one deterministically."""
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
            wallet_balance=1_000_000.0,  # ₹10,00,000
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        # Assign deterministic starter portfolio
        provision_user_portfolio(db, user)
        db.refresh(user)
    return user


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
    Body: {"email": "...", "fullName": "..."}  (fullName optional)
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
    Supports cursor pagination (?limit=N&cursor=holding_id).
    """
    user = _get_or_provision_user(email, db)
    q = db.query(Holding).filter(Holding.user_id == user.id)

    if cursor:
        # cursor is the last holding_id seen; page forward by id
        ref = db.query(Holding).filter(Holding.holding_id == cursor).first()
        if ref:
            q = q.filter(Holding.id > ref.id)

    page = q.order_by(Holding.id).limit(limit + 1).all()

    next_cursor = None
    if len(page) > limit:
        next_cursor = page[limit - 1].holding_id
        page = page[:limit]

    return serialize_holdings_list(page, limit=limit, cursor=cursor, next_cursor=next_cursor)


@router.get("/users/{email}/summary", dependencies=[Depends(_internal_guard)])
async def get_user_summary(
    email: str,
    db: Session = Depends(get_db),
):
    """
    Returns a lightweight portfolio summary (total invested, current value,
    unrealised P&L, wallet balance, holding count).
    """
    user = _get_or_provision_user(email, db)
    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()

    total_invested_paise = 0
    total_current_paise = 0
    total_accrued_paise = 0

    for h in holdings:
        inst = h.instrument
        total_invested_paise += rupees_to_paise(h.avg_purchase_price * h.units)
        total_current_paise += rupees_to_paise(inst.clean_price * h.units)
        total_accrued_paise += rupees_to_paise(inst.accrued_interest * h.units)

    unrealised_pnl_paise = total_current_paise - total_invested_paise

    return {
        "data": {
            "type": "portfolio_summary",
            "id": user.customer_id,
            "attributes": {
                "customerId": user.customer_id,
                "email": user.email,
                "holdingCount": len(holdings),
                "totalInvestedPaise": total_invested_paise,
                "totalCurrentValuePaise": total_current_paise,
                "totalAccruedInterestPaise": total_accrued_paise,
                "unrealisedPnlPaise": unrealised_pnl_paise,
                "walletBalancePaise": rupees_to_paise(user.wallet_balance),
                "providerCode": settings.PROVIDER_CODE,
                "brokerName": settings.BROKER_NAME,
                "dpName": settings.DP_NAME,
                "dpId": settings.DP_ID,
            },
        },
        "meta": {"generatedAt": get_current_ist_iso()},
    }
