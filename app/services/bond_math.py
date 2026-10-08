from datetime import date, timedelta
from typing import List, Dict, Any, Tuple
import math

def add_months(sourcedate: date, months: int) -> date:
    """Safely add months to a date, preserving end-of-month or day where possible."""
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, [31,
        29 if year % 4 == 0 and (year % 100 != 0 or year % 400 == 0) else 28,
        31, 30, 31, 30, 31, 31, 30, 31, 30, 31][month - 1])
    return date(year, month, day)

def get_frequency_count(freq_str: str) -> int:
    """Return number of coupons per year."""
    freq = (freq_str or "").upper()
    if freq in ("ANNUAL", "YEARLY"):
        return 1
    elif freq in ("SEMI_ANNUAL", "SEMI-ANNUAL", "HALF_YEARLY"):
        return 2
    elif freq in ("QUARTERLY",):
        return 4
    elif freq in ("MONTHLY",):
        return 12
    elif freq in ("CUMULATIVE", "ZERO_COUPON", "ZERO"):
        return 0
    return 1

def generate_cash_flow_dates(issue_date: date, maturity_date: date, frequency: str) -> List[date]:
    """
    Generate all coupon dates up to maturity date.
    Works backwards from maturity date by frequency intervals.
    """
    m = get_frequency_count(frequency)
    if m == 0:
        return [maturity_date]

    interval_months = 12 // m
    dates = [maturity_date]
    curr = maturity_date
    while True:
        # Step back interval_months
        curr = add_months(curr, -interval_months)
        if curr <= issue_date:
            break
        dates.append(curr)

    dates.reverse()
    return dates

def calculate_accrued_interest(
    face_value: float,
    coupon_rate: float,
    frequency: str,
    issue_date: date,
    maturity_date: date,
    settlement_date: date,
    day_count: str = "ACTUAL/365"
) -> Tuple[float, date, date]:
    """
    Calculates accrued interest using Actual/365 day count convention.
    Returns: (accrued_interest, last_coupon_date, next_coupon_date)
    """
    m = get_frequency_count(frequency)
    if m == 0 or settlement_date >= maturity_date:
        return 0.0, issue_date, maturity_date

    cf_dates = generate_cash_flow_dates(issue_date, maturity_date, frequency)
    
    # Find last coupon date before or on settlement_date
    last_coupon = issue_date
    next_coupon = maturity_date
    for d in cf_dates:
        if d <= settlement_date:
            last_coupon = d
        elif d > settlement_date:
            next_coupon = d
            break

    days_accrued = max(0, (settlement_date - last_coupon).days)
    # Standard Indian actual/365 accrued interest:
    # Face Value * Coupon Rate * (Days / 365)
    accrued = face_value * coupon_rate * (days_accrued / 365.0)
    return round(accrued, 4), last_coupon, next_coupon

def calculate_dirty_price_from_ytm(
    face_value: float,
    coupon_rate: float,
    frequency: str,
    issue_date: date,
    maturity_date: date,
    settlement_date: date,
    ytm: float
) -> float:
    """
    Calculates dirty price given annual YTM (e.g. 0.075 for 7.50%).
    Discounts all future cash flows from settlement date.
    """
    if settlement_date >= maturity_date:
        return face_value

    m = get_frequency_count(frequency)
    
    # Zero coupon bond / T-Bill
    if m == 0:
        days = max(1, (maturity_date - settlement_date).days)
        t = days / 365.0
        # Money market / Zero coupon discounting
        dirty_price = face_value / ((1.0 + ytm) ** t)
        return round(dirty_price, 4)

    # Coupon bond
    cf_dates = [d for d in generate_cash_flow_dates(issue_date, maturity_date, frequency) if d > settlement_date]
    if not cf_dates:
        return face_value

    coupon_payment = face_value * (coupon_rate / m)
    dirty_price = 0.0
    
    for i, cf_date in enumerate(cf_dates):
        days_to_cf = (cf_date - settlement_date).days
        t = days_to_cf / 365.0
        cf_amount = coupon_payment
        if cf_date == maturity_date:
            cf_amount += face_value
        
        # Periodic compounding: (1 + ytm/m)**(m*t)
        discount_factor = (1.0 + ytm / m) ** (m * t)
        dirty_price += cf_amount / discount_factor

    return round(dirty_price, 4)

def calculate_bond_metrics(
    face_value: float,
    coupon_rate: float,
    frequency: str,
    issue_date: date,
    maturity_date: date,
    settlement_date: date,
    ytm: float
) -> Dict[str, Any]:
    """
    Computes all standard bond metrics:
    clean_price, dirty_price, accrued_interest, ytm, current_yield, modified_duration,
    last_coupon_date, next_coupon_date, days_to_maturity.
    """
    accrued, last_coupon, next_coupon = calculate_accrued_interest(
        face_value, coupon_rate, frequency, issue_date, maturity_date, settlement_date
    )
    dirty_price = calculate_dirty_price_from_ytm(
        face_value, coupon_rate, frequency, issue_date, maturity_date, settlement_date, ytm
    )
    clean_price = max(0.01, dirty_price - accrued)
    
    # Current yield: (Annual Coupon) / Clean Price
    annual_coupon = face_value * coupon_rate
    current_yield = (annual_coupon / clean_price) if clean_price > 0 else 0.0

    # Modified duration calculation
    m = get_frequency_count(frequency)
    cf_dates = [d for d in generate_cash_flow_dates(issue_date, maturity_date, frequency) if d > settlement_date]
    
    weighted_time_sum = 0.0
    coupon_payment = face_value * (coupon_rate / m) if m > 0 else 0.0

    if m == 0:
        days = max(1, (maturity_date - settlement_date).days)
        t = days / 365.0
        pv = face_value / ((1.0 + ytm) ** t)
        weighted_time_sum += t * pv
    else:
        for cf_date in cf_dates:
            days_to_cf = (cf_date - settlement_date).days
            t = days_to_cf / 365.0
            cf_amount = coupon_payment + (face_value if cf_date == maturity_date else 0.0)
            pv = cf_amount / ((1.0 + ytm / m) ** (m * t))
            weighted_time_sum += t * pv

    macaulay_duration = (weighted_time_sum / dirty_price) if dirty_price > 0 else 0.0
    effective_m = m if m > 0 else 1
    modified_duration = macaulay_duration / (1.0 + (ytm / effective_m))

    days_to_maturity = max(0, (maturity_date - settlement_date).days)

    return {
        "face_value": face_value,
        "coupon_rate": round(coupon_rate, 6),
        "ytm": round(ytm, 6),
        "clean_price": round(clean_price, 2),
        "dirty_price": round(dirty_price, 2),
        "accrued_interest": round(accrued, 2),
        "current_yield": round(current_yield, 6),
        "modified_duration": round(modified_duration, 4),
        "macaulay_duration": round(macaulay_duration, 4),
        "last_coupon_date": last_coupon.isoformat(),
        "next_coupon_date": next_coupon.isoformat(),
        "days_to_maturity": days_to_maturity
    }

def calculate_ytm_from_clean_price(
    face_value: float,
    coupon_rate: float,
    frequency: str,
    issue_date: date,
    maturity_date: date,
    settlement_date: date,
    target_clean_price: float,
    tolerance: float = 1e-6,
    max_iter: int = 100
) -> float:
    """
    Solves for YTM given a target clean price using binary search.
    """
    accrued, _, _ = calculate_accrued_interest(
        face_value, coupon_rate, frequency, issue_date, maturity_date, settlement_date
    )
    target_dirty = target_clean_price + accrued

    # Binary search bounds
    low_ytm = 0.0001
    high_ytm = 1.0  # 100%

    for _ in range(max_iter):
        mid_ytm = (low_ytm + high_ytm) / 2.0
        p = calculate_dirty_price_from_ytm(
            face_value, coupon_rate, frequency, issue_date, maturity_date, settlement_date, mid_ytm
        )
        diff = p - target_dirty
        if abs(diff) < tolerance:
            return round(mid_ytm, 6)
        if diff > 0:
            # Price too high, need higher yield
            low_ytm = mid_ytm
        else:
            high_ytm = mid_ytm

    return round((low_ytm + high_ytm) / 2.0, 6)

def generate_cash_flow_schedule(
    face_value: float,
    coupon_rate: float,
    frequency: str,
    issue_date: date,
    maturity_date: date,
    settlement_date: date
) -> List[Dict[str, Any]]:
    """
    Generates detailed cash flow schedule from settlement date to maturity.
    """
    cf_dates = [d for d in generate_cash_flow_dates(issue_date, maturity_date, frequency) if d > settlement_date]
    m = get_frequency_count(frequency)
    coupon_amt = face_value * (coupon_rate / m) if m > 0 else 0.0
    
    schedule = []
    for d in cf_dates:
        is_mat = (d == maturity_date)
        principal = face_value if is_mat else 0.0
        c_amt = coupon_amt
        total = round(principal + c_amt, 2)
        schedule.append({
            "payment_date": d.isoformat(),
            "coupon_amount": round(c_amt, 2),
            "principal_repayment": round(principal, 2),
            "total_cash_flow": total,
            "is_maturity": is_mat
        })
    return schedule
