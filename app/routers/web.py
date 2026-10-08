import csv
import io
import datetime
from datetime import date, timedelta
from typing import Optional, List, Dict, Any
from fastapi import APIRouter, Depends, Request, Form, Response, HTTPException, Query, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, StreamingResponse
from app.templates_engine import templates
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.entities import (
    User, Instrument, Holding, SecondaryOrder, WalletTransaction,
    PrimaryIssue, PrimaryApplication, ApiConsent, SystemState, PriceHistory
)
from app.routers.auth import get_current_user_optional
from app.services.order_service import place_secondary_order, get_simulated_order_book
from app.services.bond_math import (
    get_frequency_count, generate_cash_flow_dates, generate_cash_flow_schedule,
    calculate_bond_metrics
)

router = APIRouter(tags=["Web Pages"])

def calculate_portfolio_summary(db: Session, user: User):
    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    total_invested = 0.0
    current_value = 0.0
    accrued_interest = 0.0
    weighted_ytm_sum = 0.0
    today = date.today()

    upcoming_coupons = []
    upcoming_maturities = []

    for h in holdings:
        inst = h.instrument
        invested = h.avg_purchase_price * h.units
        curr_val = inst.clean_price * h.units
        accrued = inst.accrued_interest * h.units

        total_invested += invested
        current_value += curr_val
        accrued_interest += accrued
        weighted_ytm_sum += (inst.current_ytm * curr_val)

        # Coupon payout per unit
        m = get_frequency_count(inst.coupon_frequency)
        if m > 0 and inst.next_coupon_date and inst.next_coupon_date >= today:
            c_payout = (inst.face_value * inst.coupon_rate / m) * h.units
            upcoming_coupons.append({
                "instrument_name": inst.name,
                "isin": inst.isin,
                "date": inst.next_coupon_date,
                "amount": c_payout
            })

        if inst.maturity_date >= today:
            upcoming_maturities.append({
                "instrument_name": inst.name,
                "isin": inst.isin,
                "date": inst.maturity_date,
                "amount": inst.face_value * h.units
            })

    upcoming_coupons.sort(key=lambda x: x["date"])
    upcoming_maturities.sort(key=lambda x: x["date"])

    portfolio_ytm = (weighted_ytm_sum / current_value) if current_value > 0 else 0.0
    pnl = current_value - total_invested
    pnl_pct = (pnl / total_invested * 100.0) if total_invested > 0 else 0.0

    return {
        "total_invested": round(total_invested, 2),
        "current_value": round(current_value, 2),
        "accrued_interest": round(accrued_interest, 2),
        "portfolio_ytm": round(portfolio_ytm * 100.0, 2),
        "pnl": round(pnl, 2),
        "pnl_pct": round(pnl_pct, 2),
        "next_coupon": upcoming_coupons[0] if upcoming_coupons else None,
        "next_maturity": upcoming_maturities[0] if upcoming_maturities else None,
        "holdings_count": len(holdings)
    }

@router.get("/", response_class=HTMLResponse)
async def home_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    summary = calculate_portfolio_summary(db, user)
    state = db.query(SystemState).first()
    featured_bonds = db.query(Instrument).filter(Instrument.is_active == True).limit(6).all()
    recent_orders = db.query(SecondaryOrder).filter(SecondaryOrder.user_id == user.id).order_by(SecondaryOrder.created_at.desc()).limit(5).all()

    return templates.TemplateResponse("home.html", {
        "request": request,
        "user": user,
        "summary": summary,
        "state": state,
        "featured_bonds": featured_bonds,
        "recent_orders": recent_orders
    })

@router.get("/marketplace", response_class=HTMLResponse)
async def marketplace_page(
    request: Request,
    type: Optional[str] = Query(None),
    rating: Optional[str] = Query(None),
    min_ytm: Optional[float] = Query(None),
    max_ytm: Optional[float] = Query(None),
    frequency: Optional[str] = Query(None),
    min_inv: Optional[float] = Query(None),
    sort: Optional[str] = Query("ytm_desc"),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    query = db.query(Instrument).filter(Instrument.is_active == True)

    if type and type != "ALL":
        query = query.filter(Instrument.instrument_type == type)
    if rating and rating != "ALL":
        query = query.filter(Instrument.credit_rating == rating)
    if min_ytm is not None:
        query = query.filter(Instrument.current_ytm >= (min_ytm / 100.0))
    if max_ytm is not None:
        query = query.filter(Instrument.current_ytm <= (max_ytm / 100.0))
    if frequency and frequency != "ALL":
        query = query.filter(Instrument.coupon_frequency == frequency)
    if min_inv is not None:
        query = query.filter((Instrument.face_value * Instrument.min_lot_size) <= min_inv)

    if sort == "ytm_desc":
        query = query.order_by(Instrument.current_ytm.desc())
    elif sort == "ytm_asc":
        query = query.order_by(Instrument.current_ytm.asc())
    elif sort == "maturity_asc":
        query = query.order_by(Instrument.maturity_date.asc())
    elif sort == "maturity_desc":
        query = query.order_by(Instrument.maturity_date.desc())
    elif sort == "rating":
        query = query.order_by(Instrument.credit_rating.asc())

    bonds = query.all()
    state = db.query(SystemState).first()

    return templates.TemplateResponse("marketplace.html", {
        "request": request,
        "user": user,
        "bonds": bonds,
        "state": state,
        "selected_type": type or "ALL",
        "selected_rating": rating or "ALL",
        "selected_frequency": frequency or "ALL",
        "selected_sort": sort,
        "min_ytm": min_ytm,
        "max_ytm": max_ytm,
        "min_inv": min_inv
    })

@router.get("/bonds/{isin}", response_class=HTMLResponse)
async def bond_detail_page(isin: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    inst = db.query(Instrument).filter(Instrument.isin == isin).first()
    if not inst:
        raise HTTPException(status_code=404, detail="Bond not found")

    today = date.today()
    schedule = generate_cash_flow_schedule(
        inst.face_value, inst.coupon_rate, inst.coupon_frequency,
        inst.issue_date, inst.maturity_date, today
    )

    # 2 years price history
    history = db.query(PriceHistory).filter(PriceHistory.instrument_id == inst.id).order_by(PriceHistory.date.asc()).all()
    history_dates = [h.date.isoformat() for h in history]
    history_prices = [h.clean_price for h in history]
    history_yields = [round(h.ytm * 100.0, 2) for h in history]

    # Cash flow chart labels and values
    cf_labels = [s["payment_date"] for s in schedule[:12]]
    cf_values = [s["total_cash_flow"] for s in schedule[:12]]

    order_book = get_simulated_order_book(inst)

    # User's current holding in this bond
    user_holding = db.query(Holding).filter(
        Holding.user_id == user.id,
        Holding.instrument_id == inst.id
    ).first() if user else None

    return templates.TemplateResponse("bond_detail.html", {
        "request": request,
        "user": user,
        "bond": inst,
        "schedule": schedule,
        "history_dates": history_dates,
        "history_prices": history_prices,
        "history_yields": history_yields,
        "cf_labels": cf_labels,
        "cf_values": cf_values,
        "order_book": order_book,
        "user_holding": user_holding
    })

@router.post("/order/place")
async def place_order_web(
    request: Request,
    background_tasks: BackgroundTasks,
    isin: str = Form(...),
    side: str = Form(...),
    order_type: str = Form(...),
    units: int = Form(...),
    limit_price: Optional[float] = Form(None),
    limit_yield: Optional[float] = Form(None),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    inst = db.query(Instrument).filter(Instrument.isin == isin).first()
    if not inst:
        return RedirectResponse(url="/marketplace", status_code=303)

    success, msg, order = place_secondary_order(
        db, user, inst, side, order_type, units,
        limit_price=limit_price, limit_yield=limit_yield,
        background_tasks=background_tasks
    )

    if not success:
        return RedirectResponse(url=f"/bonds/{isin}?error={msg}", status_code=303)

    return RedirectResponse(url=f"/orders?msg=Order+executed+successfully", status_code=303)

@router.get("/orders", response_class=HTMLResponse)
async def orders_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    orders = db.query(SecondaryOrder).filter(SecondaryOrder.user_id == user.id).order_by(SecondaryOrder.created_at.desc()).all()
    return templates.TemplateResponse("orders.html", {
        "request": request,
        "user": user,
        "orders": orders
    })

@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    order = db.query(SecondaryOrder).filter(
        SecondaryOrder.order_id == order_id,
        SecondaryOrder.user_id == user.id
    ).first()

    if order and order.status == "OPEN":
        order.status = "CANCELLED"
        db.commit()

    return RedirectResponse(url="/orders?msg=Order+cancelled", status_code=303)

@router.get("/holdings", response_class=HTMLResponse)
async def holdings_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    today = date.today()

    rows = []
    tot_invested = 0.0
    tot_current_val = 0.0
    tot_accrued = 0.0

    for h in holdings:
        inst = h.instrument
        invested = h.avg_purchase_price * h.units
        current_val = inst.clean_price * h.units
        accrued = inst.accrued_interest * h.units
        pnl = current_val - invested
        pnl_pct = (pnl / invested * 100.0) if invested > 0 else 0.0
        days_to_mat = max(0, (inst.maturity_date - today).days)

        tot_invested += invested
        tot_current_val += current_val
        tot_accrued += accrued

        rows.append({
            "holding": h,
            "instrument": inst,
            "invested": round(invested, 2),
            "current_val": round(current_val, 2),
            "accrued": round(accrued, 2),
            "pnl": round(pnl, 2),
            "pnl_pct": round(pnl_pct, 2),
            "days_to_mat": days_to_mat
        })

    total_pnl = tot_current_val - tot_invested
    total_pnl_pct = (total_pnl / tot_invested * 100.0) if tot_invested > 0 else 0.0

    return templates.TemplateResponse("holdings.html", {
        "request": request,
        "user": user,
        "rows": rows,
        "tot_invested": round(tot_invested, 2),
        "tot_current_val": round(tot_current_val, 2),
        "tot_accrued": round(tot_accrued, 2),
        "total_pnl": round(total_pnl, 2),
        "total_pnl_pct": round(total_pnl_pct, 2)
    })

@router.get("/primary-issues", response_class=HTMLResponse)
async def primary_issues_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    issues = db.query(PrimaryIssue).all()
    applications = db.query(PrimaryApplication).filter(PrimaryApplication.user_id == user.id).all() if user else []

    return templates.TemplateResponse("primary_issues.html", {
        "request": request,
        "user": user,
        "issues": issues,
        "applications": applications
    })

@router.post("/primary-issues/apply")
async def apply_primary_issue(
    request: Request,
    issue_id: int = Form(...),
    lots: int = Form(...),
    db: Session = Depends(get_db)
):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    issue = db.query(PrimaryIssue).filter(PrimaryIssue.id == issue_id).first()
    if not issue or issue.status != "OPEN":
        return RedirectResponse(url="/primary-issues?error=Issue+is+not+open", status_code=303)

    req_amt = issue.face_value * lots
    if user.wallet_balance < req_amt:
        return RedirectResponse(url=f"/primary-issues?error=Insufficient+wallet+balance.+Requires+₹{req_amt:,.2f}", status_code=303)

    import uuid
    user.wallet_balance -= req_amt
    db.add(WalletTransaction(
        user_id=user.id,
        tx_type="TRADE_DEBIT",
        amount=-req_amt,
        balance_after=user.wallet_balance,
        description=f"Applied for {lots} lots of {issue.name} ({issue.issue_code})",
        reference_id=issue.issue_code
    ))

    app = PrimaryApplication(
        app_id=f"app_{uuid.uuid4().hex[:8]}",
        user_id=user.id,
        primary_issue_id=issue.id,
        lots=lots,
        amount=req_amt,
        status="SUBMITTED"
    )
    db.add(app)
    db.commit()

    return RedirectResponse(url="/primary-issues?msg=Application+submitted+successfully", status_code=303)

@router.get("/income-calendar", response_class=HTMLResponse)
async def income_calendar_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    today = date.today()
    future_events = []
    monthly_totals = {}

    for h in holdings:
        inst = h.instrument
        m = get_frequency_count(inst.coupon_frequency)
        cf_dates = [d for d in generate_cash_flow_dates(inst.issue_date, inst.maturity_date, inst.coupon_frequency) if d >= today]
        coupon_amt = (inst.face_value * inst.coupon_rate / m) * h.units if m > 0 else 0.0

        for d in cf_dates:
            month_key = d.strftime("%b %Y")
            if d == inst.maturity_date:
                amt = (coupon_amt + (inst.face_value * h.units))
                future_events.append({
                    "date": d,
                    "instrument_name": inst.name,
                    "isin": inst.isin,
                    "event_type": "MATURITY & COUPON",
                    "amount": round(amt, 2)
                })
                monthly_totals[month_key] = monthly_totals.get(month_key, 0.0) + amt
            elif coupon_amt > 0:
                future_events.append({
                    "date": d,
                    "instrument_name": inst.name,
                    "isin": inst.isin,
                    "event_type": "COUPON",
                    "amount": round(coupon_amt, 2)
                })
                monthly_totals[month_key] = monthly_totals.get(month_key, 0.0) + coupon_amt

    future_events.sort(key=lambda x: x["date"])

    # Prepare next 12 calendar months for chart
    chart_months = []
    chart_incomes = []
    curr_m = today.replace(day=1)
    for _ in range(12):
        lbl = curr_m.strftime("%b %Y")
        chart_months.append(lbl)
        chart_incomes.append(round(monthly_totals.get(lbl, 0.0), 2))
        # advance 1 month
        curr_m = (curr_m + timedelta(days=32)).replace(day=1)

    return templates.TemplateResponse("income_calendar.html", {
        "request": request,
        "user": user,
        "events": future_events[:50],
        "chart_months": chart_months,
        "chart_incomes": chart_incomes
    })

@router.get("/maturity-ladder", response_class=HTMLResponse)
async def maturity_ladder_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    yearly_maturities = {}
    ladder_details = []

    for h in holdings:
        inst = h.instrument
        yr = inst.maturity_date.year
        principal = inst.face_value * h.units
        yearly_maturities[yr] = yearly_maturities.get(yr, 0.0) + principal
        ladder_details.append({
            "year": yr,
            "instrument_name": inst.name,
            "isin": inst.isin,
            "maturity_date": inst.maturity_date,
            "units": h.units,
            "principal": round(principal, 2)
        })

    ladder_details.sort(key=lambda x: x["maturity_date"])
    years = sorted(yearly_maturities.keys())
    ladder_amounts = [round(yearly_maturities[y], 2) for y in years]

    return templates.TemplateResponse("maturity_ladder.html", {
        "request": request,
        "user": user,
        "years": [str(y) for y in years],
        "ladder_amounts": ladder_amounts,
        "ladder_details": ladder_details
    })

@router.get("/wallet", response_class=HTMLResponse)
async def wallet_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    transactions = db.query(WalletTransaction).filter(WalletTransaction.user_id == user.id).order_by(WalletTransaction.created_at.desc()).all()
    return templates.TemplateResponse("wallet.html", {
        "request": request,
        "user": user,
        "transactions": transactions
    })

@router.post("/wallet/add-money")
async def wallet_add_money(request: Request, amount: float = Form(...), db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user or amount <= 0:
        return RedirectResponse(url="/wallet", status_code=303)

    user.wallet_balance = round(user.wallet_balance + amount, 2)
    db.add(WalletTransaction(
        user_id=user.id,
        tx_type="DEPOSIT",
        amount=amount,
        balance_after=user.wallet_balance,
        description=f"Net banking funds transfer from linked {user.bank_name} account"
    ))
    db.commit()
    return RedirectResponse(url="/wallet?msg=Funds+added+successfully", status_code=303)

@router.post("/wallet/withdraw")
async def wallet_withdraw(request: Request, amount: float = Form(...), db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user or amount <= 0:
        return RedirectResponse(url="/wallet", status_code=303)

    if user.wallet_balance < amount:
        return RedirectResponse(url="/wallet?error=Insufficient+balance+to+withdraw", status_code=303)

    user.wallet_balance = round(user.wallet_balance - amount, 2)
    db.add(WalletTransaction(
        user_id=user.id,
        tx_type="WITHDRAWAL",
        amount=-amount,
        balance_after=user.wallet_balance,
        description=f"Payout to linked {user.bank_name} account ({user.bank_account_masked})"
    ))
    db.commit()
    return RedirectResponse(url="/wallet?msg=Withdrawal+processed", status_code=303)

@router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    # Calculate interest income statement
    coupon_txs = db.query(WalletTransaction).filter(
        WalletTransaction.user_id == user.id,
        WalletTransaction.tx_type == "COUPON_CREDIT"
    ).order_by(WalletTransaction.created_at.desc()).all()

    total_interest = sum(t.amount for t in coupon_txs)
    tds_threshold = 5000.0
    # 10% TDS on coupon credits over threshold
    tds_deducted = sum(t.amount * 0.10 for t in coupon_txs if t.amount > tds_threshold)

    return templates.TemplateResponse("reports.html", {
        "request": request,
        "user": user,
        "coupon_txs": coupon_txs,
        "total_interest": round(total_interest, 2),
        "tds_deducted": round(tds_deducted, 2)
    })

@router.get("/reports/export/{rtype}")
async def export_report_csv(rtype: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        raise HTTPException(status_code=401, detail="Unauthorized")

    output = io.StringIO()
    writer = csv.writer(output)

    if rtype == "interest":
        writer.writerow(["Date", "Description", "ISIN / Reference", "Gross Interest (INR)", "TDS Deducted (10%)", "Net Credited (INR)"])
        txs = db.query(WalletTransaction).filter(
            WalletTransaction.user_id == user.id,
            WalletTransaction.tx_type == "COUPON_CREDIT"
        ).order_by(WalletTransaction.created_at.desc()).all()
        for t in txs:
            tds = round(t.amount * 0.10, 2) if t.amount > 5000 else 0.0
            net = round(t.amount - tds, 2)
            writer.writerow([t.created_at.strftime("%Y-%m-%d"), t.description, t.reference_id or "N/A", f"{t.amount:.2f}", f"{tds:.2f}", f"{net:.2f}"])
        filename = f"BondBazaar_Interest_Statement_{user.customer_id}.csv"

    elif rtype == "transactions":
        writer.writerow(["Order ID", "Date", "Side", "Bond Name", "ISIN", "Units", "Execution Price", "Accrued Interest", "Charges", "Total Consideration", "Status"])
        orders = db.query(SecondaryOrder).filter(SecondaryOrder.user_id == user.id).order_by(SecondaryOrder.created_at.desc()).all()
        for o in orders:
            writer.writerow([
                o.order_id, o.settlement_date.isoformat(), o.side, o.instrument.name, o.instrument.isin,
                o.units, f"{o.execution_price:.2f}", f"{o.accrued_interest_per_unit * o.units:.2f}",
                f"{o.stamp_duty + o.exchange_charges:.2f}", f"{o.total_consideration:.2f}", o.status
            ])
        filename = f"BondBazaar_Transactions_{user.customer_id}.csv"

    elif rtype == "tds":
        writer.writerow(["Financial Year", "Section", "Gross Interest Paid", "Exempt Amount", "Taxable Interest", "TDS Deducted at 10%", "Form 16A Status"])
        txs = db.query(WalletTransaction).filter(
            WalletTransaction.user_id == user.id,
            WalletTransaction.tx_type == "COUPON_CREDIT"
        ).all()
        tot = sum(t.amount for t in txs)
        tds = sum(t.amount * 0.10 for t in txs if t.amount > 5000)
        writer.writerow(["2026-27", "193 (Interest on Securities)", f"{tot:.2f}", "5000.00", f"{max(0, tot - 5000):.2f}", f"{tds:.2f}", "Generated"])
        filename = f"BondBazaar_TDS_Summary_{user.customer_id}.csv"

    else:
        raise HTTPException(status_code=400, detail="Invalid report type")

    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )

@router.get("/insights", response_class=HTMLResponse)
async def insights_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    holdings = db.query(Holding).filter(Holding.user_id == user.id).all()
    
    total_val = 0.0
    type_allocation = {}
    rating_buckets = {"SOVEREIGN": 0.0, "AAA": 0.0, "AA": 0.0, "A_AND_BELOW": 0.0}
    issuer_allocation = {}

    for h in holdings:
        inst = h.instrument
        val = inst.clean_price * h.units
        total_val += val

        type_allocation[inst.instrument_type] = type_allocation.get(inst.instrument_type, 0.0) + val
        issuer_allocation[inst.issuer_name] = issuer_allocation.get(inst.issuer_name, 0.0) + val

        r = (inst.credit_rating or "").upper()
        if "SOVEREIGN" in r:
            rating_buckets["SOVEREIGN"] += val
        elif "AAA" in r:
            rating_buckets["AAA"] += val
        elif "AA" in r:
            rating_buckets["AA"] += val
        else:
            rating_buckets["A_AND_BELOW"] += val

    # Risk Warnings Check:
    # 1. Warn if one issuer exceeds 25%
    # 2. Warn if more than 30% is below AA (i.e. rated A or lower)
    risk_warnings = []
    if total_val > 0:
        for issuer, val in issuer_allocation.items():
            pct = (val / total_val) * 100.0
            if pct > 25.0:
                risk_warnings.append({
                    "title": "High Issuer Concentration Risk",
                    "severity": "WARNING",
                    "detail": f"Exposure to '{issuer}' is {pct:.1f}% of your portfolio, exceeding the recommended 25% single-issuer limit."
                })

        below_aa_pct = (rating_buckets["A_AND_BELOW"] / total_val) * 100.0
        if below_aa_pct > 30.0:
            risk_warnings.append({
                "title": "Sub-AA Credit Rating Risk",
                "severity": "CRITICAL",
                "detail": f"{below_aa_pct:.1f}% of your portfolio is rated 'A' or lower, exceeding the conservative 30% credit risk threshold."
            })

    return templates.TemplateResponse("insights.html", {
        "request": request,
        "user": user,
        "total_val": round(total_val, 2),
        "type_labels": list(type_allocation.keys()),
        "type_values": [round(v, 2) for v in type_allocation.values()],
        "rating_labels": ["Sovereign", "AAA", "AA", "A & Below"],
        "rating_values": [round(rating_buckets["SOVEREIGN"], 2), round(rating_buckets["AAA"], 2), round(rating_buckets["AA"], 2), round(rating_buckets["A_AND_BELOW"], 2)],
        "issuer_labels": list(issuer_allocation.keys()),
        "issuer_values": [round(v, 2) for v in issuer_allocation.values()],
        "risk_warnings": risk_warnings
    })

@router.get("/profile", response_class=HTMLResponse)
async def profile_page(request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    if not user:
        return RedirectResponse(url="/login", status_code=303)

    consents = db.query(ApiConsent).filter(ApiConsent.user_id == user.id).all()
    return templates.TemplateResponse("profile.html", {
        "request": request,
        "user": user,
        "consents": consents
    })

@router.post("/profile/revoke-app/{consent_id}")
async def revoke_connected_app(consent_id: str, request: Request, db: Session = Depends(get_db)):
    user = get_current_user_optional(request, db)
    consent = db.query(ApiConsent).filter(
        ApiConsent.consent_id == consent_id,
        ApiConsent.user_id == user.id
    ).first()

    if consent:
        consent.status = "REVOKED"
        db.commit()

    return RedirectResponse(url="/profile?msg=App+access+revoked", status_code=303)
