import datetime
from datetime import date, timedelta
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, Response, HTTPException, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.config import settings
from app.models.entities import (
    User, Instrument, Holding, SecondaryOrder, WalletTransaction,
    PrimaryIssue, PrimaryApplication, ApiConsent, WebhookLog, ApiCallLog, SystemState
)
from app.services.pricing_engine import apply_rate_shock
from app.services.order_service import place_secondary_order, credit_bond_coupon, mature_bond_holding
from app.services.webhook_service import trigger_holdings_updated_event
from app.services.seed_data import seed_database
from app.templates_engine import templates

router = APIRouter(prefix="/admin", tags=["Admin"])

def is_admin_authenticated(request: Request) -> bool:
    return request.cookies.get("bb_admin_auth") == "true"

@router.get("", response_class=HTMLResponse)
@router.get("/", response_class=HTMLResponse)
async def admin_dashboard(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return templates.TemplateResponse("admin.html", {
            "request": request,
            "is_logged_in": False
        })

    state = db.query(SystemState).first()
    users = db.query(User).all()
    instruments = db.query(Instrument).filter(Instrument.is_active == True).all()
    consents = db.query(ApiConsent).order_by(ApiConsent.created_at.desc()).all()
    webhook_logs = db.query(WebhookLog).order_by(WebhookLog.created_at.desc()).limit(30).all()
    api_call_logs = db.query(ApiCallLog).order_by(ApiCallLog.timestamp.desc()).limit(30).all()
    primary_issues = db.query(PrimaryIssue).all()
    primary_apps = db.query(PrimaryApplication).all()

    return templates.TemplateResponse("admin.html", {
        "request": request,
        "is_logged_in": True,
        "state": state,
        "users": users,
        "instruments": instruments,
        "consents": consents,
        "webhook_logs": webhook_logs,
        "api_call_logs": api_call_logs,
        "primary_issues": primary_issues,
        "primary_apps": primary_apps
    })

@router.post("/login")
async def admin_login(request: Request, response: Response, password: str = Form(...)):
    if password == settings.ADMIN_PASSWORD:
        resp = RedirectResponse(url="/admin", status_code=303)
        resp.set_cookie("bb_admin_auth", "true", max_age=86400, httponly=True)
        return resp
    return templates.TemplateResponse("admin.html", {
        "request": request,
        "is_logged_in": False,
        "error_message": "Invalid admin password."
    })

@router.post("/logout")
async def admin_logout():
    resp = RedirectResponse(url="/admin", status_code=303)
    resp.delete_cookie("bb_admin_auth")
    return resp

@router.post("/rate-shock")
async def admin_rate_shock(
    request: Request,
    bps_shift: float = Form(...),
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    apply_rate_shock(db, bps_shift)
    return RedirectResponse(url="/admin?msg=Rate+shock+applied", status_code=303)

@router.post("/toggle-market")
async def admin_toggle_market(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    state = db.query(SystemState).first()
    if state:
        state.is_market_open = not state.is_market_open
        db.commit()
    return RedirectResponse(url="/admin?msg=Market+state+updated", status_code=303)

@router.post("/toggle-outage")
async def admin_toggle_outage(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    state = db.query(SystemState).first()
    if state:
        state.simulate_outage = not state.simulate_outage
        if state.simulate_outage:
            state.outage_until = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=5, minutes=30))) + timedelta(seconds=60)
        else:
            state.outage_until = None
        db.commit()
    return RedirectResponse(url="/admin?msg=Outage+simulation+toggled", status_code=303)

@router.post("/toggle-slow-mode")
async def admin_toggle_slow(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    state = db.query(SystemState).first()
    if state:
        state.slow_mode = not state.slow_mode
        db.commit()
    return RedirectResponse(url="/admin?msg=Slow+mode+toggled", status_code=303)

@router.post("/simulate-trade")
async def admin_simulate_trade(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Form(...),
    instrument_id: int = Form(...),
    side: str = Form(...),
    units: int = Form(...),
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    user = db.query(User).filter(User.id == user_id).first()
    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if user and inst:
        place_secondary_order(
            db, user, inst, side, "MARKET", units, background_tasks=background_tasks
        )
    return RedirectResponse(url="/admin?msg=Trade+simulated", status_code=303)

@router.post("/credit-coupon")
async def admin_credit_coupon(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Form(...),
    instrument_id: int = Form(...),
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    user = db.query(User).filter(User.id == user_id).first()
    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if user and inst:
        credit_bond_coupon(db, user, inst, background_tasks=background_tasks)
    return RedirectResponse(url="/admin?msg=Coupon+credited", status_code=303)

@router.post("/mature-bond")
async def admin_mature_bond(
    request: Request,
    background_tasks: BackgroundTasks,
    user_id: int = Form(...),
    instrument_id: int = Form(...),
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    user = db.query(User).filter(User.id == user_id).first()
    inst = db.query(Instrument).filter(Instrument.id == instrument_id).first()
    if user and inst:
        mature_bond_holding(db, user, inst, background_tasks=background_tasks)
    return RedirectResponse(url="/admin?msg=Bond+matured", status_code=303)

@router.post("/downgrade-issuer")
async def admin_downgrade_issuer(
    request: Request,
    issuer_name: str = Form(...),
    new_rating: str = Form(...),
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    insts = db.query(Instrument).filter(Instrument.issuer_name == issuer_name).all()
    for inst in insts:
        inst.credit_rating = new_rating
        # Credit spread widens on downgrade
        inst.benchmark_spread_bps += 75.0
        from app.services.pricing_engine import recalculate_instrument_prices
        state = db.query(SystemState).first()
        recalculate_instrument_prices(db, inst, state.benchmark_yield, date.today())
    db.commit()
    return RedirectResponse(url="/admin?msg=Issuer+downgraded", status_code=303)

@router.post("/consent-action")
async def admin_consent_action(
    request: Request,
    consent_id: str = Form(...),
    action: str = Form(...), # "expire" or "revoke"
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    consent = db.query(ApiConsent).filter(ApiConsent.consent_id == consent_id).first()
    if consent:
        if action == "expire":
            consent.status = "EXPIRED"
            consent.expires_at = datetime.datetime.now(datetime.timezone.utc) - timedelta(days=1)
        elif action == "revoke":
            consent.status = "REVOKED"
        db.commit()
    return RedirectResponse(url="/admin?msg=Consent+updated", status_code=303)

@router.post("/allot-primary")
async def admin_allot_primary(
    request: Request,
    issue_id: int = Form(...),
    background_tasks: BackgroundTasks = None,
    db: Session = Depends(get_db)
):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    
    issue = db.query(PrimaryIssue).filter(PrimaryIssue.id == issue_id).first()
    if issue:
        apps = db.query(PrimaryApplication).filter(
            PrimaryApplication.primary_issue_id == issue.id,
            PrimaryApplication.status == "SUBMITTED"
        ).all()
        for app in apps:
            app.status = "ALLOTTED"
            # If matching instrument exists, add holding
            inst = db.query(Instrument).filter(Instrument.name.like(f"%{issue.issuer_name[:10]}%")).first()
            if inst:
                holding = db.query(Holding).filter(
                    Holding.user_id == app.user_id,
                    Holding.instrument_id == inst.id
                ).first()
                if holding:
                    holding.units += app.lots
                else:
                    import uuid
                    db.add(Holding(
                        holding_id=f"hld_{uuid.uuid4().hex[:8]}",
                        user_id=app.user_id,
                        instrument_id=inst.id,
                        units=app.lots,
                        avg_purchase_price=inst.clean_price,
                        avg_purchase_yield=inst.current_ytm
                    ))
                trigger_holdings_updated_event(db, app.user, background_tasks)
        issue.status = "ALLOTTED"
        db.commit()

    return RedirectResponse(url="/admin?msg=Primary+allotment+processed", status_code=303)

@router.post("/reset-data")
async def admin_reset_data(request: Request, db: Session = Depends(get_db)):
    if not is_admin_authenticated(request):
        return RedirectResponse(url="/admin", status_code=303)
    seed_database(db, force_reset=True)
    return RedirectResponse(url="/admin?msg=All+data+reset+to+seed+state", status_code=303)
