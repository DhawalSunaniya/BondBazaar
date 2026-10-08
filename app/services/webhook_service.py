import hmac
import hashlib
import json
import datetime
import asyncio
import httpx
from typing import Optional
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.entities import ApiConsent, WebhookLog, User

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

def compute_hmac_signature(secret: str, payload_bytes: bytes) -> str:
    """Computes HMAC-SHA256 signature."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()

def verify_hmac_signature(secret: str, payload_bytes: bytes, signature_header: str) -> bool:
    """Verifies HMAC signature matching."""
    expected = compute_hmac_signature(secret, payload_bytes)
    # Support both bare hex or 'sha256=...' prefix
    clean_sig = signature_header.replace("sha256=", "").strip()
    return hmac.compare_digest(expected, clean_sig)

async def deliver_webhook_with_retry(log_id: int, webhook_url: str, payload_str: str, secret: str):
    payload_bytes = payload_str.encode("utf-8")
    sig = compute_hmac_signature(secret, payload_bytes)
    headers = {
        "Content-Type": "application/json",
        "X-BB-Signature": sig,
        "User-Agent": f"{settings.PLATFORM_NAME}-Webhook/1.0"
    }

    max_attempts = 3
    final_status = "FAILED"
    final_code = None
    final_err = None

    for attempt in range(1, max_attempts + 1):
        try:
            async with httpx.AsyncClient(timeout=4.0) as client:
                res = await client.post(webhook_url, content=payload_bytes, headers=headers)
                final_code = res.status_code
                if 200 <= res.status_code < 300:
                    final_status = "SUCCESS"
                    final_err = None
                    break
                else:
                    final_err = f"HTTP {res.status_code}: {res.text[:100]}"
        except Exception as e:
            final_err = str(e)

        if attempt < max_attempts:
            await asyncio.sleep(attempt * 1.0) # Backoff 1s, 2s

    # Update database log
    db = SessionLocal()
    try:
        log_entry = db.query(WebhookLog).filter(WebhookLog.id == log_id).first()
        if log_entry:
            log_entry.attempt_count = attempt
            log_entry.status_code = final_code
            log_entry.status = final_status
            log_entry.error_message = final_err
            db.commit()
    finally:
        db.close()

def trigger_holdings_updated_event(db: Session, user: User, background_tasks = None):
    """
    Finds active API consents for this user with a webhook_url,
    creates WebhookLog records, and dispatches webhook tasks.
    """
    consents = db.query(ApiConsent).filter(
        ApiConsent.user_id == user.id,
        ApiConsent.status == "ACTIVE"
    ).all()

    occurred_at = datetime.datetime.now(IST).replace(microsecond=0).isoformat()

    for consent in consents:
        if not consent.webhook_url:
            continue

        payload_obj = {
            "event": "HOLDINGS_UPDATED",
            "consentId": consent.consent_id,
            "customerId": user.customer_id,
            "occurredAt": occurred_at
        }
        payload_str = json.dumps(payload_obj)

        log_entry = WebhookLog(
            event="HOLDINGS_UPDATED",
            consent_id=consent.consent_id,
            customer_id=user.customer_id,
            payload=payload_str,
            webhook_url=consent.webhook_url,
            attempt_count=0,
            status="PENDING"
        )
        db.add(log_entry)
        db.commit()
        db.refresh(log_entry)

        if background_tasks:
            background_tasks.add_task(
                deliver_webhook_with_retry,
                log_entry.id,
                consent.webhook_url,
                payload_str,
                settings.WEBHOOK_SIGNING_SECRET
            )
        else:
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(deliver_webhook_with_retry(
                    log_entry.id,
                    consent.webhook_url,
                    payload_str,
                    settings.WEBHOOK_SIGNING_SECRET
                ))
            except RuntimeError:
                pass
