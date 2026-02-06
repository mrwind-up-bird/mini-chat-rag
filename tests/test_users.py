"""Tests for users CRUD endpoints."""

import pytest
from httpx import AsyncClient


async def _bootstrap(client: AsyncClient, slug: str):
    """Helper: bootstrap a tenant and return (headers, owner_data)."""
    resp = await client.post("/v1/tenants", json={
        "tenant_name": f"{slug} Co",
        "tenant_slug": slug,
        "owner_email": f"owner@{slug}.com",
        "owner_password": "testpass123",
    })
    assert resp.status_code == 201
    data = resp.json()
    headers = {"Authorization": f"Bearer {data['api_token']}"}
    return headers, data


@pytest.mark.asyncio
async def test_list_users(client: AsyncClient):
    """Owner can list users — sees at least themselves."""
    headers, _ = await _bootstrap(client, slug="users-list")

    resp = await client.get("/v1/users", headers=headers)
    assert resp.status_code == 200
    users = resp.json()
    assert len(users) >= 1
    assert any(u["email"] == "owner@users-list.com" for u in users)


@pytest.mark.asyncio
async def test_create_user(client: AsyncClient):
    """Owner can create a new user in the tenant."""
    headers, _ = await _bootstrap(client, slug="users-create")

    resp = await client.post("/v1/users", json={
        "email": "member@users-create.com",
        "password": "memberpass1",
        "display_name": "Test Member",
        "role": "member",
    }, headers=headers)
    assert resp.status_code == 201
    data = resp.json()
    assert data["email"] == "member@users-create.com"
    assert data["role"] == "member"
    assert data["is_active"] is True


@pytest.mark.asyncio
async def test_create_duplicate_email_rejected(client: AsyncClient):
    """Cannot create two users with same email in same tenant."""
    headers, _ = await _bootstrap(client, slug="users-dup")

    user_data = {
        "email": "dup@users-dup.com",
        "password": "password123",
        "role": "member",
    }
    resp = await client.post("/v1/users", json=user_data, headers=headers)
    assert resp.status_code == 201

    resp = await client.post("/v1/users", json=user_data, headers=headers)
    assert resp.status_code == 409


@pytest.mark.asyncio
async def test_deactivate_user(client: AsyncClient):
    """Owner can deactivate a user."""
    headers, _ = await _bootstrap(client, slug="users-deact")

    # Create a user
    resp = await client.post("/v1/users", json={
        "email": "victim@users-deact.com",
        "password": "password123",
        "role": "member",
    }, headers=headers)
    user_id = resp.json()["id"]

    # Deactivate
    resp = await client.delete(f"/v1/users/{user_id}", headers=headers)
    assert resp.status_code == 204

    # Verify deactivated
    resp = await client.get("/v1/users", headers=headers)
    user = [u for u in resp.json() if u["id"] == user_id][0]
    assert user["is_active"] is False


@pytest.mark.asyncio
async def test_member_cannot_create_user(client: AsyncClient):
    """Member-role token cannot manage users (403)."""
    headers, _ = await _bootstrap(client, slug="users-perm")

    # Create a member user
    resp = await client.post("/v1/users", json={
        "email": "member@users-perm.com",
        "password": "password123",
        "role": "member",
    }, headers=headers)
    assert resp.status_code == 201

    # Login as the member
    resp = await client.post("/v1/auth/login", json={
        "email": "member@users-perm.com",
        "password": "password123",
    })
    member_token = resp.json()["access_token"]
    member_headers = {"Authorization": f"Bearer {member_token}"}

    # Try to create user as member — should fail
    resp = await client.post("/v1/users", json={
        "email": "another@users-perm.com",
        "password": "password123",
        "role": "member",
    }, headers=member_headers)
    assert resp.status_code == 403
