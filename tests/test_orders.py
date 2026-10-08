import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from datetime import date, timedelta

from app.database import Base
from app.models.entities import User, Instrument, Holding, SecondaryOrder, SystemState
from app.services.order_service import place_secondary_order

@pytest.fixture
def test_db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    # Create system state
    session.add(SystemState(benchmark_yield=0.0708, is_market_open=True))

    # Create user
    user = User(
        email="test@example.com",
        full_name="Test Investor",
        mobile="+91 99999 88888",
        customer_id="BB-11223",
        pan="ABCDE1111F",
        pan_masked="ABCDE****F",
        demat_account="1208160011112222",
        demat_account_masked="12081600****2222",
        wallet_balance=50000.0
    )
    session.add(user)

    # Create instrument
    inst = Instrument(
        isin="INE991A00101",
        name="Test Infra Finance 8.50% 2030",
        issuer_name="Test Infra Ltd",
        instrument_type="CORPORATE_BOND",
        face_value=1000.0,
        coupon_rate=0.0850,
        coupon_frequency="ANNUAL",
        issue_date=date(2022, 1, 1),
        maturity_date=date(2030, 1, 1),
        credit_rating="AAA",
        min_lot_size=5,
        clean_price=1020.0,
        dirty_price=1040.0,
        accrued_interest=20.0,
        current_ytm=0.0815,
        modified_duration=4.2,
        is_active=True
    )
    session.add(inst)
    session.commit()

    yield session
    session.close()

def test_order_minimum_lot_enforcement(test_db):
    user = test_db.query(User).first()
    inst = test_db.query(Instrument).first()

    # Instrument min_lot_size is 5. Ordering 3 units should fail.
    success, msg, order = place_secondary_order(
        test_db, user, inst, side="BUY", order_type="MARKET", units=3
    )
    assert success is False
    assert "Minimum order lot size is 5" in msg
    assert order is None

def test_order_insufficient_funds(test_db):
    user = test_db.query(User).first()
    inst = test_db.query(Instrument).first()
    user.wallet_balance = 1000.0 # Insufficient for 5 units (~₹5200)

    success, msg, order = place_secondary_order(
        test_db, user, inst, side="BUY", order_type="MARKET", units=5
    )
    assert success is False
    assert "Insufficient funds" in msg
    assert order is None

def test_order_buy_settlement(test_db):
    user = test_db.query(User).first()
    inst = test_db.query(Instrument).first()
    user.wallet_balance = 50000.0

    success, msg, order = place_secondary_order(
        test_db, user, inst, side="BUY", order_type="MARKET", units=10
    )
    assert success is True
    assert order is not None
    assert order.status == "SETTLED"
    assert order.units == 10
    assert order.settlement_date == date.today() + timedelta(days=1)

    # Check holding was created
    holding = test_db.query(Holding).filter(Holding.user_id == user.id).first()
    assert holding is not None
    assert holding.units == 10

    # Wallet balance should be reduced
    assert user.wallet_balance < 50000.0

def test_order_sell_settlement(test_db):
    user = test_db.query(User).first()
    inst = test_db.query(Instrument).first()

    # First add a holding of 10 units
    holding = Holding(
        holding_id="hld_test",
        user_id=user.id,
        instrument_id=inst.id,
        units=10,
        avg_purchase_price=1000.0,
        avg_purchase_yield=0.08
    )
    test_db.add(holding)
    test_db.commit()

    initial_bal = user.wallet_balance

    # Sell 5 units
    success, msg, order = place_secondary_order(
        test_db, user, inst, side="SELL", order_type="MARKET", units=5
    )
    assert success is True
    assert order.status == "SETTLED"
    assert holding.units == 5
    assert user.wallet_balance > initial_bal
