import os
"""Seed an admin user + tenant. Run inside the web container."""

import asyncio

from app.core.database import async_session_factory
from app.core.security import hash_password
from app.models.tenant import Tenant
from app.models.user import User


async def main():
    async with async_session_factory() as session:
        tenant = Tenant(name="Default", slug="default")
        session.add(tenant)
        await session.flush()

        user = User(
            tenant_id=tenant.id,
            email=os.environ.get("ADMIN_EMAIL", "admin@example.com"),
            password_hash=hash_password(os.environ.get("ADMIN_PASSWORD", "changeme123")),
            role="admin",
        )
        session.add(user)
        await session.commit()
        print(f"Tenant: {tenant.id}")
        print(f"User:   {user.email} (admin)")


if __name__ == "__main__":
    asyncio.run(main())
