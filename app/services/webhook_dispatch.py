"""Webhook dispatch — fire HTTP notifications for tenant events."""

import hashlib
import hmac
import json
import logging
import uuid

import httpx
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.models.webhook import Webhook

logger = logging.getLogger(__name__)


async def dispatch_webhook_event(
    session: AsyncSession,
    tenant_id: str,
    event_type: str,
    payload: dict,
) -> None:
    """Fire webhooks for a tenant+event. Never raises."""
    try:
        stmt = select(Webhook).where(
            Webhook.tenant_id == uuid.UUID(tenant_id),
            Webhook.is_active.is_(True),
        )
        result = await session.execute(stmt)
        webhooks = result.scalars().all()

        for wh in webhooks:
            events = json.loads(wh.events)
            if event_type not in events:
                continue
            await _send_webhook(wh, event_type, payload)
    except Exception:
        logger.exception(
            "Webhook dispatch failed for tenant %s event %s", tenant_id, event_type
        )


async def _send_webhook(
    wh: Webhook, event_type: str, payload: dict
) -> None:
    body = json.dumps(payload, default=str)
    signature = hmac.new(
        wh.secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                wh.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-MiniRAG-Signature": signature,
                    "X-MiniRAG-Event": event_type,
                },
            )
    except Exception:
        logger.warning("Webhook delivery failed for %s to %s", event_type, wh.url)
