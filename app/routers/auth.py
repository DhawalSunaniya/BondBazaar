from fastapi import APIRouter, Depends, Request, Form, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from typing import Optional
import uuid

from app.database import get_db
from app.models.entities import User
from app.services.auth_service import generate_otp_for_email, verify_otp_for_email
from app.templates_engine import templates
from app.shared_identity import normalize_email, generate_identity, full_name_from_claims
from app.services.portfolio_provisioner import provision_user_portfolio

router = APIRouter(tags=["Authentication"])

def get_current_user_optional(request: Request, db: Session) -> User:
    email_raw = request.cookies.get("bb_session_email")
    if not email_raw:
        # Default to Aarav Mehta for smooth exploration if not logged in
        return db.query(User).filter(User.email == "aarav.mehta@example.com").first()
    email = normalize_email(email_raw)
    user = db.query(User).filter(User.email == email).first()
    return user or db.query(User).filter(User.email == "aarav.mehta@example.com").first()

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {
        "request": request,
        "otp_sent": False
    })

@router.post("/send-otp")
async def send_otp_action(request: Request, email: str = Form(...), db: Session = Depends(get_db)):
    clean_email = normalize_email(email)
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        return templates.TemplateResponse("login.html", {
            "request": request,
            "email": clean_email,
            "otp_sent": False,
            "error_message": f"Email '{clean_email}' not found. Please register or use demo users (aarav.mehta@example.com / priya.nair@example.com)."
        })

    generate_otp_for_email(clean_email)
    return templates.TemplateResponse("login.html", {
        "request": request,
        "email": clean_email,
        "otp_sent": True,
        "success_message": "Demo OTP '123456' has been generated. Enter it below to sign in."
    })

@router.post("/verify-otp")
async def verify_otp_action(
    request: Request,
    response: Response,
    email: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    clean_email = normalize_email(email)
    if not verify_otp_for_email(clean_email, otp):
        return templates.TemplateResponse("login.html", {
            "request": request,
            "email": clean_email,
            "otp_sent": True,
            "error_message": "Invalid OTP. Use demo OTP 123456."
        })

    user = db.query(User).filter(User.email == clean_email).first()
    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request})

@router.post("/register")
async def register_action(
    request: Request,
    full_name: str = Form(...),
    email: str = Form(...),
    mobile: str = Form(...),
    pan: str = Form(...),
    demat_account: str = Form(...),
    db: Session = Depends(get_db)
):
    clean_email = normalize_email(email)
    existing = db.query(User).filter(User.email == clean_email).first()
    if existing:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error_message": "An account with this email already exists."
        })

    identity = generate_identity(clean_email)
    display_name = full_name_from_claims(full_name, clean_email)

    user = User(
        email=clean_email,
        full_name=display_name,
        mobile=mobile.strip() or identity["mobile"],
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

    # Assign deterministic starter portfolio (Provider-C theme: G-Secs + SGBs)
    provision_user_portfolio(db, user)

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.post("/auth/google")
async def google_login_action(
    request: Request,
    email: str = Form(...),
    name: Optional[str] = Form(None),
    db: Session = Depends(get_db)
):
    """Google sign-in matching provisioned users by normalized email."""
    clean_email = normalize_email(email)
    user = db.query(User).filter(User.email == clean_email).first()
    if not user:
        identity = generate_identity(clean_email)
        display_name = full_name_from_claims(name, clean_email)
        user = User(
            email=clean_email,
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
            wallet_balance=1_000_000.0,
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        provision_user_portfolio(db, user)

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.get("/switch-user/{target_email}")
async def switch_user(target_email: str, db: Session = Depends(get_db)):
    clean = normalize_email(target_email)
    user = db.query(User).filter(User.email == clean).first()
    resp = RedirectResponse(url="/", status_code=303)
    if user:
        resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.get("/logout")
async def logout_action():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("bb_session_email")
    return resp

