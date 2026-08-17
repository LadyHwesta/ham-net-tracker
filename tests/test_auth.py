"""
Tests for authentication endpoints:
  POST /auth/register
  POST /auth/login
  GET  /auth/me
"""

from helpers import register, login, auth


class TestRegistration:
    def test_first_user_becomes_admin_and_active(self, client):
        resp = register(client)
        assert resp.status_code == 201
        data = resp.json()
        assert data["callsign"] == "W1TEST"
        assert data["is_active"] is True
        assert data["is_admin"] is True

    def test_second_user_requires_approval(self, client):
        register(client, "W1FIRST", "First User", "first@example.com")
        resp = register(client, "W2SECOND", "Second User", "second@example.com")
        assert resp.status_code == 201
        data = resp.json()
        assert data["is_active"] is False
        assert data["is_admin"] is False

    def test_callsign_stored_uppercase(self, client):
        resp = register(client, callsign="w1test")
        assert resp.status_code == 201
        assert resp.json()["callsign"] == "W1TEST"

    def test_duplicate_callsign_rejected(self, client):
        register(client)
        # Same callsign, different email
        resp = register(client, email="other@example.com")
        assert resp.status_code == 400
        assert "callsign" in resp.json()["detail"].lower()

    def test_duplicate_email_rejected(self, client):
        register(client)
        # Same email, different callsign
        resp = register(client, callsign="W2OTHER")
        assert resp.status_code == 400
        assert "email" in resp.json()["detail"].lower()


class TestLogin:
    def test_login_with_callsign(self, client):
        register(client)
        resp = client.post("/auth/login", data={
            "username": "W1TEST", "password": "testpass123"
        })
        assert resp.status_code == 200
        data = resp.json()
        assert "access_token" in data
        assert data["token_type"] == "bearer"

    def test_login_with_email(self, client):
        register(client)
        resp = client.post("/auth/login", data={
            "username": "test@example.com", "password": "testpass123"
        })
        assert resp.status_code == 200
        assert "access_token" in resp.json()

    def test_login_callsign_case_insensitive(self, client):
        """Login should succeed regardless of callsign case (stored uppercase)."""
        register(client)
        resp = client.post("/auth/login", data={
            "username": "w1test", "password": "testpass123"
        })
        assert resp.status_code == 200

    def test_login_wrong_password(self, client):
        register(client)
        resp = client.post("/auth/login", data={
            "username": "W1TEST", "password": "wrongpassword"
        })
        assert resp.status_code == 401

    def test_login_inactive_user_blocked(self, client):
        """A user awaiting approval must not be able to log in."""
        register(client, "W1ADMIN", "Admin", "admin@example.com")  # auto-approved
        register(client, "W2PEND", "Pending", "pending@example.com")  # needs approval
        resp = client.post("/auth/login", data={
            "username": "W2PEND", "password": "testpass123"
        })
        assert resp.status_code == 403

    def test_login_unknown_callsign(self, client):
        resp = client.post("/auth/login", data={
            "username": "W9NOBODY", "password": "pass"
        })
        assert resp.status_code == 401

    def test_login_response_includes_user(self, client):
        """Login response should include user info so the frontend doesn't
        need a separate /auth/me call immediately after login."""
        register(client)
        resp = client.post("/auth/login", data={
            "username": "W1TEST", "password": "testpass123"
        })
        data = resp.json()
        assert "user" in data
        assert data["user"]["callsign"] == "W1TEST"


class TestMe:
    def test_get_me_returns_current_user(self, client, admin_headers):
        resp = client.get("/auth/me", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["callsign"] == "W1ADMIN"

    def test_get_me_requires_auth(self, client):
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_get_me_with_bad_token(self, client):
        resp = client.get("/auth/me", headers={"Authorization": "Bearer notavalidtoken"})
        assert resp.status_code == 401
