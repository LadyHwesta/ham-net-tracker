"""
Tests for the separate GMRS callsign on user profiles (issue #23):
  PATCH /auth/gmrs-callsign
  gmrs_callsign field on GET /auth/me, POST /auth/register, GET /users
"""
from helpers import register


class TestGmrsCallsignDefault:
    def test_new_user_defaults_to_none(self, client, user_headers):
        resp = client.get("/auth/me", headers=user_headers)
        assert resp.json()["gmrs_callsign"] is None

    def test_register_response_includes_null_gmrs_callsign(self, client):
        resp = client.post("/auth/register", json={
            "callsign": "W1NEW", "name": "Test", "email": "w1new@example.com", "password": "testpass123",
        })
        assert resp.json()["gmrs_callsign"] is None


class TestGmrsCallsignUpdate:
    def test_set_gmrs_callsign(self, client, user_headers):
        resp = client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "wqxh7777"}, headers=user_headers)
        assert resp.status_code == 200
        assert resp.json()["gmrs_callsign"] == "WQXH7777"  # uppercased

    def test_patch_requires_auth(self, client):
        resp = client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"})
        assert resp.status_code == 401

    def test_patch_persists(self, client, user_headers):
        client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"}, headers=user_headers)
        resp = client.get("/auth/me", headers=user_headers)
        assert resp.json()["gmrs_callsign"] == "WQXH7777"

    def test_blank_clears_gmrs_callsign(self, client, user_headers):
        client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"}, headers=user_headers)
        resp = client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "  "}, headers=user_headers)
        assert resp.json()["gmrs_callsign"] is None

    def test_omitted_field_clears_gmrs_callsign(self, client, user_headers):
        client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"}, headers=user_headers)
        resp = client.patch("/auth/gmrs-callsign", json={}, headers=user_headers)
        assert resp.json()["gmrs_callsign"] is None

    def test_gmrs_callsign_present_in_login_response(self, client):
        register(client, "W1LOGIN", "Login Test", "w1login@example.com", "testpass123")
        headers = {"Authorization": "Bearer " + client.post(
            "/auth/login", data={"username": "W1LOGIN", "password": "testpass123"},
        ).json()["access_token"]}
        client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"}, headers=headers)
        resp = client.post("/auth/login", data={"username": "W1LOGIN", "password": "testpass123"})
        assert resp.json()["user"]["gmrs_callsign"] == "WQXH7777"

    def test_gmrs_callsign_visible_to_other_users_via_users_list(self, client, admin_headers, user_headers):
        client.patch("/auth/gmrs-callsign", json={"gmrs_callsign": "WQXH7777"}, headers=user_headers)
        resp = client.get("/users", headers=admin_headers)
        assert resp.status_code == 200
        u = next(u for u in resp.json() if u["gmrs_callsign"] == "WQXH7777")
        assert u["gmrs_callsign"] == "WQXH7777"
