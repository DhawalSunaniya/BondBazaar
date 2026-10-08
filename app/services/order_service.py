import uuid
import datetime
from datetime import date, timedelta
from typing import Optional, Dict, Any, Tuple
from sqlalchemy.orm import Session

from app.models.entities import (
    User, Instrument, Holding, SecondaryOrder, WalletTransaction, PrimaryIssue, PrimaryApplication
)
from app.services.bond_math import calculate_bond_metrics, calculate_accrued_interest, calculate_dirty_price_from_ytm
from app.services.webhook_service import trigger_holdings_updated_event
from app.services.tradeone_outbox import enqueue_holdings_changed

def get_simulated_order_book(inst: Instrument) -> Dict[str, Any]:
    """Generates a realistic 5-level order book for secondary trading."""
    base_price = inst.clean_price
    bids = []
    asks = []

    # 5 levels of bids below base price
    for i in range(1, 6):
        price = round(base_price - (i * 0.15), 2)
        qty = inst.min_lot_size * (15 - i * 2)
        bids.append({"level": i, "price": price, "qty": qty, "yield": round(inst.current_ytm + (i * 0.0003), 4)})

    # 5 levels of asks above base price
    for i in range(1, 6):
        price = round(base_price + (i * 0.15), 2)
        qty = inst.min_lot_size * (12 - i * 2)
        asks.append({"level": i, "price": price, "qty": qty, "yield": round(max(0.01, inst.current_ytm - (i * 0.0003)), 4)})

    return {"bids": bids, "asks": asks}

def place_secondary_order(
    db: Session,
    user: User,
    instrument: Instrument,
    side: str, # BUY or SELL
    order_type: str, # MARKET or LIMIT
    units: int,
    limit_price: Optional[float] = None,
    limit_yield: Optional[float] = None,
    background_tasks = None
) -> Tuple[bool, str, Optional[SecondaryOrder]]:
    """
    Enforces minimum lot size, funds check, holdings check, and settles market orders.
    """
    side = side.upper()
    order_type = order_type.upper()

    if units < instrument.min_lot_size:
        return False, f"Minimum order lot size is {instrument.min_lot_size} units.", None

    if units % instrument.min_lot_size != 0:
        return False, f"Order quantity must be a multiple of lot size ({instrument.min_lot_size}).", None

    today = date.today()
    settlement_date = today + timedelta(days=1) # T+1

    # Determine execution clean price
    if order_type == "LIMIT":
        if limit_yield is not None and limit_yield > 0:
            # Derive clean price from limit yield
            dirty = calculate_dirty_price_from_ytm(
                face_value=instrument.face_value,
                coupon_rate=instrument.coupon_rate,
                frequency=instrument.coupon_frequency,
                issue_date=instrument.issue_date,
                maturity_date=instrument.maturity_date,
                settlement_date=settlement_date,
                ytm=limit_yield
            )
            exec_clean_price = max(0.01, round(dirty - instrument.accrued_interest, 2))
        elif limit_price is not None and limit_price > 0:
            exec_clean_price = round(limit_price, 2)
        else:
            return False, "Limit order requires either limit price or limit yield.", None
    else:
        exec_clean_price = instrument.clean_price

    accrued_per_unit = instrument.accrued_interest
    principal_clean = round(exec_clean_price * units, 2)
    accrued_total = round(accrued_per_unit * units, 2)
    stamp_duty = round(principal_clean * 0.00015, 2) # 0.015%
    exchange_charges = 25.0
    total_consideration = round(principal_clean + accrued_total + stamp_duty + exchange_charges, 2)

    order_id = f"ord_{uuid.uuid4().hex[:8]}"

    if side == "BUY":
        # Check wallet balance
        if user.wallet_balance < total_consideration:
            return False, f"Insufficient funds. Required: ₹{total_consideration:,.2f}, Available: ₹{user.wallet_balance:,.2f}.", None

        # Settle order
        user.wallet_balance = round(user.wallet_balance - total_consideration, 2)
        db.add(WalletTransaction(
            user_id=user.id,
            tx_type="TRADE_DEBIT",
            amount=-total_consideration,
            balance_after=user.wallet_balance,
            description=f"Bought {units} units of {instrument.name} ({instrument.isin})",
            reference_id=order_id
        ))

        # Update or create holding
        holding = db.query(Holding).filter(
            Holding.user_id == user.id,
            Holding.instrument_id == instrument.id
        ).first()

        if holding:
            new_units = holding.units + units
            new_avg_price = round(((holding.avg_purchase_price * holding.units) + (exec_clean_price * units)) / new_units, 2)
            holding.units = new_units
            holding.avg_purchase_price = new_avg_price
        else:
            holding = Holding(
                holding_id=f"hld_{uuid.uuid4().hex[:8]}",
                user_id=user.id,
                instrument_id=instrument.id,
                units=units,
                avg_purchase_price=exec_clean_price,
                avg_purchase_yield=instrument.current_ytm
            )
            db.add(holding)

    elif side == "SELL":
        # Check holding
        holding = db.query(Holding).filter(
            Holding.user_id == user.id,
            Holding.instrument_id == instrument.id
        ).first()

        if not holding or holding.units < units:
            avail = holding.units if holding else 0
            return False, f"Insufficient holdings. You own {avail} units, requested {units}.", None

        holding.units -= units
        if holding.units <= 0:
            db.delete(holding)

        net_proceeds = round((exec_clean_price + accrued_per_unit) * units - stamp_duty - exchange_charges, 2)
        user.wallet_balance = round(user.wallet_balance + net_proceeds, 2)
        db.add(WalletTransaction(
            user_id=user.id,
            tx_type="TRADE_CREDIT",
            amount=net_proceeds,
            balance_after=user.wallet_balance,
            description=f"Sold {units} units of {instrument.name} ({instrument.isin})",
            reference_id=order_id
        ))

    order = SecondaryOrder(
        order_id=order_id,
        user_id=user.id,
        instrument_id=instrument.id,
        side=side,
        order_type=order_type,
        limit_price=limit_price,
        limit_yield=limit_yield,
        units=units,
        execution_price=exec_clean_price,
        accrued_interest_per_unit=accrued_per_unit,
        stamp_duty=stamp_duty,
        exchange_charges=exchange_charges,
        total_consideration=total_consideration,
        settlement_date=settlement_date,
        status="SETTLED"
    )
    db.add(order)
    db.commit()
    db.refresh(order)

    # Fire Webhook and TradeOne outbox event
    trigger_holdings_updated_event(db, user, background_tasks)
    enqueue_holdings_changed(user.email)

    return True, "Order successfully executed and settled.", order

def credit_bond_coupon(db: Session, user: User, instrument: Instrument, background_tasks = None) -> Tuple[bool, str, float]:
    """Admin action: Credits coupon interest for a user's holding."""
    holding = db.query(Holding).filter(
        Holding.user_id == user.id,
        Holding.instrument_id == instrument.id
    ).first()

    if not holding:
        return False, "User does not hold this instrument.", 0.0

    # Coupon payout: (Face Value * Coupon Rate / Frequency) * units
    from app.services.bond_math import get_frequency_count
    m = get_frequency_count(instrument.coupon_frequency)
    effective_m = m if m > 0 else 1
    coupon_amount = round(holding.units * (instrument.face_value * instrument.coupon_rate / effective_m), 2)

    user.wallet_balance = round(user.wallet_balance + coupon_amount, 2)
    db.add(WalletTransaction(
        user_id=user.id,
        tx_type="COUPON_CREDIT",
        amount=coupon_amount,
        balance_after=user.wallet_balance,
        description=f"Coupon payout credited for {holding.units} units of {instrument.name}",
        reference_id=instrument.isin
    ))
    db.commit()

    trigger_holdings_updated_event(db, user, background_tasks)
    return True, f"Successfully credited ₹{coupon_amount:,.2f} coupon interest.", coupon_amount

def mature_bond_holding(db: Session, user: User, instrument: Instrument, background_tasks = None) -> Tuple[bool, str, float]:
    """Admin action: Redeems a bond at maturity, credits principal to wallet, removes holding."""
    holding = db.query(Holding).filter(
        Holding.user_id == user.id,
        Holding.instrument_id == instrument.id
    ).first()

    if not holding:
        return False, "User does not hold this bond.", 0.0

    redemption_amount = round(holding.units * instrument.face_value, 2)
    user.wallet_balance = round(user.wallet_balance + redemption_amount, 2)
    
    db.add(WalletTransaction(
        user_id=user.id,
        tx_type="REDEMPTION_CREDIT",
        amount=redemption_amount,
        balance_after=user.wallet_balance,
        description=f"Bond matured: Principal redemption for {holding.units} units of {instrument.name}",
        reference_id=instrument.isin
    ))
    
    db.delete(holding)
    db.commit()

    trigger_holdings_updated_event(db, user, background_tasks)
    enqueue_holdings_changed(user.email)
    return True, f"Bond matured. Redeemed ₹{redemption_amount:,.2f} principal.", redemption_amount
