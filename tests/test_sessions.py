"""
Tests for session management endpoints:
  GET    /nets/{id}/sessions
  POST   /nets/{id}/sessions
  GET    /sessions/{id}
  PATCH  /sessions/{id}/end
  PATCH  /sessions/{id}/rename
  DELETE /sessions/{id}
  GET    /sessions/{id}/summary
"""


class TestSessionLifecycle:
    def test_start_session(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={
            "name": "Evening Net", "notes": None,
        }, headers=admin_headers)
        assert resp.status_code == 201
        data = resp.json()
        assert data["net_id"] == net["id"]
        assert data["ended_at"] is None
        assert data["checkin_count"] == 0

    def test_start_session_without_name(self, client, admin_headers, net):
        """Name is optional — session should still be created."""
        resp = client.post(f"/nets/{net['id']}/sessions", json={}, headers=admin_headers)
        assert resp.status_code == 201
        assert resp.json()["name"] is None

    def test_list_sessions(self, client, admin_headers, net, session):
        resp = client.get(f"/nets/{net['id']}/sessions", headers=admin_headers)
        assert resp.status_code == 200
        ids = [s["id"] for s in resp.json()]
        assert session["id"] in ids

    def test_get_session(self, client, admin_headers, session):
        resp = client.get(f"/sessions/{session['id']}", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == session["id"]
        assert "checkin_count" in data

    def test_get_nonexistent_session_returns_404(self, client, admin_headers):
        resp = client.get("/sessions/99999", headers=admin_headers)
        assert resp.status_code == 404

    def test_end_session(self, client, admin_headers, session):
        resp = client.patch(f"/sessions/{session['id']}/end", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ended_at"] is not None

    def test_end_already_ended_session_returns_400(self, client, admin_headers, session):
        client.patch(f"/sessions/{session['id']}/end", headers=admin_headers)
        resp = client.patch(f"/sessions/{session['id']}/end", headers=admin_headers)
        assert resp.status_code == 400

    def test_rename_session(self, client, admin_headers, session):
        resp = client.patch(
            f"/sessions/{session['id']}/rename",
            json={"name": "Renamed Evening Net"},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] == "Renamed Evening Net"

    def test_rename_to_none_clears_name(self, client, admin_headers, session):
        resp = client.patch(
            f"/sessions/{session['id']}/rename",
            json={"name": None},
            headers=admin_headers,
        )
        assert resp.status_code == 200
        assert resp.json()["name"] is None

    def test_delete_session(self, client, admin_headers, session):
        resp = client.delete(f"/sessions/{session['id']}", headers=admin_headers)
        assert resp.status_code == 204

    def test_delete_session_removes_it(self, client, admin_headers, session):
        client.delete(f"/sessions/{session['id']}", headers=admin_headers)
        resp = client.get(f"/sessions/{session['id']}", headers=admin_headers)
        assert resp.status_code == 404


class TestSessionSummary:
    def test_summary_after_end(self, client, admin_headers, session):
        # Add a couple of check-ins
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7AAA", "has_traffic": False,
        }, headers=admin_headers)
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7BBB", "has_traffic": True,
        }, headers=admin_headers)
        # End session
        client.patch(f"/sessions/{session['id']}/end", headers=admin_headers)
        # Fetch summary
        resp = client.get(f"/sessions/{session['id']}/summary", headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_checkins"] == 2
        assert data["traffic_count"] == 1

    def test_summary_available_on_live_session(self, client, admin_headers, session):
        """Summary should also work while session is still live."""
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7LIV", "has_traffic": False,
        }, headers=admin_headers)
        resp = client.get(f"/sessions/{session['id']}/summary", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["total_checkins"] == 1


class TestOfflineNetEntry:
    """Backfilling a net that already happened with no access to the web tool
    (issue #20) -- created already "ended" at the reported date/time, but
    add_checkin() specifically lets checkins through anyway, each stamped
    with that date/time rather than real "now"."""

    def test_requires_occurred_at(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={"is_offline": True}, headers=admin_headers)
        assert resp.status_code == 400

    def test_creates_session_at_reported_time_already_ended(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
        }, headers=admin_headers)
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["is_offline"] is True
        assert data["started_at"].startswith("2026-08-01T19:00:00")
        assert data["ended_at"] is not None
        assert data["ended_at"].startswith("2026-08-01T19:00:00")

    def test_normal_session_is_offline_defaults_false(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={}, headers=admin_headers)
        assert resp.json()["is_offline"] is False

    def test_ncs_override_reflected_in_get_session(self, client, admin_headers, net):
        created = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
            "ncs_override_callsign": "w1abc", "ncs_override_name": "Alice",
        }, headers=admin_headers).json()
        resp = client.get(f"/sessions/{created['id']}", headers=admin_headers)
        data = resp.json()
        assert data["ncs_callsign"] == "W1ABC"
        assert data["ncs_name"] == "Alice"

    def test_broadcaster_override_still_works_for_offline_entry(self, client, admin_headers, net):
        # has_broadcast isn't required for the override field to be stored/resolved --
        # matches the existing (issue #17) broadcaster-override behavior generally.
        created = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
            "broadcaster_override_callsign": "k7xyz", "broadcaster_override_name": "Bob",
        }, headers=admin_headers).json()
        resp = client.get(f"/sessions/{created['id']}", headers=admin_headers)
        data = resp.json()
        assert data["broadcaster_callsign"] == "K7XYZ"
        assert data["broadcaster_name"] == "Bob"

    def test_checkin_allowed_despite_session_already_ended(self, client, admin_headers, net):
        created = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
        }, headers=admin_headers).json()
        assert created["ended_at"] is not None
        resp = client.post(f"/sessions/{created['id']}/checkins", json={"callsign": "W2DEF"}, headers=admin_headers)
        assert resp.status_code == 201, resp.text

    def test_checkin_timestamp_matches_reported_net_time_not_now(self, client, admin_headers, net):
        created = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
        }, headers=admin_headers).json()
        checkin = client.post(f"/sessions/{created['id']}/checkins", json={"callsign": "W2DEF"}, headers=admin_headers).json()
        assert checkin["checked_in_at"].startswith("2026-08-01T19:00:00")

    def test_multiple_checkins_all_share_the_reported_time(self, client, admin_headers, net):
        created = client.post(f"/nets/{net['id']}/sessions", json={
            "is_offline": True, "occurred_at": "2026-08-01T19:00:00Z",
        }, headers=admin_headers).json()
        client.post(f"/sessions/{created['id']}/checkins", json={"callsign": "W3ONE"}, headers=admin_headers)
        client.post(f"/sessions/{created['id']}/checkins", json={"callsign": "W3TWO"}, headers=admin_headers)
        checkins = client.get(f"/sessions/{created['id']}/checkins", headers=admin_headers).json()
        assert len(checkins) == 2
        assert all(c["checked_in_at"].startswith("2026-08-01T19:00:00") for c in checkins)


class TestSessionPermissions:
    def test_unauthenticated_cannot_start_session(self, client, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={})
        assert resp.status_code == 401

    def test_unauthenticated_cannot_get_session(self, client, session):
        resp = client.get(f"/sessions/{session['id']}")
        assert resp.status_code == 401
