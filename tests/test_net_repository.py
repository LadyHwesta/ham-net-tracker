"""
Tests for pushing public nets to the central Net Repository directory
(issue #12): net_repository.push_net(), and its wiring into
POST /nets and PUT /nets/{id}.

NET_REPOSITORY_URL/NET_REPOSITORY_API_KEY are forced empty for the whole
suite (see conftest.py) so a real .env can't make these tests hit the live
service. The net_repo_configured fixture turns push_net "on" for tests that
need it, and pushed_nets intercepts httpx.post so no real network call is
made.
"""
import net_repository


class TestCreateNetPush:
    def test_no_push_when_not_configured(self, client, admin_headers, pushed_nets):
        client.post("/nets", json={"name": "Test Net", "public_listed": True}, headers=admin_headers)
        assert pushed_nets == []

    def test_no_push_when_not_public(self, client, admin_headers, net_repo_configured, pushed_nets):
        client.post("/nets", json={"name": "Private Net", "public_listed": False}, headers=admin_headers)
        assert pushed_nets == []

    def test_pushes_public_net_on_create(self, client, admin_headers, net_repo_configured, pushed_nets):
        resp = client.post("/nets", json={
            "name": "Monday Night Net", "frequency": "146.520 MHz", "description": "Weekly check-in",
            "net_type": "ham", "is_ares": False, "dmr_talkgroup": "3117", "public_listed": True,
        }, headers=admin_headers)
        net_id = resp.json()["id"]

        assert len(pushed_nets) == 1
        call = pushed_nets[0]
        assert call["url"] == "https://netrepo.example.com/nets/submit"
        assert call["headers"] == {"Authorization": "Bearer nr_testkey"}
        payload = call["json"]
        assert payload["name"] == "Monday Night Net"
        assert payload["frequency"] == "146.520 MHz"
        assert payload["description"] == "Weekly check-in"
        assert payload["net_type"] == "ham"
        assert payload["is_ares"] is False
        assert payload["dmr_talkgroup"] == "3117"
        assert payload["source_net_id"] == net_id
        assert payload["contact_callsign"] == "W1ADMIN"
        assert payload["submitted_by_callsign"] == "W1ADMIN"
        assert payload["schedules"] == []


class TestUpdateNetPush:
    def test_pushes_when_toggled_public(self, client, admin_headers, net_repo_configured, pushed_nets):
        resp = client.post("/nets", json={"name": "Test Net", "public_listed": False}, headers=admin_headers)
        net_id = resp.json()["id"]
        assert pushed_nets == []  # not pushed while private

        client.put(f"/nets/{net_id}", json={"name": "Test Net", "public_listed": True}, headers=admin_headers)
        assert len(pushed_nets) == 1
        assert pushed_nets[0]["json"]["source_net_id"] == net_id

    def test_no_push_when_staying_private(self, client, admin_headers, net_repo_configured, pushed_nets):
        resp = client.post("/nets", json={"name": "Test Net", "public_listed": False}, headers=admin_headers)
        net_id = resp.json()["id"]

        client.put(f"/nets/{net_id}", json={"name": "Renamed Net", "public_listed": False}, headers=admin_headers)
        assert pushed_nets == []


class TestPushNetFunction:
    def test_push_fails_gracefully_on_http_error(self, client, admin_headers, net_repo_configured, monkeypatch):
        """A Net Repository outage must not break creating a net locally."""
        import httpx

        def fake_post(*args, **kwargs):
            raise httpx.ConnectError("connection refused")

        monkeypatch.setattr(httpx, "post", fake_post)

        resp = client.post("/nets", json={"name": "Test Net", "public_listed": True}, headers=admin_headers)
        assert resp.status_code == 201  # net creation still succeeds

    def test_not_configured_returns_false(self):
        assert net_repository.net_repository_configured() is False
