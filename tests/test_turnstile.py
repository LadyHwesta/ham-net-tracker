"""
Tests for Cloudflare Turnstile bot protection on registration/login:
  GET  /auth/config
  POST /auth/register (turnstile_token)
  POST /auth/login (turnstile_token)

Opt-in: with TURNSTILE_SITE_KEY/SECRET_KEY unset (the default in every other
test file), none of this code path is exercised at all -- registration and
login behave exactly as before.
"""

import main
from helpers import register, login


class TestTurnstileVerifyHelper:
    def test_no_token_fails_without_network_call(self, monkeypatch, turnstile_configured):
        calls = []
        monkeypatch.setattr(main.httpx, "post", lambda *a, **k: calls.append(1))
        assert main._verify_turnstile(None, "1.2.3.4") is False
        assert calls == []

    def test_cloudflare_success_true(self, monkeypatch, turnstile_configured):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"success": True}

        monkeypatch.setattr(main.httpx, "post", lambda *a, **k: FakeResponse())
        assert main._verify_turnstile("sometoken", "1.2.3.4") is True

    def test_cloudflare_success_false(self, monkeypatch, turnstile_configured):
        class FakeResponse:
            def raise_for_status(self):
                pass

            def json(self):
                return {"success": False, "error-codes": ["invalid-input-response"]}

        monkeypatch.setattr(main.httpx, "post", lambda *a, **k: FakeResponse())
        assert main._verify_turnstile("badtoken", "1.2.3.4") is False

    def test_network_error_fails_closed(self, monkeypatch, turnstile_configured):
        def raise_error(*a, **k):
            raise ConnectionError("boom")

        monkeypatch.setattr(main.httpx, "post", raise_error)
        assert main._verify_turnstile("sometoken", "1.2.3.4") is False


class TestAuthConfig:
    def test_disabled_by_default(self, client):
        resp = client.get("/auth/config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["turnstile_enabled"] is False
        assert data["turnstile_site_key"] is None

    def test_enabled_exposes_site_key_only(self, client, turnstile_configured):
        resp = client.get("/auth/config")
        data = resp.json()
        assert data["turnstile_enabled"] is True
        assert data["turnstile_site_key"] == "1x00000000000000000000AA"
        assert "secret" not in str(data).lower()


class TestRegistrationTurnstile:
    def test_registration_unaffected_when_not_configured(self, client):
        resp = register(client, "W1NOTS", "No Turnstile", "nots@example.com")
        assert resp.status_code == 201

    def test_registration_requires_token_when_configured(self, client, turnstile_configured):
        resp = client.post("/auth/register", json={
            "callsign": "W1NOTOK", "name": "No Token", "email": "notok@example.com", "password": "testpass123",
        })
        assert resp.status_code == 400

    def test_registration_rejects_failed_verification(self, client, turnstile_configured, turnstile_verify):
        turnstile_verify.set_result(False)
        resp = client.post("/auth/register", json={
            "callsign": "W1BADTOK", "name": "Bad Token", "email": "badtok@example.com",
            "password": "testpass123", "turnstile_token": "bad",
        })
        assert resp.status_code == 400
        assert len(turnstile_verify) == 1

    def test_registration_succeeds_with_valid_token(self, client, turnstile_configured, turnstile_verify):
        resp = client.post("/auth/register", json={
            "callsign": "W1GOODTOK", "name": "Good Token", "email": "goodtok@example.com",
            "password": "testpass123", "turnstile_token": "good-token-value",
        })
        assert resp.status_code == 201, resp.text
        assert turnstile_verify[0]["token"] == "good-token-value"


class TestLoginTurnstile:
    def test_login_unaffected_when_not_configured(self, client):
        register(client, "W1LOGINNOTS", "No TS", "loginnots@example.com")
        token = login(client, "W1LOGINNOTS")
        assert token

    def test_login_rejects_missing_token_when_configured(self, client, turnstile_configured):
        register(client, "W1LOGINMISS", "Login Miss", "loginmiss@example.com")
        resp = client.post("/auth/login", data={"username": "W1LOGINMISS", "password": "testpass123"})
        assert resp.status_code == 400

    def test_login_rejects_failed_verification(self, client, turnstile_configured, turnstile_verify):
        register(client, "W1LOGINBAD", "Login Bad", "loginbad@example.com")
        turnstile_verify.set_result(False)
        resp = client.post("/auth/login", data={
            "username": "W1LOGINBAD", "password": "testpass123", "turnstile_token": "bad",
        })
        assert resp.status_code == 400

    def test_login_succeeds_with_valid_token(self, client, turnstile_configured, turnstile_verify):
        register(client, "W1LOGINGOOD", "Login Good", "logingood@example.com")
        resp = client.post("/auth/login", data={
            "username": "W1LOGINGOOD", "password": "testpass123", "turnstile_token": "good",
        })
        assert resp.status_code == 200, resp.text
        assert resp.json()["access_token"]
