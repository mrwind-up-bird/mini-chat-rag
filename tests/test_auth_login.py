"""Tests for auth login + me endpoints."""

import pytest
from httpx import AsyncClient


async def _bootstrap(client: AsyncClient, slug: str = "auth-test"):
    """Helper: bootstrap a tenant and return (headers, data)."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": "Auth Test Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    return headers, data


@pytest.mark.asyncio
async def test_login_success(client: AsyncClient):
    """Login with valid credentials returns JWT + user + tenant."""
    await _bootstrap(client, slug="login-ok")

    resp = await client.post("/v1/auth/login", json={
        "email": "owner@login-ok.com",
        "password": "testpass123",
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["token_type"] == "bearer"
    assert "." in data["access_token"]  # JWT has dots
    assert data["user"]["email"] == "owner@login-ok.com"
    assert data["user"]["role"] == "owner"
    assert data["tenant"]["slug"] == "login-ok"


@pytest.mark.asyncio
async def test_login_wrong_password(client: AsyncClient):
    """Login with wrong password returns 401."""
    await _bootstrap(client, slug="login-bad-pw")

    resp = await client.post("/v1/auth/login", json={
        "email": "owner@login-bad-pw.com",
        "password": "wrongpassword",
    })
    assert resp.status_code == 401
    assert "Invalid" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_login_unknown_email(client: AsyncClient):
    """Login with nonexistent email returns 401."""
    resp = await client.post("/v1/auth/login", json={
        "email": "nobody@nowhere.com",
        "password": "whatever123",
    })
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_me_with_jwt(client: AsyncClient):
    """GET /v1/auth/me works with JWT from login."""
    await _bootstrap(client, slug="me-jwt")

    # Login to get JWT
    resp = await client.post("/v1/auth/login", json={
        "email": "owner@me-jwt.com",
        "password": "testpass123",
    })
    jwt_token = resp.json()["access_token"]
    jwt_headers = {"Authorization": f"Bearer {jwt_token}"}

    # Use JWT to call /me
    resp = await client.get("/v1/auth/me", headers=jwt_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["user"]["email"] == "owner@me-jwt.com"
    assert data["tenant"]["slug"] == "me-jwt"


@pytest.mark.asyncio
async def test_me_with_api_token(client: AsyncClient):
    """GET /v1/auth/me also works with API token."""
    headers, _ = await _bootstrap(client, slug="me-api")

    resp = await client.get("/v1/auth/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["user"]["email"] == "owner@me-api.com"
