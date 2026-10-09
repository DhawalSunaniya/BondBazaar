"""
TradeOne outbox: reliable transactional event delivery to TradeOne aggregator.

Events:
  POST {TRADEONE_URL}/internal/v1/events
  Header: x-internal-key: {INTERNAL_API_KEY}
  Body: {"provider":"c","email":"...","event":"HOLDINGS_CHANGED","occurredAt":"..."}

Guarantees:
- OutboxEvent row inserted in the SAME database transaction as the trade/holding change.
- Background worker POSTs with header x-internal-key.
- Retries with exponential backoff up to 10 attempts (1s, 2s, 4s ... up to 60s).
- Never blocks or fails trade execution.
- All delivery attempts and results are logged.
"""

import asyncio
import datetime
import logging
import uuid
from typing import Optional, List, Dict, Any
from sqlalchemy.orm import Session
import httpx

from app.config import settings
from app.models.entities import OutboxEvent

logger = logging.getLogger("bondbazaar.outbox")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_TRADEONE_URL: Optional[str] = getattr(settings, "TRADEONE_URL", None)
_MAX_RETRIES = 10
_BASE_DELAY = 1.0     # seconds
_MAX_DELAY = 60.0     # seconds


def create_outbox_event(
    db: Session,
    email: str,
    event: str = "HOLDINGS_CHANGED",
    provider: str = "c",
) -> OutboxEvent:
    """
    Inserts an OutboxEvent row in the current DB transaction.
    Must be committed along with the trade/holding change.
    """
    occurred_at = datetime.datetime.now(IST).replace(microsecond=0).isoformat()
    evt = OutboxEvent(
        event_id=f"evt_{uuid.uuid4().hex[:12]}",
        email=email.strip().lower(),
        provider=provider,
        event=event,
        occurred_at=occurred_at,
        status="PENDING",
        attempts=0,
    )
    db.add(evt)
    return evt


async def _deliver(payload: dict) -> None:
    """Delivery attempt loop with exponential backoff for in-memory or legacy payloads."""
    tradeone_url = getattr(settings, "TRADEONE_URL", "") or _TRADEONE_URL
    if not tradeone_url:
        logger.debug("TRADEONE_URL not configured — outbox event skipped.")
        return

    url = f"{tradeone_url.rstrip('/')}/internal/v1/events"
    headers = {
        "x-internal-key": getattr(settings, "INTERNAL_API_KEY", ""),
        "Content-Type": "application/json",
    }
    delay = _BASE_DELAY

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload, headers=headers)
                if 200 <= resp.status_code < 300:
                    logger.info(
                        "Outbox event delivered to TradeOne (attempt %d/%d): %s (status %d)",
                        attempt, _MAX_RETRIES, payload.get("event"), resp.status_code,
                    )
                    return
                logger.warning(
                    "TradeOne returned %d on attempt %d/%d for event %s: %s",
                    resp.status_code, attempt, _MAX_RETRIES, payload.get("event"), resp.text[:100],
                )
        except Exception as exc:
            logger.warning(
                "TradeOne delivery error attempt %d/%d for event %s: %s",
                attempt, _MAX_RETRIES, payload.get("event"), exc,
            )

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(min(delay, _MAX_DELAY))
            delay *= 2

    logger.error(
        "Outbox gave up after %d attempts for event %s (email=%s)",
        _MAX_RETRIES, payload.get("event"), payload.get("email"),
    )


async def deliver_outbox_event(event_id: str) -> None:
    """
    Background delivery worker for a specific DB outbox event.
    Logs each attempt and updates OutboxEvent status/attempts/last_error in DB.
    """
    from app.database import SessionLocal
    tradeone_url = getattr(settings, "TRADEONE_URL", "") or _TRADEONE_URL
    
    db = SessionLocal()
    try:
        evt = db.query(OutboxEvent).filter(OutboxEvent.event_id == event_id).first()
        if not evt or evt.status == "DELIVERED":
            return
        payload = {
            "provider": evt.provider,
            "email": evt.email,
            "event": evt.event,
            "occurredAt": evt.occurred_at,
        }
    finally:
        db.close()

    if not tradeone_url:
        logger.info("TRADEONE_URL not configured — outbox delivery recorded as pending for %s", event_id)
        db = SessionLocal()
        try:
            evt = db.query(OutboxEvent).filter(OutboxEvent.event_id == event_id).first()
            if evt:
                evt.last_error = "TRADEONE_URL not configured"
                evt.last_attempt_at = datetime.datetime.now(IST)
                db.commit()
        finally:
            db.close()
        return

    url = f"{tradeone_url.rstrip('/')}/internal/v1/events"
    headers = {
        "x-internal-key": getattr(settings, "INTERNAL_API_KEY", ""),
        "Content-Type": "application/json",
    }
    delay = _BASE_DELAY

    for attempt in range(1, _MAX_RETRIES + 1):
        db = SessionLocal()
        try:
            evt = db.query(OutboxEvent).filter(OutboxEvent.event_id == event_id).first()
            if not evt:
                return
            if evt.status == "DELIVERED":
                return

            evt.attempts = attempt
            evt.last_attempt_at = datetime.datetime.now(IST)

            success = False
            try:
                async with httpx.AsyncClient(timeout=5.0) as client:
                    resp = await client.post(url, json=payload, headers=headers)
                    if 200 <= resp.status_code < 300:
                        evt.status = "DELIVERED"
                        evt.last_error = None
                        db.commit()
                        logger.info(
                            "Outbox event %s delivered to TradeOne (attempt %d/%d): status %d",
                            event_id, attempt, _MAX_RETRIES, resp.status_code,
                        )
                        success = True
                    else:
                        evt.last_error = f"HTTP {resp.status_code}: {resp.text[:120]}"
                        logger.warning(
                            "TradeOne delivery HTTP %d on attempt %d/%d for %s",
                            resp.status_code, attempt, _MAX_RETRIES, event_id,
                        )
            except Exception as exc:
                evt.last_error = str(exc)[:200]
                logger.warning(
                    "TradeOne delivery exception attempt %d/%d for %s: %s",
                    attempt, _MAX_RETRIES, event_id, exc,
                )

            if success:
                return

            if attempt == _MAX_RETRIES:
                evt.status = "FAILED"

            db.commit()
        finally:
            db.close()

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(min(delay, _MAX_DELAY))
            delay *= 2


def trigger_outbox_delivery(event_id: str) -> None:
    """Schedules background delivery task for a committed outbox event."""
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(deliver_outbox_event(event_id))
    except RuntimeError:
        logger.debug("No active event loop for outbox delivery of %s", event_id)


def enqueue_holdings_changed(email: str) -> None:
    """
    Enqueues a HOLDINGS_CHANGED outbox event for the given email.
    Schedules an asyncio task; never blocks the caller.
    """
    occurred_at = datetime.datetime.now(IST).replace(microsecond=0).isoformat()
    payload = {
        "provider": "c",
        "email": email.strip().lower(),
        "event": "HOLDINGS_CHANGED",
        "occurredAt": occurred_at,
    }
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_deliver(payload))
    except RuntimeError:
        logger.debug("No event loop; outbox event not scheduled.")


def resend_all_outbox_events(db: Session) -> int:
    """
    Resets all non-delivered (or failed) outbox events to PENDING and triggers delivery.
    Returns the count of events rescheduled.
    """
    events = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.status.in_(["FAILED", "PENDING"]))
        .all()
    )
    count = 0
    for evt in events:
        evt.status = "PENDING"
        evt.attempts = 0
        evt.last_error = None
        count += 1
        trigger_outbox_delivery(evt.event_id)
    db.commit()
    return count


async def outbox_background_worker() -> None:
    """Periodic background worker that processes any lingering PENDING events."""
    from app.database import SessionLocal
    while True:
        try:
            await asyncio.sleep(30)
            db = SessionLocal()
            try:
                pending = (
                    db.query(OutboxEvent)
                    .filter(OutboxEvent.status == "PENDING")
                    .limit(20)
                    .all()
                )
                for evt in pending:
                    asyncio.create_task(deliver_outbox_event(evt.event_id))
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.error("Outbox background worker error: %s", exc)
            await asyncio.sleep(5)
