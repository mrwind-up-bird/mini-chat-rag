"""Webhook CRUD — all queries scoped to tenant_id."""

import hashlib
import hmac
import json
import secrets
import uuid

import httpx
from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlmodel import select

from app.api.deps import Auth, Session
from app.models.webhook import (
    Webhook,
    WebhookCreate,
    WebhookCreated,
    WebhookEvent,
    WebhookRead,
)

router = APIRouter(prefix="/webhooks", tags=["webhooks"])


def _to_read(wh: Webhook) -> WebhookRead:
    events = json.loads(wh.events) if isinstance(wh.events, str) else wh.events
    return WebhookRead(
        id=wh.id,
        tenant_id=wh.tenant_id,
        url=wh.url,
        events=events,
        is_active=wh.is_active,
        description=wh.description,
        has_secret=bool(wh.secret),
        created_at=wh.created_at,
        updated_at=wh.updated_at,
    )


class TestPingResponse(BaseModel):
    success: bool
    status_code: int | None = None


@router.post("", response_model=WebhookCreated, status_code=status.HTTP_201_CREATED)
async def create_webhook(
    body: WebhookCreate,
    auth: Auth,
    session: Session,
) -> WebhookCreated:
    # Validate event types
    valid_events = {e.value for e in WebhookEvent}
    for event in body.events:
        if event not in valid_events:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"Invalid event type: {event}. Valid: {sorted(valid_events)}",
            )

    raw_secret = body.secret or secrets.token_urlsafe(32)

    wh = Webhook(
        tenant_id=auth.tenant_id,
        url=body.url,
        secret=raw_secret,
        events=json.dumps(body.events),
        description=body.description,
    )
    session.add(wh)
    await session.commit()
    await session.refresh(wh)

    events = json.loads(wh.events) if isinstance(wh.events, str) else wh.events
    return WebhookCreated(
        id=wh.id,
        tenant_id=wh.tenant_id,
        url=wh.url,
        events=events,
        is_active=wh.is_active,
        description=wh.description,
        has_secret=True,
        secret=raw_secret,
        created_at=wh.created_at,
        updated_at=wh.updated_at,
    )


@router.get("", response_model=list[WebhookRead])
async def list_webhooks(
    auth: Auth,
    session: Session,
) -> list[WebhookRead]:
    stmt = (
        select(Webhook)
        .where(Webhook.tenant_id == auth.tenant_id)
        .order_by(Webhook.created_at.desc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    return [_to_read(wh) for wh in result.scalars().all()]


@router.get("/{webhook_id}", response_model=WebhookRead)
async def get_webhook(
    webhook_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> WebhookRead:
    wh = await _get_or_404(webhook_id, auth.tenant_id, session)
    return _to_read(wh)


@router.delete("/{webhook_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_webhook(
    webhook_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> None:
    wh = await _get_or_404(webhook_id, auth.tenant_id, session)
    await session.delete(wh)
    await session.commit()


@router.post("/{webhook_id}/test", response_model=TestPingResponse)
async def test_webhook(
    webhook_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> TestPingResponse:
    """Send a test ping to the webhook URL."""
    wh = await _get_or_404(webhook_id, auth.tenant_id, session)

    payload = {"event": "test.ping", "webhook_id": str(wh.id)}
    body = json.dumps(payload, default=str)
    signature = hmac.new(
        wh.secret.encode(), body.encode(), hashlib.sha256
    ).hexdigest()

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                wh.url,
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-MiniRAG-Signature": signature,
                    "X-MiniRAG-Event": "test.ping",
                },
            )
        return TestPingResponse(success=resp.is_success, status_code=resp.status_code)
    except Exception:
        return TestPingResponse(success=False, status_code=None)


# ── Internal helper ───────────────────────────────────────────

async def _get_or_404(
    webhook_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session,
) -> Webhook:
    stmt = select(Webhook).where(
        Webhook.id == webhook_id,
        Webhook.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    wh = result.scalar_one_or_none()
    if wh is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Webhook not found"
        )
    return wh
