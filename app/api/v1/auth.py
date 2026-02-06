"""Authentication endpoints — login + current user."""

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import create_jwt, verify_password
from app.models.tenant import Tenant, TenantRead
from app.models.user import User, UserRead

router = APIRouter(prefix="/auth", tags=["auth"])


# ── Schemas ──────────────────────────────────────────────────

class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserRead
    tenant: TenantRead


class MeResponse(BaseModel):
    user: UserRead
    tenant: TenantRead


# ── Routes ───────────────────────────────────────────────────

@router.post("/login", response_model=LoginResponse)
async def login(body: LoginRequest, session: Session) -> LoginResponse:
    """Authenticate with email + password, receive a JWT."""
    stmt = select(User).where(User.email == body.email)
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()

    if user is None or not verify_password(body.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid email or password",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Account is disabled",
        )

    tenant = await session.get(Tenant, user.tenant_id)
    if tenant is None or not tenant.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Tenant is disabled",
        )

    token = create_jwt(
        subject=str(user.id),
        tenant_id=str(user.tenant_id),
        role=user.role,
    )

    return LoginResponse(
        access_token=token,
        user=UserRead.model_validate(user),
        tenant=TenantRead.model_validate(tenant),
    )


@router.get("/me", response_model=MeResponse)
async def get_me(auth: Auth, session: Session) -> MeResponse:
    """Return the current authenticated user and their tenant."""
    user = await session.get(User, auth.user_id)
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")

    tenant = await session.get(Tenant, auth.tenant_id)
    if tenant is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Tenant not found")

    return MeResponse(
        user=UserRead.model_validate(user),
        tenant=TenantRead.model_validate(tenant),
    )
