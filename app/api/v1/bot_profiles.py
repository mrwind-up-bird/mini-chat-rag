"""BotProfile CRUD — all queries scoped to tenant_id."""

import json
import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import decrypt_value, encrypt_value
from app.models.base import utcnow
from app.models.bot_profile import (
    BotProfile,
    BotProfileCreate,
    BotProfileRead,
    BotProfileUpdate,
)

router = APIRouter(prefix="/bot-profiles", tags=["bot-profiles"])


def _to_read(bp: BotProfile) -> BotProfileRead:
    return BotProfileRead(
        id=bp.id,
        tenant_id=bp.tenant_id,
        name=bp.name,
        description=bp.description,
        model=bp.model,
        system_prompt=bp.system_prompt,
        temperature=bp.temperature,
        max_tokens=bp.max_tokens,
        has_credentials=bp.encrypted_credentials is not None,
        is_active=bp.is_active,
        created_at=bp.created_at,
        updated_at=bp.updated_at,
    )


@router.post("", response_model=BotProfileRead, status_code=status.HTTP_201_CREATED)
async def create_bot_profile(
    body: BotProfileCreate,
    auth: Auth,
    session: Session,
) -> BotProfileRead:
    encrypted = None
    if body.credentials:
        encrypted = encrypt_value(json.dumps(body.credentials))

    bp = BotProfile(
        tenant_id=auth.tenant_id,
        name=body.name,
        description=body.description,
        model=body.model,
        system_prompt=body.system_prompt,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        encrypted_credentials=encrypted,
    )
    session.add(bp)
    await session.commit()
    await session.refresh(bp)
    return _to_read(bp)


@router.get("", response_model=list[BotProfileRead])
async def list_bot_profiles(
    auth: Auth,
    session: Session,
) -> list[BotProfileRead]:
    stmt = (
        select(BotProfile)
        .where(BotProfile.tenant_id == auth.tenant_id)
        .order_by(BotProfile.created_at.desc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    return [_to_read(bp) for bp in result.scalars().all()]


@router.get("/{profile_id}", response_model=BotProfileRead)
async def get_bot_profile(
    profile_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> BotProfileRead:
    bp = await _get_or_404(profile_id, auth.tenant_id, session)
    return _to_read(bp)


@router.patch("/{profile_id}", response_model=BotProfileRead)
async def update_bot_profile(
    profile_id: uuid.UUID,
    body: BotProfileUpdate,
    auth: Auth,
    session: Session,
) -> BotProfileRead:
    bp = await _get_or_404(profile_id, auth.tenant_id, session)

    update_data = body.model_dump(exclude_unset=True)

    # Handle credentials separately
    if "credentials" in update_data:
        creds = update_data.pop("credentials")
        if creds:
            bp.encrypted_credentials = encrypt_value(json.dumps(creds))
        else:
            bp.encrypted_credentials = None

    for field, value in update_data.items():
        setattr(bp, field, value)

    bp.updated_at = utcnow()
    session.add(bp)
    await session.commit()
    await session.refresh(bp)
    return _to_read(bp)


@router.delete("/{profile_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_bot_profile(
    profile_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> None:
    bp = await _get_or_404(profile_id, auth.tenant_id, session)
    bp.is_active = False
    bp.updated_at = utcnow()
    session.add(bp)
    await session.commit()


# ── Internal helper ───────────────────────────────────────────

async def _get_or_404(
    profile_id: uuid.UUID,
    tenant_id: uuid.UUID,
    session,
) -> BotProfile:
    stmt = select(BotProfile).where(
        BotProfile.id == profile_id,
        BotProfile.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    bp = result.scalar_one_or_none()
    if bp is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Bot profile not found")
    return bp
