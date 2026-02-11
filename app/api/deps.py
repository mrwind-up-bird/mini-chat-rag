"""FastAPI dependencies for authentication and tenant resolution."""

import uuid
from typing import Annotated

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jose import JWTError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlmodel import select

from app.core.database import get_session
from app.core.security import decode_jwt, hash_api_token
from app.models.api_token import ApiToken
from app.models.base import utcnow
from app.models.user import User

bearer_scheme = HTTPBearer()


class AuthContext:
    """Resolved identity carried through a request."""

    __slots__ = ("tenant_id", "user_id", "token_id", "user_role")

    def __init__(
        self,
        tenant_id: uuid.UUID,
        user_id: uuid.UUID,
        user_role: str,
        token_id: uuid.UUID | None = None,
    ) -> None:
        self.tenant_id = tenant_id
        self.user_id = user_id
        self.user_role = user_role
        self.token_id = token_id


async def _resolve_api_token(
    raw_token: str, session: AsyncSession
) -> AuthContext:
    """Look up an API token by its SHA-256 hash."""
    token_hash = hash_api_token(raw_token)
    stmt = select(ApiToken).where(
        ApiToken.token_hash == token_hash,
        ApiToken.is_active.is_(True),  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    api_token = result.scalar_one_or_none()

    if api_token is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or revoked API token",
        )

    # Check expiry
    if api_token.expires_at and api_token.expires_at < utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API token has expired",
        )

    # Fetch the owning user to get role info
    user = await session.get(User, api_token.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token owner account is disabled",
        )

    # Update last_used_at (fire-and-forget, non-blocking)
    api_token.last_used_at = utcnow()
    session.add(api_token)
    await session.commit()

    return AuthContext(
        tenant_id=api_token.tenant_id,
        user_id=api_token.user_id,
        user_role=user.role,
        token_id=api_token.id,
    )


async def _resolve_jwt(token: str) -> AuthContext:
    """Decode a JWT and extract tenant_id + user_id."""
    try:
        payload = decode_jwt(token)
    except JWTError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired JWT",
        ) from exc

    try:
        return AuthContext(
            tenant_id=uuid.UUID(payload["tid"]),
            user_id=uuid.UUID(payload["sub"]),
            user_role=payload.get("role", "member"),
        )
    except (KeyError, ValueError) as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Malformed JWT payload",
        ) from exc


async def get_auth_context(
    credentials: Annotated[HTTPAuthorizationCredentials, Depends(bearer_scheme)],
    session: Annotated[AsyncSession, Depends(get_session)],
) -> AuthContext:
    """Resolve a bearer token to an AuthContext.

    Supports two token types:
    - API tokens (opaque, ~43 chars from token_urlsafe(32))
    - JWTs (contain dots: header.payload.signature)
    """
    raw = credentials.credentials

    if "." in raw:
        return await _resolve_jwt(raw)
    return await _resolve_api_token(raw, session)


# Typed shorthand for use in route signatures
Auth = Annotated[AuthContext, Depends(get_auth_context)]
Session = Annotated[AsyncSession, Depends(get_session)]
