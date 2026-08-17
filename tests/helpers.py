"""Shared test helpers — plain functions used by conftest fixtures and test files."""

from fastapi.testclient import TestClient


def register(client: TestClient, callsign="W1TEST", name="Test User",
             email="test@example.com", password="testpass123"):
    """POST /auth/register and return the Response."""
    return client.post("/auth/register", json={
        "callsign": callsign,
        "name": name,
        "email": email,
        "password": password,
    })


def login(client: TestClient, username: str, password="testpass123") -> str:
    """POST /auth/login and return the raw JWT string."""
    resp = client.post("/auth/login", data={
        "username": username,
        "password": password,
    })
    assert resp.status_code == 200, f"Login failed ({resp.status_code}): {resp.text}"
    return resp.json()["access_token"]


def auth(token: str) -> dict:
    """Return an Authorization header dict for the given token."""
    return {"Authorization": f"Bearer {token}"}
