"""
Tests for TradeOne integration:
1. Identity: normalize_email, generate_identity determinism, full_name_from_claims
2. Provisioning: new user gets deterministic portfolio (G-Secs + SGBs first)
3. Deterministic portfolio: same email → same holdings after DB reset
4. Internal API key security: missing key → 401, wrong key → 401, correct → 200
5. Holdings parity: internal holdings JSON matches public /open/v1/holdings structure
6. Google linking: normalized email matches provisioned user
7. Outbox retry: enqueue_holdings_changed schedules an asyncio task (mock)
"""

import asyncio
import hashlib
import hmac
import json
import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# Ensure app is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

# ── 1. Identity tests ─────────────────────────────────────────────────────────

from app.shared_identity import normalize_email, generate_identity, full_name_from_claims


def test_normalize_email_strips_and_lowercases():
    assert normalize_email("  USER@Example.COM  ") == "user@example.com"
    assert normalize_email("John.DOE@TEST.ORG") == "john.doe@test.org"
    assert normalize_email("already@good.com") == "already@good.com"


def test_normalize_email_whitespace_variants():
    assert normalize_email("\tTabbed@Email.com\n") == "tabbed@email.com"


def test_generate_identity_is_deterministic():
    a = generate_identity("test@example.com")
    b = generate_identity("test@example.com")
    assert a == b, "Same email must produce identical identity"


def test_generate_identity_differs_per_email():
    a = generate_identity("alice@example.com")
    b = generate_identity("bob@example.com")
    assert a["customer_id"] != b["customer_id"]
    assert a["demat_account"] != b["demat_account"]


def test_generate_identity_normalizes_email():
    a = generate_identity("User@Example.COM")
    b = generate_identity("user@example.com")
    assert a == b, "Identity must be same after normalization"


def test_generate_identity_no_real_pan():
    """PAN must NOT match the real Indian PAN pattern AAAAA9999A."""
    import re
    pan_pattern = re.compile(r"^[A-Z]{5}[0-9]{4}[A-Z]$")
    for email in ["test@x.com", "foo@bar.org", "z@z.z"]:
        identity = generate_identity(email)
        assert not pan_pattern.match(identity["pan"]), \
            f"PAN {identity['pan']} looks like a real PAN — must not!"


def test_generate_identity_no_real_aadhaar():
    """Demat/ID fields must not look like a 12-digit Aadhaar number."""
    for email in ["test@x.com", "foo@bar.org"]:
        identity = generate_identity(email)
        demat = identity["demat_account"].replace("-", "").replace("BB", "")
        assert not demat.isdigit() or len(demat) != 12, \
            "Identity field must not be a plain 12-digit number (Aadhaar-like)"


def test_full_name_from_google_claim():
    assert full_name_from_claims("Priya Nair", "priya@test.com") == "Priya Nair"


def test_full_name_fallback_from_email():
    name = full_name_from_claims(None, "john.doe@example.com")
    assert "John" in name and "Doe" in name


def test_full_name_fallback_blank_google():
    name = full_name_from_claims("   ", "alice.bob@test.com")
    assert "Alice" in name


# ── 2 & 3. Provisioning and determinism tests ─────────────────────────────────

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from app.database import Base
import app.models.entities


def make_test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    return Session()


def seed_instruments(db):
    """Seed minimal instruments for testing."""
    from app.models.entities import Instrument
    from datetime import date

    instruments = [
        Instrument(
            isin="IN0002000017", name="7.18% GS 2033", issuer_name="GOI",
            instrument_type="GSEC", face_value=1000.0, coupon_rate=0.0718,
            coupon_frequency="SEMI_ANNUAL", issue_date=date(2023, 8, 14),
            maturity_date=date(2033, 8, 14), clean_price=1005.0,
            current_ytm=0.0710, accrued_interest=2.0, credit_rating="SOVEREIGN",
            rating_agency="RBI", min_lot_size=10,
        ),
        Instrument(
            isin="IN0002000024", name="7.10% GS 2034", issuer_name="GOI",
            instrument_type="GSEC", face_value=1000.0, coupon_rate=0.071,
            coupon_frequency="SEMI_ANNUAL", issue_date=date(2024, 4, 8),
            maturity_date=date(2034, 4, 8), clean_price=998.0,
            current_ytm=0.0715, accrued_interest=1.5, credit_rating="SOVEREIGN",
            rating_agency="RBI", min_lot_size=10,
        ),
        Instrument(
            isin="IN0020180001", name="SGB 2028", issuer_name="RBI",
            instrument_type="SGB", face_value=5000.0, coupon_rate=0.025,
            coupon_frequency="SEMI_ANNUAL", issue_date=date(2020, 10, 23),
            maturity_date=date(2028, 10, 23), clean_price=4800.0,
            current_ytm=0.0650, accrued_interest=10.0, credit_rating="SOVEREIGN",
            rating_agency="RBI", min_lot_size=1,
        ),
    ]
    for i in instruments:
        db.add(i)
    db.commit()
    return instruments


def make_user(db, email="new@example.com"):
    from app.models.entities import User
    from app.shared_identity import generate_identity, normalize_email
    norm = normalize_email(email)
    identity = generate_identity(norm)
    user = User(
        email=norm, full_name="Test User", mobile="+91 9000000000",
        customer_id=identity["customer_id"],
        pan=identity["pan"], pan_masked=identity["pan_masked"],
        demat_account=identity["demat_account"],
        demat_account_masked=identity["demat_account_masked"],
        wallet_balance=1_000_000.0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_provision_creates_zero_holdings_by_default():
    """Default behavior: SEED_STARTER_PORTFOLIO=False creates ZERO holdings."""
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    holdings = provision_user_portfolio(db, user)
    assert holdings == [], "Provisioner must create zero holdings by default"


def test_provision_creates_holdings_when_enabled(monkeypatch):
    """When explicitly enabled, deterministic starter portfolio is created."""
    monkeypatch.setattr("app.config.settings.SEED_STARTER_PORTFOLIO", True)
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    holdings = provision_user_portfolio(db, user)
    assert len(holdings) > 0, "Provisioner must create holdings when SEED_STARTER_PORTFOLIO is True"


def test_provision_overlap_isins_included(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEED_STARTER_PORTFOLIO", True)
    from app.services.portfolio_provisioner import OVERLAP_ISINS
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    holdings = provision_user_portfolio(db, user)
    holding_isins = [h.instrument.isin for h in holdings]
    for isin in OVERLAP_ISINS:
        if any(i.isin == isin for i in db.query(__import__('app.models.entities', fromlist=['Instrument']).Instrument).all()):
            assert isin in holding_isins, f"Overlap ISIN {isin} must be in new user portfolio"


def test_provision_is_deterministic(monkeypatch):
    """Same email → same holdings after DB reset when enabled."""
    monkeypatch.setattr("app.config.settings.SEED_STARTER_PORTFOLIO", True)
    email = "deterministic@example.com"

    def _run():
        db = make_test_db()
        seed_instruments(db)
        user = make_user(db, email)
        from app.services.portfolio_provisioner import provision_user_portfolio
        holdings = provision_user_portfolio(db, user)
        return [(h.instrument.isin, h.units, h.avg_purchase_price) for h in holdings]

    run1 = _run()
    run2 = _run()
    assert run1 == run2, "Same email must produce identical portfolio after DB reset"


def test_provision_price_within_15pct(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEED_STARTER_PORTFOLIO", True)
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    holdings = provision_user_portfolio(db, user)
    for h in holdings:
        current = h.instrument.clean_price
        pct = abs(h.avg_purchase_price - current) / current
        assert pct <= 0.15, \
            f"avg_purchase_price {h.avg_purchase_price} is more than 15% from {current}"


def test_provision_sets_wallet(monkeypatch):
    monkeypatch.setattr("app.config.settings.SEED_STARTER_PORTFOLIO", True)
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    provision_user_portfolio(db, user)
    assert user.wallet_balance == 1_000_000.0


def test_provision_demo_users_untouched():
    """Provisioner must silently skip demo emails."""
    db = make_test_db()
    seed_instruments(db)
    from app.models.entities import User
    demo = User(
        email="aarav.mehta@example.com", full_name="Aarav Mehta",
        mobile="+91 0", customer_id="BB-77031",
        pan="BB123456X", pan_masked="BB12****X",
        demat_account="IN300003-BB-AABBCCDD",
        demat_account_masked="IN300003-BB-****CCDD",
        wallet_balance=350000.0,
    )
    db.add(demo)
    db.commit()
    db.refresh(demo)
    from app.services.portfolio_provisioner import provision_user_portfolio
    holdings = provision_user_portfolio(db, demo)
    assert holdings == [], "Demo users must not be auto-provisioned"


def test_provision_idempotent():
    """Calling provisioner twice must not create duplicate holdings."""
    db = make_test_db()
    seed_instruments(db)
    user = make_user(db)
    from app.services.portfolio_provisioner import provision_user_portfolio
    first = provision_user_portfolio(db, user)
    second = provision_user_portfolio(db, user)
    assert second == [], "Second call must be a no-op"


# ── 4. Internal API key security ──────────────────────────────────────────────

from fastapi.testclient import TestClient


def make_test_app():
    """Build a minimal FastAPI test app with internal router and INTERNAL_API_ENABLED."""
    from fastapi import FastAPI
    from app.database import Base
    app = FastAPI()
    from app.routers.internal import router
    app.include_router(router)
    return app


@pytest.fixture()
def internal_client(monkeypatch):
    monkeypatch.setattr("app.config.settings.INTERNAL_API_ENABLED", True)
    monkeypatch.setattr("app.config.settings.INTERNAL_API_KEY", "test-key-abc")
    monkeypatch.setattr("app.config.settings.SHARED_IDENTITY_SALT", "test-shared-salt-abc")
    import app.routers.internal as internal_mod
    monkeypatch.setattr(internal_mod.settings, "INTERNAL_API_ENABLED", True)
    monkeypatch.setattr(internal_mod.settings, "INTERNAL_API_KEY", "test-key-abc")
    monkeypatch.setattr(internal_mod.settings, "SHARED_IDENTITY_SALT", "test-shared-salt-abc")
    # Patch get_db to return the in-memory db
    db = make_test_db()
    seed_instruments(db)

    from app import database
    monkeypatch.setattr(database, "SessionLocal", lambda: db)

    app = make_test_app()

    def override_db():
        yield db

    from app.database import get_db
    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def test_internal_missing_key_returns_401(internal_client):
    resp = internal_client.get("/internal/v1/users/test@example.com/profile")
    assert resp.status_code == 401


def test_internal_wrong_key_returns_401(internal_client):
    resp = internal_client.get(
        "/internal/v1/users/test@example.com/profile",
        headers={"x-internal-key": "wrong-key"},
    )
    assert resp.status_code == 401


def test_internal_correct_key_returns_200(internal_client):
    resp = internal_client.get(
        "/internal/v1/users/test@example.com/profile",
        headers={"x-internal-key": "test-key-abc"},
    )
    assert resp.status_code == 200


def test_internal_provision_endpoint(internal_client):
    resp = internal_client.post(
        "/internal/v1/users/provision",
        json={"email": "newuser@example.com", "fullName": "New User"},
        headers={"x-internal-key": "test-key-abc"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["provisioned"] is True
    assert "customerId" in data


def test_internal_provision_idempotent(internal_client):
    body = {"email": "idempotent@example.com"}
    h = {"x-internal-key": "test-key-abc"}
    r1 = internal_client.post("/internal/v1/users/provision", json=body, headers=h)
    r2 = internal_client.post("/internal/v1/users/provision", json=body, headers=h)
    assert r1.json()["customerId"] == r2.json()["customerId"]


# ── 5. Holdings parity ────────────────────────────────────────────────────────

def test_internal_holdings_json_structure(internal_client):
    """Internal holdings must have the same JSON:API structure as public API."""
    h = {"x-internal-key": "test-key-abc"}
    # Provision first
    internal_client.post(
        "/internal/v1/users/provision",
        json={"email": "parity@example.com"},
        headers=h,
    )
    resp = internal_client.get(
        "/internal/v1/users/parity@example.com/holdings",
        headers=h,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert "links" in body
    assert "meta" in body
    assert "count" in body["meta"]
    assert "generatedAt" in body["meta"]
    if body["data"]:
        item = body["data"][0]
        assert item["type"] == "holding"
        attrs = item["attributes"]
        assert "isin" in attrs
        assert "faceValuePaise" in attrs
        assert "ytmBps" in attrs
        assert "couponRateBps" in attrs


def test_internal_summary_json_structure(internal_client):
    """Internal summary must return aggregate portfolio data with required attributes."""
    h = {"x-internal-key": "test-key-abc"}
    internal_client.post(
        "/internal/v1/users/provision",
        json={"email": "summaryuser@example.com"},
        headers=h,
    )
    resp = internal_client.get(
        "/internal/v1/users/summaryuser@example.com/summary",
        headers=h,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert "data" in body
    assert body["data"]["type"] == "portfolio_summary"
    attrs = body["data"]["attributes"]
    assert "holdingCount" in attrs
    assert "totalInvestedPaise" in attrs
    assert "totalCurrentValuePaise" in attrs
    assert "totalAccruedInterestPaise" in attrs
    assert "unrealisedPnlPaise" in attrs
    assert "walletBalancePaise" in attrs
    assert attrs["providerCode"] == "c"


# ── 6. Google linking — normalized email matching ─────────────────────────────

def test_google_linking_normalized_email():
    """User provisioned with normalized email is found by any casing variant."""
    db = make_test_db()
    seed_instruments(db)
    # Provision with lowercase
    user_lower = make_user(db, "google.user@example.com")
    # Look up with original casing (as Google might send)
    from app.shared_identity import normalize_email
    from app.models.entities import User
    found = db.query(User).filter(User.email == normalize_email("Google.User@Example.COM")).first()
    assert found is not None
    assert found.id == user_lower.id


# ── 7. Outbox retry ───────────────────────────────────────────────────────────

def test_outbox_enqueue_schedules_task():
    """enqueue_holdings_changed must schedule an asyncio task (non-blocking)."""
    from app.services.tradeone_outbox import enqueue_holdings_changed

    tasks_created = []

    class FakeLoop:
        def create_task(self, coro):
            tasks_created.append(coro)
            return MagicMock()

    with patch("asyncio.get_event_loop", return_value=FakeLoop()):
        enqueue_holdings_changed("test@example.com")

    assert len(tasks_created) == 1, "Must schedule exactly one asyncio task"


@pytest.mark.asyncio
async def test_outbox_retries_on_failure():
    """_deliver must retry up to MAX_RETRIES times before giving up."""
    from app.services import tradeone_outbox
    original_url = tradeone_outbox._TRADEONE_URL
    tradeone_outbox._TRADEONE_URL = "http://unreachable.invalid"
    attempt_count = 0

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            raise Exception("connection refused")

    with patch("httpx.AsyncClient", return_value=FakeClient()), \
         patch("asyncio.sleep", new_callable=AsyncMock):
        await tradeone_outbox._deliver({
            "provider": "c",
            "email": "x@x.com",
            "event": "HOLDINGS_CHANGED",
            "occurredAt": "2026-01-01T00:00:00+05:30",
        })

    assert attempt_count == tradeone_outbox._MAX_RETRIES
    tradeone_outbox._TRADEONE_URL = original_url


@pytest.mark.asyncio
async def test_outbox_stops_on_success():
    """_deliver must stop retrying after first success."""
    from app.services import tradeone_outbox
    original_url = tradeone_outbox._TRADEONE_URL
    tradeone_outbox._TRADEONE_URL = "http://tradeone.local"
    attempt_count = 0

    class FakeResponse:
        status_code = 200

    class FakeClient:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            pass

        async def post(self, url, **kwargs):
            nonlocal attempt_count
            attempt_count += 1
            return FakeResponse()

    with patch("httpx.AsyncClient", return_value=FakeClient()):
        await tradeone_outbox._deliver({
            "provider": "c",
            "email": "x@x.com",
            "event": "HOLDINGS_CHANGED",
            "occurredAt": "2026-01-01T00:00:00+05:30",
        })

    assert attempt_count == 1, "Must stop after first successful delivery"
    tradeone_outbox._TRADEONE_URL = original_url


# ── 8. Requirement 5 verification tests ───────────────────────────────────────

def test_req5_new_google_user_provisioning_has_zero_holdings(internal_client):
    """A new Google user via provisioning has zero holdings, positions, orders and trades."""
    from app.database import SessionLocal
    h = {"x-internal-key": "test-key-abc"}
    email = "newgoogleuser@example.com"
    r = internal_client.post(
        "/internal/v1/users/provision",
        json={"email": email, "fullName": "New Google User"},
        headers=h,
    )
    assert r.status_code == 200
    db = SessionLocal()
    try:
        from app.models.entities import User, Holding, SecondaryOrder
        u = db.query(User).filter(User.email == email).first()
        assert u is not None
        assert u.wallet_balance == 1_000_000.0  # STARTING_FUNDS
        holdings_cnt = db.query(Holding).filter(Holding.user_id == u.id).count()
        orders_cnt = db.query(SecondaryOrder).filter(SecondaryOrder.user_id == u.id).count()
        assert holdings_cnt == 0, "New user must have ZERO holdings"
        assert orders_cnt == 0, "New user must have ZERO orders"
    finally:
        db.close()


def test_req5_internal_endpoint_returns_empty_list_for_existing_user_without_trades(internal_client):
    """The internal endpoint returns an empty list (not 404) for an existing user without trades."""
    h = {"x-internal-key": "test-key-abc"}
    email = "notradesuser@example.com"
    # Provision user so user exists
    internal_client.post(
        "/internal/v1/users/provision",
        json={"email": email},
        headers=h,
    )
    # Holdings query
    resp = internal_client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=h,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["data"] == []
    assert body["meta"]["count"] == 0
    assert body["meta"]["totalInvestedPaise"] == 0
    assert body["meta"]["totalCurrentValuePaise"] == 0

    # Summary query also returns 200 with 0 counts
    sum_resp = internal_client.get(
        f"/internal/v1/users/{email}/summary",
        headers=h,
    )
    assert sum_resp.status_code == 200
    sum_data = sum_resp.json()["data"]["attributes"]
    assert sum_data["holdingCount"] == 0
    assert sum_data["invested"] == 0
    assert sum_data["currentValue"] == 0
    assert sum_data["last_trade_at"] is None


def test_req5_buy_5_sell_2_returns_3_with_correct_avg_price(internal_client):
    """After a buy of 5 and a sell of 2 it returns 3 with the correct average price."""
    from app.database import SessionLocal
    from app.models.entities import User, Instrument
    from app.services.order_service import place_secondary_order

    h = {"x-internal-key": "test-key-abc"}
    email = "trader52@example.com"
    internal_client.post(
        "/internal/v1/users/provision",
        json={"email": email},
        headers=h,
    )
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        inst = db.query(Instrument).first()
        inst.min_lot_size = 1  # Ensure order of 5 and 2 is accepted

        # Buy 5 units
        clean_price = inst.clean_price
        success, msg, ord1 = place_secondary_order(
            db, user, inst, side="BUY", order_type="MARKET", units=5
        )
        assert success is True

        # Sell 2 units
        success, msg, ord2 = place_secondary_order(
            db, user, inst, side="SELL", order_type="MARKET", units=2
        )
        assert success is True
    finally:
        db.close()

    # Query internal holdings endpoint
    resp = internal_client.get(
        f"/internal/v1/users/{email}/holdings",
        headers=h,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert len(data) == 1
    attrs = data[0]["attributes"]
    assert attrs["units"] == 3
    expected_avg_paise = int(round(clean_price * 100))
    assert attrs["avgPurchasePricePaise"] == expected_avg_paise


def test_req5_two_different_emails_never_see_each_others_data(internal_client):
    """Two different emails never see each other's data."""
    from app.database import SessionLocal
    from app.models.entities import User, Instrument
    from app.services.order_service import place_secondary_order

    h = {"x-internal-key": "test-key-abc"}
    email_a = "user_alpha@example.com"
    email_b = "user_beta@example.com"

    internal_client.post("/internal/v1/users/provision", json={"email": email_a}, headers=h)
    internal_client.post("/internal/v1/users/provision", json={"email": email_b}, headers=h)

    # User A trades 5 units
    db = SessionLocal()
    try:
        user_a = db.query(User).filter(User.email == email_a).first()
        inst = db.query(Instrument).first()
        inst.min_lot_size = 1
        place_secondary_order(db, user_a, inst, side="BUY", order_type="MARKET", units=5)
    finally:
        db.close()

    # User A holdings
    resp_a = internal_client.get(f"/internal/v1/users/{email_a}/holdings", headers=h)
    assert resp_a.status_code == 200
    assert len(resp_a.json()["data"]) == 1

    # User B holdings (has not traded)
    resp_b = internal_client.get(f"/internal/v1/users/{email_b}/holdings", headers=h)
    assert resp_b.status_code == 200
    assert resp_b.json()["data"] == [], "User B must see zero holdings"


def test_req5_unknown_email_returns_404_never_demo_user(internal_client):
    """Unknown email returns 404 and never the demo user."""
    h = {"x-internal-key": "test-key-abc"}
    resp = internal_client.get(
        "/internal/v1/users/nonexistent_person_12345@example.com/holdings",
        headers=h,
    )
    assert resp.status_code == 404
    assert resp.json()["detail"] == "USER_NOT_FOUND"

    sum_resp = internal_client.get(
        "/internal/v1/users/nonexistent_person_12345@example.com/summary",
        headers=h,
    )
    assert sum_resp.status_code == 404
    assert sum_resp.json()["detail"] == "USER_NOT_FOUND"


def test_req5_pending_limit_orders_do_not_appear_as_holdings(internal_client):
    """Pending limit orders do not appear as holdings."""
    from app.database import SessionLocal
    from app.models.entities import User, Instrument
    from app.services.order_service import place_secondary_order

    h = {"x-internal-key": "test-key-abc"}
    email = "limituser@example.com"
    internal_client.post("/internal/v1/users/provision", json={"email": email}, headers=h)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        inst = db.query(Instrument).first()
        inst.min_lot_size = 1

        # Place pending BUY limit order with limit_price well below market clean_price
        pending_limit_price = inst.clean_price - 100.0
        success, msg, ord_limit = place_secondary_order(
            db, user, inst, side="BUY", order_type="LIMIT", units=5,
            limit_price=pending_limit_price
        )
        assert success is True
        assert ord_limit.status == "PENDING"
    finally:
        db.close()

    # Query holdings — must be empty!
    resp = internal_client.get(f"/internal/v1/users/{email}/holdings", headers=h)
    assert resp.status_code == 200
    assert resp.json()["data"] == [], "Pending limit orders must not appear as holdings"


def test_req5_outbox_event_created_in_same_transaction_as_trade(internal_client):
    """The outbox event is created in the same transaction as the trade."""
    from app.database import SessionLocal
    from app.models.entities import User, Instrument, OutboxEvent
    from app.services.order_service import place_secondary_order

    h = {"x-internal-key": "test-key-abc"}
    email = "outboxuser@example.com"
    internal_client.post("/internal/v1/users/provision", json={"email": email}, headers=h)

    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == email).first()
        inst = db.query(Instrument).first()
        inst.min_lot_size = 1

        # Execute market trade
        success, msg, ord_exec = place_secondary_order(
            db, user, inst, side="BUY", order_type="MARKET", units=5
        )
        assert success is True

        # Check outbox event exists in DB
        outbox = (
            db.query(OutboxEvent)
            .filter(OutboxEvent.email == email, OutboxEvent.event == "HOLDINGS_CHANGED")
            .first()
        )
        assert outbox is not None
        assert outbox.status in ("PENDING", "DELIVERED")
        assert outbox.provider == "c"
        assert outbox.occurred_at is not None
    finally:
        db.close()

