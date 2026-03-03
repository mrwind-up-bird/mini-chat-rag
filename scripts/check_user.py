"""Check user password in DB. Run: python -m scripts.check_user"""

import asyncio

from sqlmodel import select

from app.core.database import async_session_factory
from app.core.security import verify_password
from app.models.user import User


async def main():
    async with async_session_factory() as s:
        r = await s.execute(select(User))
        u = r.scalar_one()
        print(f"Email: {u.email}")
        print(f"Hash:  {u.password_hash[:40]}...")
        print(f"Verify meister12: {verify_password('meister12', u.password_hash)}")


if __name__ == "__main__":
    asyncio.run(main())
