"""
Deterministic starter portfolio assignment for new (non-demo) BondBazaar users.

Provider C theme: Government Securities (G-Secs) + Sovereign Gold Bonds (SGBs),
with 2-3 intentional ISINs that overlap with other providers so TradeOne can
demonstrate cross-broker position merging.

Rules:
- Same normalised email → same ISINs, units, and avg_purchase_price after any DB reset.
- Average price within ±15 % of current clean price.
- Starting wallet = ₹10,00,000.
- Existing seeded demo users are never touched here.
"""

import hashlib
import hmac
import json
import os
import uuid
from typing import List, Optional
from datetime import date

from sqlalchemy.orm import Session

from app.config import settings
from app.models.entities import Instrument, Holding, User, SecondaryOrder
from app.shared_identity import normalize_email

# ── OVERLAPPING ISINs (must match ISINs used in other providers) ──────────────
# These two G-Sec ISINs are deliberately chosen to appear in at least one other
# provider so TradeOne sees a cross-broker duplicate for the same user.
OVERLAP_ISINS = [
    "IN0002000017",  # 7.18% GS 2033   — shared with providers A/B
    "IN0002000024",  # 7.10% GS 2034   — shared with provider B
    "IN0008000146",  # SGB 2021-22 Series VIII — shared with provider A/B
]

# Provider-C portfolio recipe: types to prefer, in priority order.
PREFERRED_TYPES = ["GSEC", "SGB", "TAX_FREE_BOND", "CORPORATE_BOND"]

# Demo emails that already have a hand-crafted seed — never auto-provision them.
DEMO_EMAILS = {"aarav.mehta@example.com", "priya.nair@example.com"}


# ── Determinism helpers ───────────────────────────────────────────────────────

def _seed_int(email: str, tag: str) -> int:
    """Stable integer derived from email + tag via HMAC-SHA256."""
    salt = getattr(settings, "SHARED_IDENTITY_SALT", "tradeone-shared-identity-salt")
    mac = hmac.new(
        salt.encode(),
        f"{tag}:{normalize_email(email)}".encode(),
        hashlib.sha256,
    )
    return int(mac.hexdigest(), 16)


def _stable_price(current_price: float, email: str, isin: str) -> float:
    """Returns a deterministic avg purchase price within ±15 % of current price."""
    seed = _seed_int(email, f"price:{isin}") % 10000  # 0-9999
    pct = (seed / 9999.0) * 0.30 - 0.15              # -0.15 … +0.15
    raw = current_price * (1.0 + pct)
    return round(raw, 2)


def _stable_units(email: str, isin: str, min_lot: int) -> int:
    """Returns a deterministic number of lots (1-5 lots)."""
    seed = _seed_int(email, f"units:{isin}") % 5     # 0-4
    lots = seed + 1                                    # 1-5 lots
    return lots * min_lot


# ── Public API ────────────────────────────────────────────────────────────────

def load_shared_instruments(db: Session) -> List[Instrument]:
    """Returns all active instruments from the DB, G-Secs and SGBs first."""
    all_insts = db.query(Instrument).filter(Instrument.is_active == True).all()
    # Sort: preferred types first, then everything else alphabetically
    def sort_key(i: Instrument):
        try:
            idx = PREFERRED_TYPES.index(i.instrument_type)
        except ValueError:
            idx = len(PREFERRED_TYPES)
        return (idx, i.isin)
    return sorted(all_insts, key=sort_key)


def provision_user_portfolio(db: Session, user: User) -> List[Holding]:
    """
    Creates a deterministic starter portfolio for `user` if they have no holdings yet.
    Only creates holdings if SEED_STARTER_PORTFOLIO is True.
    Does nothing if holdings already exist or SEED_STARTER_PORTFOLIO is False.
    Returns the list of created Holding objects.
    """
    if not getattr(settings, "SEED_STARTER_PORTFOLIO", False):
        return []

    email = normalize_email(user.email)
    if email in DEMO_EMAILS:
        return []

    existing = db.query(Holding).filter(Holding.user_id == user.id).count()
    if existing > 0:
        return []

    instruments = load_shared_instruments(db)
    if not instruments:
        return []

    # 1. Always include overlap ISINs (if available in this DB)
    overlap_map = {i.isin: i for i in instruments if i.isin in OVERLAP_ISINS}
    chosen: List[Instrument] = list(overlap_map.values())

    # 2. Fill up to 5 instruments from preferred types (no duplicates)
    chosen_isins = {i.isin for i in chosen}
    for inst in instruments:
        if len(chosen) >= 5:
            break
        if inst.isin not in chosen_isins:
            chosen.append(inst)
            chosen_isins.add(inst.isin)

    created: List[Holding] = []
    for inst in chosen:
        units = _stable_units(email, inst.isin, max(1, inst.min_lot_size))
        avg_price = _stable_price(
            inst.clean_price if inst.clean_price else inst.face_value,
            email,
            inst.isin,
        )
        holding_id = f"hld_{uuid.uuid4().hex[:8]}"
        h = Holding(
            holding_id=holding_id,
            user_id=user.id,
            instrument_id=inst.id,
            units=units,
            avg_purchase_price=avg_price,
            avg_purchase_yield=round(inst.current_ytm * 1.0, 4),
        )
        db.add(h)
        created.append(h)

        # Backing settled order so TradeOne delivery audit recognizes the holding
        order = SecondaryOrder(
            order_id=f"ord_p{uuid.uuid4().hex[:8]}",
            user_id=user.id,
            instrument_id=inst.id,
            side="BUY",
            order_type="MARKET",
            units=units,
            execution_price=avg_price,
            accrued_interest_per_unit=inst.accrued_interest or 0.0,
            stamp_duty=round(avg_price * units * 0.00015, 2),
            exchange_charges=25.0,
            total_consideration=round((avg_price + (inst.accrued_interest or 0.0)) * units + 25.0, 2),
            settlement_date=date.today(),
            status="SETTLED"
        )
        db.add(order)

    # Starting wallet
    if user.wallet_balance < 1_000_000:
        user.wallet_balance = 1_000_000.0  # ₹10,00,000

    db.commit()
    return created
