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


class TestSessionPermissions:
    def test_unauthenticated_cannot_start_session(self, client, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={})
        assert resp.status_code == 401

    def test_unauthenticated_cannot_get_session(self, client, session):
        resp = client.get(f"/sessions/{session['id']}")
        assert resp.status_code == 401
