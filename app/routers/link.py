import json
import secrets
import datetime
from typing import Optional
from fastapi import APIRouter, Depends, Request, Form, Response, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.entities import LinkToken, User
from app.services.auth_service import verify_otp_for_email, generate_otp_for_email
from app.templates_engine import templates

router = APIRouter(tags=["Link Flow"])

@router.get("/link", response_class=HTMLResponse)
async def link_page(request: Request, token: str, db: Session = Depends(get_db)):
    token_rec = db.query(LinkToken).filter(LinkToken.link_token == token).first()
    if not token_rec or token_rec.is_used:
        return templates.TemplateResponse("link_consent.html", {
            "request": request,
            "error_message": "Invalid or expired Link token. Please initiate connection again from the aggregator app."
        })

    # Check if user session exists in cookie
    user_email = request.cookies.get("bb_session_email")
    user = db.query(User).filter(User.email == user_email).first() if user_email else None

    scopes_list = json.loads(token_rec.scopes) if token_rec.scopes else ["holdings", "profile"]

    return templates.TemplateResponse("link_consent.html", {
        "request": request,
        "token": token,
        "client_name": token_rec.client_name,
        "scopes": scopes_list,
        "redirect_uri": token_rec.redirect_uri,
        "user": user,
        "expires_in_days": 90,
        "otp_sent": False
    })

@router.post("/link/send-otp")
async def link_send_otp(
    request: Request,
    token: str = Form(...),
    email: str = Form(...),
    db: Session = Depends(get_db)
):
    token_rec = db.query(LinkToken).filter(LinkToken.link_token == token).first()
    if not token_rec:
        return RedirectResponse(url=f"/link?token={token}&error=Invalid+token", status_code=303)

    user = db.query(User).filter(User.email == email.strip().lower()).first()
    if not user:
        return templates.TemplateResponse("link_consent.html", {
            "request": request,
            "token": token,
            "client_name": token_rec.client_name,
            "scopes": json.loads(token_rec.scopes) if token_rec.scopes else [],
            "error_message": f"Email '{email}' is not registered on BondBazaar. Try 'aarav.mehta@example.com'."
        })

    generate_otp_for_email(email.strip().lower())
    scopes_list = json.loads(token_rec.scopes) if token_rec.scopes else []

    return templates.TemplateResponse("link_consent.html", {
        "request": request,
        "token": token,
        "client_name": token_rec.client_name,
        "scopes": scopes_list,
        "email": email.strip().lower(),
        "otp_sent": True,
        "success_message": "Demo OTP '123456' has been generated for your login."
    })

@router.post("/link/verify-login")
async def link_verify_login(
    request: Request,
    response: Response,
    token: str = Form(...),
    email: str = Form(...),
    otp: str = Form(...),
    db: Session = Depends(get_db)
):
    token_rec = db.query(LinkToken).filter(LinkToken.link_token == token).first()
    if not token_rec:
        return RedirectResponse(url=f"/link?token={token}", status_code=303)

    if not verify_otp_for_email(email, otp):
        scopes_list = json.loads(token_rec.scopes) if token_rec.scopes else []
        return templates.TemplateResponse("link_consent.html", {
            "request": request,
            "token": token,
            "client_name": token_rec.client_name,
            "scopes": scopes_list,
            "email": email,
            "otp_sent": True,
            "error_message": "Invalid OTP. For demo access, use 123456."
        })

    user = db.query(User).filter(User.email == email.strip().lower()).first()
    scopes_list = json.loads(token_rec.scopes) if token_rec.scopes else []

    resp = templates.TemplateResponse("link_consent.html", {
        "request": request,
        "token": token,
        "client_name": token_rec.client_name,
        "scopes": scopes_list,
        "user": user,
        "expires_in_days": 90,
        "otp_sent": False
    })
    resp.set_cookie("bb_session_email", user.email, max_age=86400 * 30, httponly=True)
    return resp

@router.post("/link/authorize")
async def link_authorize(
    request: Request,
    token: str = Form(...),
    action: str = Form(...), # "allow" or "deny"
    db: Session = Depends(get_db)
):
    token_rec = db.query(LinkToken).filter(LinkToken.link_token == token).first()
    if not token_rec or token_rec.is_used:
        return RedirectResponse(url=f"/link?token={token}&error=Token+expired", status_code=303)

    user_email = request.cookies.get("bb_session_email")
    user = db.query(User).filter(User.email == user_email).first()
    if not user:
        return RedirectResponse(url=f"/link?token={token}", status_code=303)

    if action == "deny":
        redirect_target = f"{token_rec.redirect_uri}?error=access_denied&link_session={token_rec.link_token}"
        return RedirectResponse(url=redirect_target, status_code=303)

    # Allow action
    pub_token = f"pub_{secrets.token_hex(16)}"
    link_session = f"lsess_{secrets.token_hex(16)}"

    token_rec.user_id = user.id
    token_rec.public_token = pub_token
    token_rec.link_session = link_session
    db.commit()

    sep = "&" if "?" in token_rec.redirect_uri else "?"
    redirect_target = f"{token_rec.redirect_uri}{sep}public_token={pub_token}&link_session={link_session}"

    return templates.TemplateResponse("link_consent.html", {
        "request": request,
        "authorization_complete": True,
        "redirect_url": redirect_target,
        "public_token": pub_token,
        "link_session": link_session,
        "client_name": token_rec.client_name
    })
