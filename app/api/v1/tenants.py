"""Tenant registration (bootstrap) endpoint."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr, Field
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import generate_api_token, hash_api_token, hash_password
from app.models.api_token import ApiToken
from app.models.tenant import Tenant, TenantRead
from app.models.user import User, UserRole

router = APIRouter(prefix="/tenants", tags=["tenants"])


# ── Bootstrap request / response schemas ──────────────────────

class TenantBootstrapRequest(BaseModel):
    """Everything needed to create a new tenant + owner in one call."""
    tenant_name: str = Field(max_length=255)
    tenant_slug: str = Field(max_length=100, pattern=r"^[a-z0-9\-]+$")
    owner_email: EmailStr
    owner_password: str = Field(min_length=8, max_length=128)
    owner_display_name: str = Field(default="", max_length=255)


class TenantBootstrapResponse(BaseModel):
    tenant: TenantRead
    api_token: str = Field(description="Shown once — store it securely")
    token_prefix: str


# ── Routes ────────────────────────────────────────────────────

@router.post(
    "",
    response_model=TenantBootstrapResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new tenant (bootstrap)",
)
async def bootstrap_tenant(
    body: TenantBootstrapRequest,
    session: Session,
) -> TenantBootstrapResponse:
    """Create a tenant, its first owner user, and an initial API token.

    This is the only unauthenticated write endpoint.
    The raw API token is returned once — the caller must store it.
    """
    # Check slug uniqueness
    existing = await session.execute(
        select(Tenant).where(Tenant.slug == body.tenant_slug)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Slug '{body.tenant_slug}' is already taken",
        )

    # 1. Create tenant
    tenant = Tenant(
        name=body.tenant_name,
        slug=body.tenant_slug,
    )
    session.add(tenant)
    await session.flush()  # populate tenant.id

    # 2. Create owner user
    user = User(
        tenant_id=tenant.id,
        email=body.owner_email,
        password_hash=hash_password(body.owner_password),
        display_name=body.owner_display_name,
        role=UserRole.OWNER,
    )
    session.add(user)
    await session.flush()

    # 3. Create first API token
    raw_token = generate_api_token()
    prefix = raw_token[:8]
    token = ApiToken(
        tenant_id=tenant.id,
        user_id=user.id,
        name="default",
        token_hash=hash_api_token(raw_token),
        token_prefix=prefix,
    )
    session.add(token)
    await session.commit()
    await session.refresh(tenant)

    return TenantBootstrapResponse(
        tenant=TenantRead.model_validate(tenant),
        api_token=raw_token,
        token_prefix=prefix,
    )


@router.get(
    "/me",
    response_model=TenantRead,
    summary="Get current tenant info",
)
async def get_current_tenant(
    auth: Auth,
    session: Session,
) -> TenantRead:
    """Returns the tenant associated with the authenticated token."""
    tenant = await session.get(Tenant, auth.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")
    return TenantRead.model_validate(tenant)
