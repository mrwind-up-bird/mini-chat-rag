"""Users CRUD — tenant-scoped, restricted to owner/admin."""

import uuid

from fastapi import APIRouter, HTTPException, status
from sqlmodel import select

from app.api.deps import Auth, Session
from app.core.security import hash_password
from app.models.base import utcnow
from app.models.user import User, UserCreate, UserRead, UserRole

router = APIRouter(prefix="/users", tags=["users"])


def _require_elevated(auth_role: str) -> None:
    """Raise 403 if the caller is not owner or admin."""
    if auth_role not in (UserRole.OWNER, UserRole.ADMIN):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only owners and admins can manage users",
        )


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: UserCreate,
    auth: Auth,
    session: Session,
) -> UserRead:
    _require_elevated(auth.user_role)

    # Check email uniqueness within tenant
    stmt = select(User).where(
        User.tenant_id == auth.tenant_id,
        User.email == body.email,
    )
    result = await session.execute(stmt)
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A user with this email already exists in this tenant",
        )

    user = User(
        tenant_id=auth.tenant_id,
        email=body.email,
        password_hash=hash_password(body.password),
        display_name=body.display_name,
        role=body.role,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserRead.model_validate(user)


@router.get("", response_model=list[UserRead])
async def list_users(
    auth: Auth,
    session: Session,
) -> list[UserRead]:
    stmt = (
        select(User)
        .where(User.tenant_id == auth.tenant_id)
        .order_by(User.email.asc())  # type: ignore[union-attr]
    )
    result = await session.execute(stmt)
    return [UserRead.model_validate(u) for u in result.scalars().all()]


@router.patch("/{user_id}", response_model=UserRead)
async def update_user(
    user_id: uuid.UUID,
    body: UserCreate,
    auth: Auth,
    session: Session,
) -> UserRead:
    _require_elevated(auth.user_role)
    user = await _get_or_404(user_id, auth.tenant_id, session)

    update_data = body.model_dump(exclude_unset=True)
    if "password" in update_data:
        user.password_hash = hash_password(update_data.pop("password"))
    for field, value in update_data.items():
        setattr(user, field, value)

    user.updated_at = utcnow()
    session.add(user)
    await session.commit()
    await session.refresh(user)
    return UserRead.model_validate(user)


@router.delete("/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
async def deactivate_user(
    user_id: uuid.UUID,
    auth: Auth,
    session: Session,
) -> None:
    _require_elevated(auth.user_role)
    user = await _get_or_404(user_id, auth.tenant_id, session)
    user.is_active = False
    user.updated_at = utcnow()
    session.add(user)
    await session.commit()


# ── Internal helper ───────────────────────────────────────────

async def _get_or_404(
    user_id: uuid.UUID, tenant_id: uuid.UUID, session
) -> User:
    stmt = select(User).where(
        User.id == user_id,
        User.tenant_id == tenant_id,
    )
    result = await session.execute(stmt)
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
