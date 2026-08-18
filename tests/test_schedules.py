"""
Tests for scheduling and Net Control / Broadcaster sign-ups:
  POST   /nets/{id}/schedules
  POST   /nets/{id}/signups
  GET    /nets/{id}/signups
  GET    /nets/{id}/upcoming
  GET    /sessions/{id}          — scheduled duty display
  GET    /public/active          — scheduled duty display (public)
"""

from datetime import date


def _schedule_for_today(client, headers, net_id):
    """Create a weekly schedule matching today's weekday, so `today` is a valid slot_date."""
    today = date.today()
    resp = client.post(f"/nets/{net_id}/schedules", json={
        "day_of_week": today.weekday(),
        "start_time": "19:30",
        "timezone": "UTC",
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json(), today


def _broadcast_net(client, headers, label="Amateur Radio Newsline"):
    resp = client.post("/nets", json={
        "name": "Newsline Net", "is_ares": False,
        "has_broadcast": True, "broadcast_label": label,
    }, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


class TestSignupRoles:
    def test_signup_defaults_to_net_control(self, client, admin_headers, net):
        sched, today = _schedule_for_today(client, admin_headers, net["id"])
        resp = client.post(f"/nets/{net['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "callsign": "W1AW",
        }, headers=admin_headers)
        assert resp.status_code == 201, resp.text
        assert resp.json()["role"] == "net_control"

    def test_broadcaster_rejected_when_net_has_no_broadcast(self, client, admin_headers, net):
        sched, today = _schedule_for_today(client, admin_headers, net["id"])
        resp = client.post(f"/nets/{net['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "broadcaster", "callsign": "W1AW",
        }, headers=admin_headers)
        assert resp.status_code == 400

    def test_net_control_and_broadcaster_fill_independently(self, client, admin_headers):
        bnet = _broadcast_net(client, admin_headers)
        sched, today = _schedule_for_today(client, admin_headers, bnet["id"])
        r1 = client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "net_control", "callsign": "W1AW",
        }, headers=admin_headers)
        assert r1.status_code == 201, r1.text
        r2 = client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "broadcaster", "callsign": "K2ABC",
        }, headers=admin_headers)
        assert r2.status_code == 201, r2.text
        assert r2.json()["role"] == "broadcaster"

    def test_duplicate_role_on_same_date_conflicts(self, client, admin_headers, net):
        sched, today = _schedule_for_today(client, admin_headers, net["id"])
        client.post(f"/nets/{net['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "callsign": "W1AW",
        }, headers=admin_headers)
        resp = client.post(f"/nets/{net['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "callsign": "K2ABC",
        }, headers=admin_headers)
        assert resp.status_code == 409

    def test_both_role_blocks_subsequent_single_role_signup(self, client, admin_headers):
        bnet = _broadcast_net(client, admin_headers)
        sched, today = _schedule_for_today(client, admin_headers, bnet["id"])
        r1 = client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "both", "callsign": "W1AW",
        }, headers=admin_headers)
        assert r1.status_code == 201, r1.text
        r2 = client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "broadcaster", "callsign": "K2ABC",
        }, headers=admin_headers)
        assert r2.status_code == 409

    def test_both_role_rejected_when_a_role_already_taken(self, client, admin_headers):
        bnet = _broadcast_net(client, admin_headers)
        sched, today = _schedule_for_today(client, admin_headers, bnet["id"])
        client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "net_control", "callsign": "W1AW",
        }, headers=admin_headers)
        resp = client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "both", "callsign": "K2ABC",
        }, headers=admin_headers)
        assert resp.status_code == 409

    def test_upcoming_lists_both_role_signups(self, client, admin_headers):
        bnet = _broadcast_net(client, admin_headers)
        sched, today = _schedule_for_today(client, admin_headers, bnet["id"])
        client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "net_control", "callsign": "W1AW",
        }, headers=admin_headers)
        client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "broadcaster", "callsign": "K2ABC",
        }, headers=admin_headers)
        resp = client.get(f"/nets/{bnet['id']}/upcoming?weeks=1", headers=admin_headers)
        assert resp.status_code == 200
        slot = next(s for s in resp.json() if s["slot_date"] == str(today))
        roles = {sig["role"] for sig in slot["signups"]}
        assert roles == {"net_control", "broadcaster"}


class TestDutyDisplay:
    def test_session_shows_scheduled_net_control(self, client, admin_headers, net):
        sched, today = _schedule_for_today(client, admin_headers, net["id"])
        client.post(f"/nets/{net['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "callsign": "W1AW", "name": "Alice",
        }, headers=admin_headers)
        s = client.post(f"/nets/{net['id']}/sessions", json={"name": "Test"}, headers=admin_headers).json()
        resp = client.get(f"/sessions/{s['id']}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ncs_callsign"] == "W1AW"
        assert resp.json()["ncs_name"] == "Alice"

    def test_session_falls_back_to_operator_when_no_signup(self, client, admin_headers, session):
        resp = client.get(f"/sessions/{session['id']}", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["ncs_callsign"] == "W1ADMIN"

    def test_public_active_shows_broadcaster(self, client, admin_headers):
        bnet = _broadcast_net(client, admin_headers)
        sched, today = _schedule_for_today(client, admin_headers, bnet["id"])
        client.post(f"/nets/{bnet['id']}/signups", json={
            "schedule_id": sched["id"], "slot_date": str(today), "role": "broadcaster",
            "callsign": "K2ABC", "name": "Bob",
        }, headers=admin_headers)
        client.post(f"/nets/{bnet['id']}/sessions", json={"name": "Live"}, headers=admin_headers)
        resp = client.get("/public/active")
        assert resp.status_code == 200
        row = next(r for r in resp.json() if r["net_name"] == "Newsline Net")
        assert row["broadcaster_callsign"] == "K2ABC"
        assert row["broadcaster_name"] == "Bob"
        assert row["broadcast_label"] == "Amateur Radio Newsline"
