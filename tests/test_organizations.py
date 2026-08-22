"""
Tests for multi-tenancy (issue #1):
  POST /auth/register (org_slug/org_name/org_website_url)
  GET  /orgs, GET /orgs/mine
  POST /orgs/join
  PATCH /auth/current-org
  GET  /orgs/{id}/pending-members, /orgs/{id}/members
  PATCH /orgs/{id}/members/{user_id}/approve
  POST /orgs/{id}/members/{user_id}/reject
  Org scoping on /nets, /users, /sessions, /checkins

Founding a brand new org requires a website URL and, since the founder can't
approve their own account, a super admin's sign-off via the existing global
/admin/users/{id}/approve before they can log in (issue #1 follow-up).
Joining an EXISTING org is unchanged: that org's own admin approves it.
"""

from helpers import register, login, auth


def _bootstrap_super_admin(client):
    """Register the instance's first-ever user -- automatically the global
    super admin and immediately active (no one else exists to approve them).
    Needed by nearly every test below to approve a subsequent org founder."""
    register(client, "W0SUPER", "Super", "w0super@example.com")
    return login(client, "W0SUPER")


def _org_owner(client, super_token, callsign, org_slug, org_name, website="https://example.org"):
    """Register a user founding a brand new org, have the super admin approve
    them, and return their JWT."""
    resp = client.post("/auth/register", json={
        "callsign": callsign, "name": callsign, "email": f"{callsign.lower()}@example.com",
        "password": "testpass123", "org_slug": org_slug, "org_name": org_name, "org_website_url": website,
    })
    assert resp.status_code == 201, resp.text
    user_id = resp.json()["id"]
    approve = client.patch(f"/admin/users/{user_id}/approve", headers=auth(super_token))
    assert approve.status_code == 200, approve.text
    return login(client, callsign)


class TestRegistrationOrgFlow:
    def test_first_ever_user_is_super_admin_regardless_of_org(self, client):
        resp = register(client, "W1FIRST", "First", "first@example.com")
        data = resp.json()
        assert data["is_active"] is True
        assert data["is_admin"] is True
        assert data["current_org_id"] is not None

    def test_new_org_requires_website_url(self, client):
        resp = client.post("/auth/register", json={
            "callsign": "W2NOWEB", "name": "No Web", "email": "noweb@example.com", "password": "testpass123",
            "org_slug": "no-web-org", "org_name": "No Web Org",
        })
        assert resp.status_code == 400

    def test_new_org_website_url_must_be_http_or_https(self, client):
        """Rejects e.g. a javascript: URI -- this gets rendered as a clickable
        link in the admin approval queue, so anything else is a stored-XSS
        vector against whoever reviews it."""
        resp = client.post("/auth/register", json={
            "callsign": "W2BADURL", "name": "Bad URL", "email": "badurl@example.com", "password": "testpass123",
            "org_slug": "bad-url-org", "org_name": "Bad URL Org",
            "org_website_url": "javascript:alert(1)",
        })
        assert resp.status_code == 400

    def test_new_org_founder_is_not_active_until_super_admin_approves(self, client):
        super_token = _bootstrap_super_admin(client)
        resp = client.post("/auth/register", json={
            "callsign": "W2OWN", "name": "Owner", "email": "owner@example.com", "password": "testpass123",
            "org_slug": "acme-ares", "org_name": "ACME ARES", "org_website_url": "https://acme-ares.example.org",
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["is_active"] is False
        assert data["is_admin"] is False
        login_resp = client.post("/auth/login", data={"username": "W2OWN", "password": "testpass123"})
        assert login_resp.status_code == 403

        # A super admin approving them (the existing global escape hatch) unblocks login
        approve = client.patch(f"/admin/users/{data['id']}/approve", headers=auth(super_token))
        assert approve.status_code == 200
        login(client, "W2OWN")  # no longer blocked

    def test_admin_users_list_includes_org_name_and_website_for_pending_founder(self, client):
        super_token = _bootstrap_super_admin(client)
        client.post("/auth/register", json={
            "callsign": "W2OWN", "name": "Owner", "email": "owner@example.com", "password": "testpass123",
            "org_slug": "acme-ares", "org_name": "ACME ARES", "org_website_url": "https://acme-ares.example.org",
        })
        users = client.get("/admin/users", headers=auth(super_token)).json()
        row = next(u for u in users if u["callsign"] == "W2OWN")
        assert row["org_name"] == "ACME ARES"
        assert row["org_website_url"] == "https://acme-ares.example.org"

    def test_new_org_founder_membership_is_already_admin_role_once_approved(self, client):
        super_token = _bootstrap_super_admin(client)
        token = _org_owner(client, super_token, "W2OWN", "acme-ares", "ACME ARES")
        mine = client.get("/orgs/mine", headers=auth(token)).json()
        assert len(mine) == 1
        assert mine[0]["role"] == "admin"

    def test_joining_an_existing_org_is_pending(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")  # creates the "default" org
        resp = register(client, "W2JOIN", "Joiner", "joiner@example.com")  # joins "default"
        assert resp.status_code == 201
        assert resp.json()["is_active"] is False
        login_resp = client.post("/auth/login", data={"username": "W2JOIN", "password": "testpass123"})
        assert login_resp.status_code == 403

    def test_org_admin_can_approve_pending_member(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")
        owner_token = login(client, "W1OWN")
        org_id = client.get("/auth/me", headers=auth(owner_token)).json()["current_org_id"]

        register(client, "W2JOIN", "Joiner", "joiner@example.com")
        pending = client.get(f"/orgs/{org_id}/pending-members", headers=auth(owner_token)).json()
        joiner_id = next(m["user_id"] for m in pending if m["callsign"] == "W2JOIN")

        resp = client.patch(f"/orgs/{org_id}/members/{joiner_id}/approve", headers=auth(owner_token))
        assert resp.status_code == 204
        login(client, "W2JOIN")  # no longer blocked

    def test_non_org_admin_cannot_approve(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")
        owner_token = login(client, "W1OWN")
        org_id = client.get("/auth/me", headers=auth(owner_token)).json()["current_org_id"]

        register(client, "W2MEMBER", "Member", "member@example.com")
        pending = client.get(f"/orgs/{org_id}/pending-members", headers=auth(owner_token)).json()
        member_id = next(m["user_id"] for m in pending if m["callsign"] == "W2MEMBER")
        client.patch(f"/orgs/{org_id}/members/{member_id}/approve", headers=auth(owner_token))
        member_token = login(client, "W2MEMBER")

        register(client, "W3JOIN", "Joiner3", "joiner3@example.com")
        resp = client.patch(f"/orgs/{org_id}/members/{member_id}/approve", headers=auth(member_token))
        assert resp.status_code == 403

    def test_reject_pending_member_deletes_membership_not_account(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")
        owner_token = login(client, "W1OWN")
        org_id = client.get("/auth/me", headers=auth(owner_token)).json()["current_org_id"]

        register(client, "W2JOIN", "Joiner", "joiner@example.com")
        pending = client.get(f"/orgs/{org_id}/pending-members", headers=auth(owner_token)).json()
        joiner_id = next(m["user_id"] for m in pending if m["callsign"] == "W2JOIN")

        resp = client.post(f"/orgs/{org_id}/members/{joiner_id}/reject", headers=auth(owner_token))
        assert resp.status_code == 204
        pending_after = client.get(f"/orgs/{org_id}/pending-members", headers=auth(owner_token)).json()
        assert not any(m["user_id"] == joiner_id for m in pending_after)
        # account itself still exists (unlike the legacy global reject, which deletes it)
        users = client.get("/admin/users", headers=auth(owner_token)).json()
        assert any(u["callsign"] == "W2JOIN" for u in users)

    def test_super_admin_approve_clears_pending_memberships_too(self, client):
        register(client, "W1SUPER", "Super", "super@example.com")
        super_token = login(client, "W1SUPER")

        register(client, "W2JOIN", "Joiner", "joiner@example.com")  # joins "default", pending
        users = client.get("/admin/users", headers=auth(super_token)).json()
        joiner_id = next(u["id"] for u in users if u["callsign"] == "W2JOIN")

        resp = client.patch(f"/admin/users/{joiner_id}/approve", headers=auth(super_token))
        assert resp.status_code == 200
        login(client, "W2JOIN")  # no longer blocked


class TestOrgSwitching:
    def test_orgs_mine_lists_approved_orgs(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")
        token = login(client, "W1OWN")
        mine = client.get("/orgs/mine", headers=auth(token)).json()
        assert len(mine) == 1
        assert mine[0]["slug"] == "default"

    def test_join_second_org_then_switch(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")  # first-ever user -> also super admin
        token = login(client, "W1OWN")
        resp = client.post("/orgs/join", json={
            "org_slug": "second-org", "org_name": "Second Org", "org_website_url": "https://second.example.org",
        }, headers=auth(token))
        assert resp.status_code == 201
        second_org_id = resp.json()["id"]

        # Founding a second org via /orgs/join is pending too -- not auto-approved
        # just because the caller is already active elsewhere (issue #1 follow-up).
        # (Not asserting a 403 on switching to it here: W1OWN is themselves a
        # super admin -- first-ever user -- so they can switch to ANY org
        # regardless of approval status, same bypass /nets etc. already have.
        # test_cannot_switch_to_an_unapproved_org below covers the non-admin case.)
        mine_before = client.get("/orgs/mine", headers=auth(token)).json()
        assert len(mine_before) == 1

        # W1OWN is a super admin (first-ever user) so can self-approve via the
        # existing global escape hatch.
        me = client.get("/auth/me", headers=auth(token)).json()
        approve = client.patch(f"/admin/users/{me['id']}/approve", headers=auth(token))
        assert approve.status_code == 200

        mine = client.get("/orgs/mine", headers=auth(token)).json()
        assert len(mine) == 2

        switch = client.patch("/auth/current-org", json={"org_id": second_org_id}, headers=auth(token))
        assert switch.status_code == 200
        assert switch.json()["current_org_id"] == second_org_id

    def test_join_same_org_twice_errors(self, client):
        register(client, "W1OWN", "Owner", "owner@example.com")
        token = login(client, "W1OWN")
        resp = client.post("/orgs/join", json={"org_slug": "default"}, headers=auth(token))
        assert resp.status_code == 400

    def test_cannot_switch_to_an_unapproved_org(self, client):
        super_token = _bootstrap_super_admin(client)
        token = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        other_org_id = client.post(
            "/auth/register",
            json={
                "callsign": "W3OUT", "name": "Outsider", "email": "outsider@example.com",
                "password": "testpass123", "org_slug": "other-org", "org_name": "Other Org",
                "org_website_url": "https://other.example.org",
            },
        ).json()["current_org_id"]

        resp = client.patch("/auth/current-org", json={"org_id": other_org_id}, headers=auth(token))
        assert resp.status_code == 403


class TestCrossOrgIsolation:
    def test_user_cannot_see_other_orgs_net_in_list(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        net_a = client.post("/nets", json={"name": "Org A Net", "is_ares": False}, headers=auth(token_a)).json()

        token_b = _org_owner(client, super_token, "W1BORG", "org-b", "Org B")
        nets_b = client.get("/nets", headers=auth(token_b)).json()
        assert all(n["id"] != net_a["id"] for n in nets_b)

    def test_user_gets_404_fetching_other_orgs_net_directly(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        net_a = client.post("/nets", json={"name": "Org A Net", "is_ares": False}, headers=auth(token_a)).json()

        token_b = _org_owner(client, super_token, "W1BORG", "org-b", "Org B")
        resp = client.get(f"/nets/{net_a['id']}", headers=auth(token_b))
        assert resp.status_code == 404

    def test_new_net_is_scoped_to_creators_current_org(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        net = client.post("/nets", json={"name": "Org A Net", "is_ares": False}, headers=auth(token_a)).json()
        me = client.get("/auth/me", headers=auth(token_a)).json()
        assert net["org_id"] == me["current_org_id"]

    def test_users_picker_excludes_other_orgs(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        _org_owner(client, super_token, "W1BORG", "org-b", "Org B")
        users_seen_by_a = client.get("/users", headers=auth(token_a)).json()
        assert all(u["callsign"] != "W1BORG" for u in users_seen_by_a)

    def test_org_admin_cannot_approve_a_different_orgs_pending_member(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")

        token_b = _org_owner(client, super_token, "W1BORG", "org-b", "Org B")
        org_b_id = client.get("/auth/me", headers=auth(token_b)).json()["current_org_id"]

        join_resp = client.post("/auth/register", json={
            "callsign": "W2JOINB", "name": "Joiner", "email": "joinb@example.com",
            "password": "testpass123", "org_slug": "org-b",
        })
        joiner_id = join_resp.json()["id"]

        resp = client.patch(f"/orgs/{org_b_id}/members/{joiner_id}/approve", headers=auth(token_a))
        assert resp.status_code == 403

    def test_session_and_checkins_isolated_across_orgs(self, client):
        super_token = _bootstrap_super_admin(client)
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        net_a = client.post("/nets", json={"name": "Org A Net", "is_ares": False}, headers=auth(token_a)).json()
        session_a = client.post(f"/nets/{net_a['id']}/sessions", json={}, headers=auth(token_a)).json()
        client.post(f"/sessions/{session_a['id']}/checkins", json={"callsign": "W9ORGA"}, headers=auth(token_a))

        token_b = _org_owner(client, super_token, "W1BORG", "org-b", "Org B")
        resp = client.get(f"/sessions/{session_a['id']}", headers=auth(token_b))
        assert resp.status_code == 404
        resp2 = client.post(f"/sessions/{session_a['id']}/checkins", json={"callsign": "W9ORGB"}, headers=auth(token_b))
        assert resp2.status_code in (403, 404)

    def test_super_admin_bypasses_org_scoping(self, client):
        super_token = _bootstrap_super_admin(client)  # W0SUPER -- first-ever user -> super admin
        token_a = _org_owner(client, super_token, "W1AORG", "org-a", "Org A")
        net_a = client.post("/nets", json={"name": "Org A Net", "is_ares": False}, headers=auth(token_a)).json()

        resp = client.get(f"/nets/{net_a['id']}", headers=auth(super_token))
        assert resp.status_code == 200

        all_nets = client.get("/nets", headers=auth(super_token)).json()
        assert any(n["id"] == net_a["id"] for n in all_nets)


class TestBackwardCompatDefaultOrg:
    """A single-tenant deployment that never sends org fields should behave
    exactly as it did before this feature existed."""

    def test_omitting_org_fields_uses_default_org(self, client):
        register(client, "W1DEF", "Default", "default@example.com")
        token = login(client, "W1DEF")
        mine = client.get("/orgs/mine", headers=auth(token)).json()
        assert len(mine) == 1
        assert mine[0]["slug"] == "default"
        me = client.get("/auth/me", headers=auth(token)).json()
        assert me["current_org_id"] == mine[0]["id"]

    def test_second_plain_registration_joins_same_default_org_pending(self, client):
        register(client, "W1DEF", "Default", "default@example.com")
        resp = register(client, "W2DEF", "Second", "second@example.com")
        assert resp.json()["is_active"] is False
