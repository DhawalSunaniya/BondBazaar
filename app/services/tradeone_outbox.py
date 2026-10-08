"""
TradeOne outbox: fire-and-forget async event delivery to TradeOne aggregator.

POST {TRADEONE_URL}/internal/v1/events
Body: {"provider":"c","email":"...","event":"HOLDINGS_CHANGED","occurredAt":"..."}

Retry up to 10 times with exponential backoff (1s, 2s, 4s … capped at 60s).
Never blocks or fails the caller — all errors are logged, not raised.
"""

import asyncio
import datetime
import logging
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger("bondbazaar.outbox")

IST = datetime.timezone(datetime.timedelta(hours=5, minutes=30))

_TRADEONE_URL: Optional[str] = getattr(settings, "TRADEONE_URL", None)
_MAX_RETRIES = 10
_BASE_DELAY = 1.0     # seconds
_MAX_DELAY = 60.0     # seconds


async def _deliver(payload: dict) -> None:
    """Single delivery attempt loop with exponential backoff."""
    if not _TRADEONE_URL:
        logger.debug("TRADEONE_URL not configured — outbox event skipped.")
        return

    url = f"{_TRADEONE_URL.rstrip('/')}/internal/v1/events"
    delay = _BASE_DELAY

    for attempt in range(1, _MAX_RETRIES + 1):
        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.post(url, json=payload)
                if resp.status_code < 300:
                    logger.info(
                        "Outbox event delivered to TradeOne (attempt %d): %s",
                        attempt, payload.get("event"),
                    )
                    return
                logger.warning(
                    "TradeOne returned %d on attempt %d for event %s",
                    resp.status_code, attempt, payload.get("event"),
                )
        except Exception as exc:
            logger.warning(
                "TradeOne delivery error attempt %d for event %s: %s",
                attempt, payload.get("event"), exc,
            )

        if attempt < _MAX_RETRIES:
            await asyncio.sleep(min(delay, _MAX_DELAY))
            delay *= 2

    logger.error(
        "Outbox gave up after %d attempts for event %s (email=%s)",
        _MAX_RETRIES, payload.get("event"), payload.get("email"),
    )


def enqueue_holdings_changed(email: str) -> None:
    """
    Enqueues a HOLDINGS_CHANGED outbox event for the given email.
    Fire-and-forget: schedules an asyncio task; never blocks the caller.
    """
    occurred_at = datetime.datetime.now(IST).replace(microsecond=0).isoformat()
    payload = {
        "provider": "c",
        "email": email,
        "event": "HOLDINGS_CHANGED",
        "occurredAt": occurred_at,
    }
    try:
        loop = asyncio.get_event_loop()
        loop.create_task(_deliver(payload))
    except RuntimeError:
        # No running event loop (e.g. in tests) — silently skip
        logger.debug("No event loop; outbox event not scheduled.")
