"""
Tests for email verification (tech debt: "No email verification on registration"):
  GET /auth/verify-email
  email_verified gate on POST /auth/login
  email_verified / verification email behavior on POST /auth/register

SMTP is never configured in the test environment (see conftest.py), so by
default every registration is auto-verified — matching today's behavior for
anyone who hasn't set up SMTP. The `smtp_configured` fixture flips
_smtp_configured() on for tests that need to exercise the verification gate,
and `sent_emails` intercepts send_email() so no real network call is made.
"""
from helpers import register, auth


class TestRegistrationWithoutSmtp:
    """Default test environment: SMTP unconfigured -- unchanged pre-existing behavior."""

    def test_user_is_auto_verified(self, client):
        resp = register(client, "W1FIRST", "First User", "first@example.com")
        assert resp.json()["email_verified"] is True

    def test_second_user_also_auto_verified(self, client):
        register(client, "W1FIRST", "First User", "first@example.com")
        resp = register(client, "W2SECOND", "Second User", "second@example.com")
        assert resp.json()["email_verified"] is True


class TestRegistrationWithSmtp:
    def test_first_user_still_auto_verified(self, client, smtp_configured, sent_emails):
        """Bootstrap admin skips verification even with SMTP configured -- avoids a
        first-run lockout if the fresh SMTP config turns out to be wrong."""
        resp = register(client, "W1FIRST", "First User", "first@example.com")
        assert resp.json()["email_verified"] is True
        assert not any(e["subject"] == "[Ham Net Tracker] Verify Your Email" for e in sent_emails)

    def test_second_user_requires_verification(self, client, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        resp = register(client, "W2SECOND", "Second User", "second@example.com")
        assert resp.json()["email_verified"] is False

        verify_emails = [e for e in sent_emails if e["subject"] == "[Ham Net Tracker] Verify Your Email"]
        assert len(verify_emails) == 1
        assert verify_emails[0]["to"] == ["second@example.com"]


class TestLoginGate:
    def test_login_blocked_when_unverified(self, client, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        register(client, "W2SECOND", "Second User", "second@example.com")

        resp = client.post("/auth/login", data={"username": "W2SECOND", "password": "testpass123"})
        assert resp.status_code == 403
        assert "verify" in resp.json()["detail"].lower()

    def test_login_still_blocked_after_verify_if_not_approved(self, client, db, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        register(client, "W2SECOND", "Second User", "second@example.com")

        token = _extract_token(db, "W2SECOND")
        client.get(f"/auth/verify-email?token={token}", follow_redirects=False)

        resp = client.post("/auth/login", data={"username": "W2SECOND", "password": "testpass123"})
        assert resp.status_code == 403
        assert "approval" in resp.json()["detail"].lower()

    def test_login_succeeds_once_verified_and_approved(self, client, db, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        register(client, "W2SECOND", "Second User", "second@example.com")

        admin_token = client.post("/auth/login", data={"username": "W1FIRST", "password": "testpass123"}).json()["access_token"]
        users = client.get("/admin/users", headers=auth(admin_token)).json()
        pending = next(u for u in users if u["callsign"] == "W2SECOND")
        client.patch(f"/admin/users/{pending['id']}/approve", headers=auth(admin_token))

        token = _extract_token(db, "W2SECOND")
        client.get(f"/auth/verify-email?token={token}", follow_redirects=False)

        resp = client.post("/auth/login", data={"username": "W2SECOND", "password": "testpass123"})
        assert resp.status_code == 200
        assert "access_token" in resp.json()


class TestVerifyEmailEndpoint:
    def test_valid_token_marks_verified_and_redirects(self, client, db, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        register(client, "W2SECOND", "Second User", "second@example.com")
        token = _extract_token(db, "W2SECOND")

        resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/?verified=1"

    def test_invalid_token_redirects_with_failure(self, client):
        resp = client.get("/auth/verify-email?token=not-a-real-token", follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert resp.headers["location"] == "/?verified=0"

    def test_token_is_single_use(self, client, db, smtp_configured, sent_emails):
        register(client, "W1FIRST", "First User", "first@example.com")
        register(client, "W2SECOND", "Second User", "second@example.com")
        token = _extract_token(db, "W2SECOND")

        client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
        resp = client.get(f"/auth/verify-email?token={token}", follow_redirects=False)
        assert resp.headers["location"] == "/?verified=0"


def _extract_token(db, callsign):
    """UserOut doesn't expose verification_token (it's not meant to be public) --
    read it straight from the DB, the same way the user's email link would carry it."""
    from models import User
    return db.query(User).filter(User.callsign == callsign).first().verification_token
