from datetime import date
import pytest
from app.services.bond_math import (
    calculate_accrued_interest,
    calculate_dirty_price_from_ytm,
    calculate_bond_metrics,
    calculate_ytm_from_clean_price,
    generate_cash_flow_dates,
    generate_cash_flow_schedule
)

def test_accrued_interest_exact():
    # 10% annual coupon, face value 1000, 182.5 days accrued should be ~50
    issue = date(2025, 1, 1)
    maturity = date(2030, 1, 1)
    settlement = date(2025, 7, 2) # 182 days later
    accrued, last_c, next_c = calculate_accrued_interest(
        face_value=1000.0,
        coupon_rate=0.10,
        frequency="ANNUAL",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement
    )
    # Expected: 1000 * 0.10 * (182 / 365) = 49.863
    assert last_c == issue
    assert next_c == date(2026, 1, 1)
    assert 49.5 <= accrued <= 50.5

def test_bond_price_at_par():
    # When coupon rate equals YTM exactly on a coupon date, clean price is approximately face value (1000)
    issue = date(2025, 1, 1)
    maturity = date(2030, 1, 1)
    settlement = date(2025, 1, 1)
    metrics = calculate_bond_metrics(
        face_value=1000.0,
        coupon_rate=0.08,
        frequency="ANNUAL",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement,
        ytm=0.08
    )
    assert metrics["accrued_interest"] == 0.0
    assert abs(metrics["clean_price"] - 1000.0) < 0.5
    assert abs(metrics["dirty_price"] - 1000.0) < 0.5

def test_zero_coupon_tbill():
    # 91-day T-bill with face value 100, YTM 6.5%
    issue = date(2026, 1, 1)
    maturity = date(2026, 4, 2) # 91 days
    settlement = date(2026, 1, 1)
    metrics = calculate_bond_metrics(
        face_value=100.0,
        coupon_rate=0.0,
        frequency="ZERO_COUPON",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement,
        ytm=0.065
    )
    assert metrics["accrued_interest"] == 0.0
    # Price should be 100 / (1.065 ** (91/365)) approx 98.44
    assert 98.0 < metrics["clean_price"] < 99.0

def test_ytm_inversion():
    # Given clean price, calculate YTM and check it matches back
    issue = date(2024, 6, 15)
    maturity = date(2029, 6, 15)
    settlement = date(2026, 10, 7)
    known_ytm = 0.0782
    metrics = calculate_bond_metrics(
        face_value=1000.0,
        coupon_rate=0.0810,
        frequency="SEMI_ANNUAL",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement,
        ytm=known_ytm
    )
    computed_ytm = calculate_ytm_from_clean_price(
        face_value=1000.0,
        coupon_rate=0.0810,
        frequency="SEMI_ANNUAL",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement,
        target_clean_price=metrics["clean_price"]
    )
    assert abs(computed_ytm - known_ytm) < 0.001

def test_cash_flow_schedule():
    issue = date(2025, 1, 1)
    maturity = date(2027, 1, 1)
    settlement = date(2025, 6, 1)
    schedule = generate_cash_flow_schedule(
        face_value=1000.0,
        coupon_rate=0.08,
        frequency="ANNUAL",
        issue_date=issue,
        maturity_date=maturity,
        settlement_date=settlement
    )
    assert len(schedule) == 2
    assert schedule[-1]["is_maturity"] is True
    assert schedule[-1]["principal_repayment"] == 1000.0
