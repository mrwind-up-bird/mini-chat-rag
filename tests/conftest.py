"""Shared test fixtures — async SQLite in-memory DB + test client."""

from collections.abc import AsyncGenerator

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine
from sqlalchemy.orm import sessionmaker
from sqlmodel import SQLModel

# Import all models so metadata is populated
import app.models  # noqa: F401
from app.core import cache
from app.core.database import get_session
from app.main import app


@pytest.fixture(scope="session")
async def engine():
    eng = create_async_engine("sqlite+aiosqlite://", echo=False)
    async with eng.begin() as conn:
        await conn.run_sync(SQLModel.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture(scope="session")
def test_session_factory(engine):
    """Session factory bound to the test SQLite engine."""
    return sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@pytest.fixture
async def session(test_session_factory) -> AsyncGenerator[AsyncSession, None]:
    async with test_session_factory() as sess:
        yield sess
        await sess.rollback()


@pytest.fixture
async def client(session) -> AsyncGenerator[AsyncClient, None]:
    """HTTPX async test client with DB session override."""

    async def _override_session():
        yield session

    app.dependency_overrides[get_session] = _override_session
    cache.clear()

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac

    app.dependency_overrides.clear()
    cache.clear()
