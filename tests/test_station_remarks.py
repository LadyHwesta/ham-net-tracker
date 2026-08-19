"""
Tests for per-net station remarks & preferred names (issue #14):
  GET    /nets/{id}/stations/{callsign}/remark
  PUT    /nets/{id}/stations/{callsign}/remark
  DELETE /nets/{id}/stations/{callsign}/remark

Also covers the preferred_name override showing up in:
  GET /nets/{id}/expected
"""


class TestUpsertRemark:
    def test_set_both_fields(self, client, admin_headers, net):
        resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "remark": "Net control backup",
            "preferred_name": "Bob",
        }, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["remark"] == "Net control backup"
        assert data["preferred_name"] == "Bob"
        assert data["callsign"] == "W7ABC"

    def test_set_preferred_name_only(self, client, admin_headers, net):
        resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["preferred_name"] == "Bob"
        assert data["remark"] is None

    def test_set_remark_only(self, client, admin_headers, net):
        resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "remark": "Uses handheld, weak signal",
        }, headers=admin_headers)
        assert resp.status_code == 200
        data = resp.json()
        assert data["remark"] == "Uses handheld, weak signal"
        assert data["preferred_name"] is None

    def test_callsign_stored_uppercase(self, client, admin_headers, net):
        resp = client.put(f"/nets/{net['id']}/stations/w7abc/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["callsign"] == "W7ABC"

    def test_update_existing_remark(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Robert",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["preferred_name"] == "Robert"

    def test_both_fields_blank_deletes_row(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
            "remark": "Some note",
        }, headers=admin_headers)

        resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "",
            "remark": "",
        }, headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json() is None

        get_resp = client.get(f"/nets/{net['id']}/stations/W7ABC/remark", headers=admin_headers)
        assert get_resp.json() is None


class TestGetRemark:
    def test_get_missing_remark_returns_null(self, client, admin_headers, net):
        resp = client.get(f"/nets/{net['id']}/stations/W7ZZZ/remark", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json() is None

    def test_get_existing_remark(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        resp = client.get(f"/nets/{net['id']}/stations/W7ABC/remark", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["preferred_name"] == "Bob"


class TestDeleteRemark:
    def test_delete_remark(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        resp = client.delete(f"/nets/{net['id']}/stations/W7ABC/remark", headers=admin_headers)
        assert resp.status_code == 204

        get_resp = client.get(f"/nets/{net['id']}/stations/W7ABC/remark", headers=admin_headers)
        assert get_resp.json() is None

    def test_delete_nonexistent_remark_is_noop(self, client, admin_headers, net):
        resp = client.delete(f"/nets/{net['id']}/stations/W7ZZZ/remark", headers=admin_headers)
        assert resp.status_code == 204


class TestPreferredNameOverride:
    """issue #14: preferred_name should override the FCC-derived name on the
    Expected Stations screen, without touching the stored Checkin.name."""

    def _make_expected_station(self, client, admin_headers, net, callsign, fcc_name):
        """Two check-ins across two sessions clears the default min_checkins=2 bar."""
        for _ in range(2):
            s = client.post(f"/nets/{net['id']}/sessions", json={}, headers=admin_headers).json()
            client.post(f"/sessions/{s['id']}/checkins", json={
                "callsign": callsign, "name": fcc_name, "has_traffic": False,
            }, headers=admin_headers)
            client.patch(f"/sessions/{s['id']}/end", headers=admin_headers)

    def test_expected_uses_fcc_name_by_default(self, client, admin_headers, net):
        self._make_expected_station(client, admin_headers, net, "W7ABC", "Robert Smith")

        resp = client.get(f"/nets/{net['id']}/expected", headers=admin_headers)
        assert resp.status_code == 200
        station = next(s for s in resp.json() if s["callsign"] == "W7ABC")
        assert station["name"] == "Robert Smith"

    def test_expected_uses_preferred_name_when_set(self, client, admin_headers, net):
        self._make_expected_station(client, admin_headers, net, "W7ABC", "Robert Smith")
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)

        resp = client.get(f"/nets/{net['id']}/expected", headers=admin_headers)
        assert resp.status_code == 200
        station = next(s for s in resp.json() if s["callsign"] == "W7ABC")
        assert station["name"] == "Bob"

    def test_stored_checkin_name_unaffected_by_preferred_name(self, client, admin_headers, net, session):
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        add = client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7ABC", "name": "Robert Smith", "has_traffic": False,
        }, headers=admin_headers)
        assert add.json()["name"] == "Robert Smith"


class TestHistoryPreferredNameOverride:
    """issue #14 follow-up: preferred name can be set/edited from the History
    view after a net has closed, and should override the name shown there too."""

    def test_history_uses_fcc_name_by_default(self, client, admin_headers, net, session):
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7ABC", "name": "Robert Smith", "has_traffic": False,
        }, headers=admin_headers)

        resp = client.get(f"/nets/{net['id']}/history", headers=admin_headers)
        assert resp.status_code == 200
        station = next(s for s in resp.json() if s["callsign"] == "W7ABC")
        assert station["name"] == "Robert Smith"

    def test_history_uses_preferred_name_when_set(self, client, admin_headers, net, session):
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7ABC", "name": "Robert Smith", "has_traffic": False,
        }, headers=admin_headers)
        client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)

        resp = client.get(f"/nets/{net['id']}/history", headers=admin_headers)
        assert resp.status_code == 200
        station = next(s for s in resp.json() if s["callsign"] == "W7ABC")
        assert station["name"] == "Bob"

    def test_history_preferred_name_settable_after_session_ends(self, client, admin_headers, net, session):
        """The whole point of this feature: editing works after the net has closed."""
        client.post(f"/sessions/{session['id']}/checkins", json={
            "callsign": "W7ABC", "name": "Robert Smith", "has_traffic": False,
        }, headers=admin_headers)
        client.patch(f"/sessions/{session['id']}/end", headers=admin_headers)

        put_resp = client.put(f"/nets/{net['id']}/stations/W7ABC/remark", json={
            "preferred_name": "Bob",
        }, headers=admin_headers)
        assert put_resp.status_code == 200

        resp = client.get(f"/nets/{net['id']}/history", headers=admin_headers)
        station = next(s for s in resp.json() if s["callsign"] == "W7ABC")
        assert station["name"] == "Bob"
