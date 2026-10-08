from fastapi import APIRouter, Depends, Request, Form, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
import uuid

from app.database import get_db
from app.models.entities import User
from app.services.auth_service import generate_otp_for_email, verify_otp_for_email
from app.templates_engine import templates

router = APIRouter(tags=["Authentication"])

def get_current_user_optional(request: Request, db: Session) -> User:
    email = request.cookies.get("bb_session_email")
    if not email:
        # Default to Aarav Mehta for smooth exploration if not logged in
        return db.query(User).filter(User.email == "aarav.mehta@example.com").first()
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
    clean_email = email.strip().lower()
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
    clean_email = email.strip().lower()
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
    clean_email = email.strip().lower()
    existing = db.query(User).filter(User.email == clean_email).first()
    if existing:
        return templates.TemplateResponse("register.html", {
            "request": request,
            "error_message": "An account with this email already exists."
        })

    # Generate customer ID e.g. BB-91823
    cid = f"BB-{uuid.uuid4().int % 90000 + 10000}"
    pan_clean = pan.strip().upper()
    pan_masked = f"{pan_clean[:5]}****{pan_clean[-1]}" if len(pan_clean) >= 6 else "ABCDE****F"
    demat_clean = demat_account.strip()
    demat_masked = f"{demat_clean[:8]}****{demat_clean[-4:]}" if len(demat_clean) >= 12 else "12081600****1234"

    user = User(
        email=clean_email,
        full_name=full_name.strip(),
        mobile=mobile.strip(),
        customer_id=cid,
        pan=pan_clean,
        pan_masked=pan_masked,
        demat_account=demat_clean,
        demat_account_masked=demat_masked,
        wallet_balance=250000.0
    )
    db.add(user)
    db.commit()

    resp = RedirectResponse(url="/", status_code=303)
    resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.get("/switch-user/{target_email}")
async def switch_user(target_email: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == target_email).first()
    resp = RedirectResponse(url="/", status_code=303)
    if user:
        resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.get("/logout")
async def logout_action():
    resp = RedirectResponse(url="/login", status_code=303)
    resp.delete_cookie("bb_session_email")
    return resp
