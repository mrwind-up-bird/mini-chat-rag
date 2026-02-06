"""API token management — create, list, revoke."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import generate_api_token, hash_api_token
from app.models.api_token import ApiToken, ApiTokenCreate, ApiTokenCreated, ApiTokenRead

router = APIRouter(prefix="/api-tokens", tags=["api-tokens"])


@router.post(
    "",
    response_model=ApiTokenCreated,
    status_code=status.HTTP_201_CREATED,
    summary="Create a new API token",
)
async def create_api_token(
    body: ApiTokenCreate,
    auth: Auth,
    session: Session,
) -> ApiTokenCreated:
    """Generate a new API token for the current tenant.

    The raw token is returned once — store it securely.
    """
    raw_token = generate_api_token()
    prefix = raw_token[:8]

    token = ApiToken(
        tenant_id=auth.tenant_id,
        user_id=auth.user_id,
        name=body.name,
        token_hash=hash_api_token(raw_token),
        token_prefix=prefix,
    )
    session.add(token)
    await session.commit()
    await session.refresh(token)

    return ApiTokenCreated(
        **ApiTokenRead.model_validate(token).model_dump(),
        raw_token=raw_token,
    )


@router.get(
    "",
    response_model=list[ApiTokenRead],
    summary="List API tokens for current tenant",
)
async def list_api_tokens(
    auth: Auth,
    session: Session,
) -> list[ApiTokenRead]:
    stmt = (
        select(ApiToken)
        .where(ApiToken.tenant_id == auth.tenant_id)
        .order_by(ApiToken.created_at.desc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    tokens = result.scalars().all()
    return [ApiTokenRead.model_validate(t) for t in tokens]


@router.delete(
    "/{token_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Revoke an API token",
)
async def revoke_api_token(
    token_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> None:
    """Soft-delete: sets is_active=False. The token can no longer authenticate."""
    stmt = select(ApiToken).where(
        ApiToken.id == token_id,
        ApiToken.tenant_id == auth.tenant_id,
    )
    result = await session.execute(stmt)
    token = result.scalar_one_or_none()

    if token is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Token not found",
        )

    token.is_active = False
    session.add(token)
    await session.commit()
