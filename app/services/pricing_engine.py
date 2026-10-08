import asyncio
import random
from datetime import date, timedelta
from sqlalchemy.orm import Session
from app.database import SessionLocal
from app.models.entities import Instrument, SystemState
from app.services.bond_math import calculate_bond_metrics

def recalculate_instrument_prices(db: Session, inst: Instrument, benchmark_yield: float, today: date):
    settlement_date = today + timedelta(days=1)
    
    # Target yield is benchmark yield + bond's spread
    spread = (inst.benchmark_spread_bps or 0.0) / 10000.0
    new_ytm = max(0.005, benchmark_yield + spread)

    metrics = calculate_bond_metrics(
        face_value=inst.face_value,
        coupon_rate=inst.coupon_rate,
        frequency=inst.coupon_frequency,
        issue_date=inst.issue_date,
        maturity_date=inst.maturity_date,
        settlement_date=settlement_date,
        ytm=new_ytm
    )

    inst.current_ytm = metrics["ytm"]
    inst.clean_price = metrics["clean_price"]
    inst.dirty_price = metrics["dirty_price"]
    inst.accrued_interest = metrics["accrued_interest"]
    inst.current_yield = metrics["current_yield"]
    inst.modified_duration = metrics["modified_duration"]
    if metrics["next_coupon_date"]:
        inst.next_coupon_date = date.fromisoformat(metrics["next_coupon_date"])

def nudge_market_prices(db: Session):
    state = db.query(SystemState).first()
    if not state or not state.is_market_open:
        return

    # Benchmark yield micro-shift: +/- 0.5 bps
    micro_shift = random.uniform(-0.00005, 0.00005)
    state.benchmark_yield = max(0.04, min(0.12, state.benchmark_yield + micro_shift))
    
    today = date.today()
    instruments = db.query(Instrument).filter(Instrument.is_active == True).all()
    for inst in instruments:
        # Small idiosyncratic spread noise: +/- 0.2 bps
        spread_noise = random.uniform(-0.2, 0.2)
        inst.benchmark_spread_bps = round(inst.benchmark_spread_bps + spread_noise, 2)
        recalculate_instrument_prices(db, inst, state.benchmark_yield, today)

    db.commit()

def apply_rate_shock(db: Session, bps_shift: float):
    """
    Shift benchmark yield by bps_shift basis points (e.g. +50 bps = +0.0050).
    Recalculates all bond prices immediately.
    """
    state = db.query(SystemState).first()
    if not state:
        return

    shift_decimal = bps_shift / 10000.0
    state.benchmark_yield = max(0.01, state.benchmark_yield + shift_decimal)
    
    today = date.today()
    instruments = db.query(Instrument).filter(Instrument.is_active == True).all()
    for inst in instruments:
        recalculate_instrument_prices(db, inst, state.benchmark_yield, today)

    db.commit()

async def pricing_background_task():
    """Background task nudging yields every 10 seconds."""
    while True:
        try:
            await asyncio.sleep(10)
            db = SessionLocal()
            try:
                nudge_market_prices(db)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as e:
            # log and continue
            await asyncio.sleep(10)
