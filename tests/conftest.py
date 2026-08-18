"""
Pytest configuration and shared fixtures for the Ham Net Tracker test suite.

Strategy
--------
- DATABASE_URL is set to SQLite in-memory BEFORE any app imports, so
  database.py never tries to connect to PostgreSQL.
- StaticPool is applied so all get_db() calls share the same in-memory
  connection — critical for SQLite :memory: to work across requests.
- Rate limiting is disabled so tests can hit /auth endpoints freely.
- Tables are created once per session; rows are wiped between tests.
"""

import os
import sys

# Add this directory to sys.path so helpers.py is importable from test files.
sys.path.insert(0, os.path.dirname(__file__))

# ── Must happen before any app imports ──────────────────────────────────────
os.environ["DATABASE_URL"] = "sqlite:///:memory:"
os.environ.setdefault("SECRET_KEY", "test-secret-key-not-for-production")
os.environ.setdefault("ACCESS_TOKEN_EXPIRE_MINUTES", "60")

# ── Patch the database module to use StaticPool ──────────────────────────────
# SQLite :memory: databases are per-connection; StaticPool forces SQLAlchemy
# to reuse one connection so all get_db() calls see the same data.
import database  # noqa: E402 — must come after env vars are set
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

_test_engine = create_engine(
    "sqlite:///:memory:",
    connect_args={"check_same_thread": False},
    poolclass=StaticPool,
)
_TestSession = sessionmaker(autocommit=False, autoflush=False, bind=_test_engine)

# Replace the module-level engine and session factory
database.engine = _test_engine
database.SessionLocal = _TestSession

# ── Now safe to import the app ───────────────────────────────────────────────
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from models import Base  # noqa: E402
from main import app, limiter  # noqa: E402
from helpers import register, login, auth  # noqa: E402, F401

# Disable rate limiting so tests can call auth endpoints without hitting caps.
limiter.enabled = False


# ── Database lifecycle ───────────────────────────────────────────────────────

@pytest.fixture(scope="session", autouse=True)
def setup_database():
    """Create all tables once for the entire test session."""
    Base.metadata.create_all(bind=_test_engine)
    yield
    Base.metadata.drop_all(bind=_test_engine)


@pytest.fixture(autouse=True)
def clean_tables():
    """Wipe all rows after each test to guarantee isolation."""
    yield
    db = _TestSession()
    try:
        for table in reversed(Base.metadata.sorted_tables):
            db.execute(table.delete())
        db.commit()
    finally:
        db.close()


# ── Test client ──────────────────────────────────────────────────────────────

@pytest.fixture
def client():
    """FastAPI TestClient — uses `with` so the startup event fires."""
    with TestClient(app) as c:
        yield c


# ── Composite fixtures ───────────────────────────────────────────────────────

@pytest.fixture
def admin_token(client):
    """Register the first user (auto-admin/active) and return their JWT."""
    register(client, "W1ADMIN", "Admin User", "admin@example.com")
    return login(client, "W1ADMIN")


@pytest.fixture
def admin_headers(admin_token):
    """Authorization headers for the admin user."""
    return auth(admin_token)


@pytest.fixture
def user_token(client, admin_token):
    """Register a second user, approve them as admin, return their JWT."""
    register(client, "W2USER", "Regular User", "user@example.com")
    users = client.get("/admin/users", headers=auth(admin_token)).json()
    pending = next(u for u in users if u["callsign"] == "W2USER")
    client.patch(f"/admin/users/{pending['id']}/approve", headers=auth(admin_token))
    return login(client, "W2USER")


@pytest.fixture
def user_headers(user_token):
    """Authorization headers for the regular (non-admin) user."""
    return auth(user_token)


@pytest.fixture
def db():
    """Raw DB session for seeding test data directly (e.g. cache rows)."""
    session = _TestSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def net(client, admin_headers):
    """Create and return a test net owned by the admin."""
    resp = client.post("/nets", json={
        "name": "Monday Night 2m Net",
        "frequency": "146.520 MHz",
        "description": "Test net",
        "is_ares": False,
    }, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


@pytest.fixture
def session(client, admin_headers, net):
    """Start and return a live session on the test net."""
    resp = client.post(f"/nets/{net['id']}/sessions", json={
        "name": "Test Session",
        "notes": None,
    }, headers=admin_headers)
    assert resp.status_code == 201, resp.text
    return resp.json()
