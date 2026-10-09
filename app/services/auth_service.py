import base64
import hashlib
import secrets
import datetime
from datetime import timedelta
from typing import Optional, Tuple, Dict, Any, List
from sqlalchemy.orm import Session
from fastapi import Request, HTTPException, status

from app.config import settings
from app.models.entities import User, ApiClient, LinkToken, ApiConsent

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

# In-memory OTP storage for realism: {email: {"otp": "123456", "expires_at": datetime}}
ACTIVE_OTPS: Dict[str, Dict[str, Any]] = {}

def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()

def verify_secret(secret: str, hashed: str) -> bool:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest() == hashed

def generate_otp_for_email(email: str) -> str:
    # Always 123456 in demo mode
    otp = settings.DEMO_OTP
    exp = datetime.datetime.now(IST) + timedelta(minutes=10)
    ACTIVE_OTPS[email.lower()] = {"otp": otp, "expires_at": exp}
    return otp

def verify_otp_for_email(email: str, entered_otp: str) -> bool:
    email_clean = email.lower()
    # In demo mode, 123456 is always accepted
    if entered_otp.strip() == settings.DEMO_OTP:
        return True
    
    data = ACTIVE_OTPS.get(email_clean)
    if not data:
        return False
    if datetime.datetime.now(IST) > data["expires_at"]:
        return False
    return data["otp"] == entered_otp.strip()

def parse_basic_auth(auth_header: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    if not auth_header or not auth_header.startswith("Basic "):
        return None, None
    try:
        encoded = auth_header.split(" ", 1)[1]
        decoded = base64.b64decode(encoded).decode("utf-8")
        if ":" in decoded:
            app_id, app_secret = decoded.split(":", 1)
            return app_id.strip(), app_secret.strip()
    except Exception:
        pass
    return None, None

def authenticate_api_client(db: Session, auth_header: Optional[str]) -> Optional[ApiClient]:
    app_id, app_secret = parse_basic_auth(auth_header)
    if not app_id or not app_secret:
        return None

    client = db.query(ApiClient).filter(ApiClient.client_id == app_id, ApiClient.is_active == True).first()
    if not client:
        return None

    try:
        if verify_secret(app_secret, client.client_secret_hash):
            return client
    except Exception:
        pass

    return None

def create_link_token_record(
    db: Session,
    client: ApiClient,
    scopes: List[str],
    redirect_uri: str,
    webhook_url: Optional[str],
    request_host: str
) -> Dict[str, Any]:
    link_token = f"lnk_{secrets.token_hex(16)}"
    expires_at = datetime.datetime.now(IST) + timedelta(minutes=30)

    import json
    token_rec = LinkToken(
        link_token=link_token,
        client_id=client.client_id,
        client_name=client.client_name,
        scopes=json.dumps(scopes),
        redirect_uri=redirect_uri,
        webhook_url=webhook_url,
        expires_at=expires_at,
        is_used=False
    )
    db.add(token_rec)
    db.commit()

    link_url = f"{request_host}/link?token={link_token}"
    return {
        "linkToken": link_token,
        "expiresAt": expires_at.replace(microsecond=0).isoformat(),
        "linkUrl": link_url
    }

def exchange_public_token_for_access(
    db: Session,
    client: ApiClient,
    public_token: str
) -> Tuple[bool, Optional[Dict[str, Any]], Optional[str]]:
    token_rec = db.query(LinkToken).filter(
        LinkToken.public_token == public_token,
        LinkToken.client_id == client.client_id
    ).first()

    if not token_rec:
        return False, None, "Invalid or unrecognized public token."

    if token_rec.is_used:
        return False, None, "Public token has already been exchanged."

    if not token_rec.user_id:
        return False, None, "Link session was not authorized by user."

    user = db.query(User).filter(User.id == token_rec.user_id).first()
    if not user:
        return False, None, "User not found."

    raw_access_token = f"acc_{secrets.token_hex(20)}"
    token_hash = hash_secret(raw_access_token)
    consent_id = f"cns_{secrets.token_hex(10)}"
    expires_at = datetime.datetime.now(IST) + timedelta(days=settings.ACCESS_TOKEN_EXPIRY_DAYS)

    consent = ApiConsent(
        consent_id=consent_id,
        client_id=client.client_id,
        user_id=user.id,
        scopes=token_rec.scopes,
        access_token_hash=token_hash,
        access_token_prefix=raw_access_token[:10],
        expires_at=expires_at,
        status="ACTIVE",
        webhook_url=token_rec.webhook_url
    )
    db.add(consent)

    token_rec.is_used = True
    db.commit()

    return True, {
        "accessToken": raw_access_token,
        "consentId": consent_id,
        "consentExpiresAt": expires_at.replace(microsecond=0).isoformat(),
        "customerId": user.customer_id
    }, None

def authenticate_bearer_token(db: Session, auth_header: Optional[str]) -> Tuple[Optional[User], Optional[ApiConsent], Optional[str]]:
    """
    Validates Bearer acc_...
    Returns (User, ApiConsent, error_code)
    Error codes: 'INVALID_TOKEN', 'CONSENT_EXPIRED', 'CONSENT_REVOKED'.
    """
    if not auth_header or not auth_header.startswith("Bearer "):
        return None, None, "INVALID_TOKEN"

    raw_token = auth_header.split(" ", 1)[1].strip()
    token_hash = hash_secret(raw_token)

    consent = db.query(ApiConsent).filter(ApiConsent.access_token_hash == token_hash).first()
    if not consent:
        return None, None, "INVALID_TOKEN"

    if consent.status == "REVOKED":
        return None, consent, "CONSENT_REVOKED"

    now = datetime.datetime.now(datetime.timezone.utc).replace(tzinfo=None)
    exp = consent.expires_at
    if exp and exp.tzinfo is not None:
        exp = exp.astimezone(datetime.timezone.utc).replace(tzinfo=None)

    if consent.status == "EXPIRED" or (exp and exp < now):
        consent.status = "EXPIRED"
        db.commit()
        return None, consent, "CONSENT_EXPIRED"

    user = db.query(User).filter(User.id == consent.user_id).first()
    if not user:
        return None, consent, "INVALID_TOKEN"

    return user, consent, None
