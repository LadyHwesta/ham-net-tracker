"""
Tests for ARES/ACES tactical positions and shift sign-on/off (issue #21).

Gated on the SESSION's is_activation flag, not just net.is_ares -- a
routine session on an ARES net must reject these exactly like a
non-ARES net would, and its own behavior (evac zone, expected stations,
checkins) must stay byte-for-byte unaffected.
"""


def _ares_net(client, headers, name="ARES Test Net"):
    resp = client.post("/nets", json={"name": name, "is_ares": True}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _activation_session(client, headers, net_id):
    resp = client.post(f"/nets/{net_id}/sessions", json={"is_activation": True}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _routine_session(client, headers, net_id):
    resp = client.post(f"/nets/{net_id}/sessions", json={}, headers=headers)
    assert resp.status_code == 201, resp.text
    return resp.json()


def _position(client, headers, tactical_callsign="SHELTER 1", **extra):
    anet = _ares_net(client, headers)
    activation = _activation_session(client, headers, anet["id"])
    resp = client.post(
        f"/sessions/{activation['id']}/tactical-positions",
        json={"tactical_callsign": tactical_callsign, **extra},
        headers=headers,
    )
    assert resp.status_code == 201, resp.text
    return anet, activation, resp.json()


class TestActivationFlag:
    def test_session_create_defaults_to_not_activation(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={}, headers=admin_headers)
        assert resp.json()["is_activation"] is False

    def test_activation_forced_false_on_non_ares_net(self, client, admin_headers, net):
        resp = client.post(f"/nets/{net['id']}/sessions", json={"is_activation": True}, headers=admin_headers)
        assert resp.json()["is_activation"] is False

    def test_activation_can_be_set_on_ares_net(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        resp = client.post(f"/nets/{anet['id']}/sessions", json={"is_activation": True}, headers=admin_headers)
        assert resp.json()["is_activation"] is True


class TestTacticalPositionCreation:
    def test_create_position_requires_activation_session(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        routine = _routine_session(client, admin_headers, anet["id"])
        resp = client.post(
            f"/sessions/{routine['id']}/tactical-positions",
            json={"tactical_callsign": "SHELTER 1"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_create_position_requires_ares_net(self, client, admin_headers, net):
        activation = client.post(
            f"/nets/{net['id']}/sessions", json={"is_activation": True}, headers=admin_headers
        ).json()
        resp = client.post(
            f"/sessions/{activation['id']}/tactical-positions",
            json={"tactical_callsign": "SHELTER 1"},
            headers=admin_headers,
        )
        assert resp.status_code == 400

    def test_create_position_fields(self, client, admin_headers):
        _anet, _activation, position = _position(
            client, admin_headers,
            tactical_callsign="shelter 1", location="123 Main St",
            assigned_callsign="w1abc", assigned_name="Alice",
        )
        assert position["tactical_callsign"] == "SHELTER 1"
        assert position["location"] == "123 Main St"
        assert position["assigned_callsign"] == "W1ABC"
        assert position["assigned_name"] == "Alice"
        assert position["current_callsign"] is None  # vacant until someone signs on

    def test_tactical_callsign_required(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        resp = client.post(
            f"/sessions/{activation['id']}/tactical-positions",
            json={"tactical_callsign": "   "},
            headers=admin_headers,
        )
        assert resp.status_code == 422

    def test_list_positions_ordered_by_creation(self, client, admin_headers):
        # NET CONTROL is auto-created at session start and always sorts first
        # (issue #21 follow-up) -- user-created positions follow in creation order.
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        client.post(f"/sessions/{activation['id']}/tactical-positions", json={"tactical_callsign": "COMMAND"}, headers=admin_headers)
        client.post(f"/sessions/{activation['id']}/tactical-positions", json={"tactical_callsign": "SHELTER 1"}, headers=admin_headers)
        resp = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers)
        assert resp.status_code == 200
        assert [p["tactical_callsign"] for p in resp.json()] == ["NET CONTROL", "COMMAND", "SHELTER 1"]


class TestSignOnOff:
    def test_sign_on_creates_checkin_and_sets_current_occupant(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        resp = client.post(
            f"/tactical-positions/{position['id']}/sign-on",
            json={"callsign": "w1abc", "name": "Alice"},
            headers=admin_headers,
        )
        assert resp.status_code == 201, resp.text
        checkin = resp.json()
        assert checkin["callsign"] == "W1ABC"
        assert checkin["tactical_position_id"] == position["id"]
        assert checkin["tactical_callsign"] == "SHELTER 1"
        assert checkin["signed_off_at"] is None

        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        shelter1 = next(p for p in positions if p["id"] == position["id"])
        assert shelter1["current_callsign"] == "W1ABC"
        assert shelter1["current_checkin_id"] == checkin["id"]

    def test_signing_on_again_signs_off_previous_occupant(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        first = client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers).json()
        second = client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W2DEF"}, headers=admin_headers).json()

        checkins = client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers).json()
        first_after = next(c for c in checkins if c["id"] == first["id"])
        assert first_after["signed_off_at"] is not None
        second_after = next(c for c in checkins if c["id"] == second["id"])
        assert second_after["signed_off_at"] is None

        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        shelter1 = next(p for p in positions if p["id"] == position["id"])
        assert shelter1["current_callsign"] == "W2DEF"

    def test_sign_off_vacates_with_no_new_checkin(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        before_count = len(client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers).json())

        resp = client.post(f"/tactical-positions/{position['id']}/sign-off", headers=admin_headers)
        assert resp.status_code == 200
        assert resp.json()["signed_off_at"] is not None

        after_count = len(client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers).json())
        assert after_count == before_count  # no new checkin created on sign-off

        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        shelter1 = next(p for p in positions if p["id"] == position["id"])
        assert shelter1["current_callsign"] is None

    def test_sign_off_when_already_vacant_404s(self, client, admin_headers):
        _anet, _activation, position = _position(client, admin_headers)
        resp = client.post(f"/tactical-positions/{position['id']}/sign-off", headers=admin_headers)
        assert resp.status_code == 404

    def test_same_callsign_can_hold_two_positions(self, client, admin_headers):
        anet, activation, position1 = _position(client, admin_headers)
        position2 = client.post(
            f"/sessions/{activation['id']}/tactical-positions", json={"tactical_callsign": "COMMAND"}, headers=admin_headers
        ).json()
        r1 = client.post(f"/tactical-positions/{position1['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        r2 = client.post(f"/tactical-positions/{position2['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        assert r1.status_code == 201
        assert r2.status_code == 201  # duplicate-callsign block on ham nets is bypassed here

    def test_same_callsign_can_resign_onto_same_position_later(self, client, admin_headers):
        _anet, _activation, position = _position(client, admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-off", headers=admin_headers)
        resp = client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        assert resp.status_code == 201

    def test_sign_on_rejected_on_ended_session(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        client.patch(f"/sessions/{activation['id']}/end", headers=admin_headers)
        resp = client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        assert resp.status_code == 400


class TestShiftHistory:
    def test_shift_history_ordering_and_denormalized_callsign(self, client, admin_headers):
        _anet, _activation, position = _position(client, admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W2DEF"}, headers=admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W3GHI"}, headers=admin_headers)

        resp = client.get(f"/tactical-positions/{position['id']}/shifts", headers=admin_headers)
        assert resp.status_code == 200
        shifts = resp.json()
        assert [s["callsign"] for s in shifts] == ["W1ABC", "W2DEF", "W3GHI"]
        assert shifts[0]["signed_off_at"] is not None
        assert shifts[1]["signed_off_at"] is not None
        assert shifts[2]["signed_off_at"] is None
        assert all(s["tactical_callsign"] == "SHELTER 1" for s in shifts)


class TestDeletePosition:
    def test_delete_position_keeps_checkin_history(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        checkin = client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers).json()

        resp = client.delete(f"/tactical-positions/{position['id']}", headers=admin_headers)
        assert resp.status_code == 204

        # The checkin itself is not cascade-deleted with the position (its
        # tactical_position_id is nulled via ON DELETE SET NULL in Postgres,
        # which the SQLite test DB doesn't enforce -- untested here for the
        # same reason no other ondelete="SET NULL" column is in this suite).
        checkins = client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers).json()
        kept = next(c for c in checkins if c["id"] == checkin["id"])
        assert kept["callsign"] == "W1ABC"

        # NET CONTROL (auto-created) remains -- only the user-created position was removed.
        resp = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers)
        remaining = resp.json()
        assert len(remaining) == 1
        assert remaining[0]["is_net_control"] is True


class TestListCheckinsIncludesTactical:
    def test_list_checkins_includes_tactical_callsign(self, client, admin_headers):
        _anet, activation, position = _position(client, admin_headers)
        client.post(f"/tactical-positions/{position['id']}/sign-on", json={"callsign": "W1ABC"}, headers=admin_headers)

        resp = client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers)
        checkin = resp.json()[0]
        assert checkin["tactical_callsign"] == "SHELTER 1"

    def test_plain_checkin_has_no_tactical_callsign(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        client.post(f"/sessions/{activation['id']}/checkins", json={"callsign": "W1XYZ"}, headers=admin_headers)
        resp = client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers)
        assert resp.json()[0]["tactical_callsign"] is None


class TestRoutineAresSessionUnaffected:
    """A routine (non-activation) session on an ARES net must behave exactly
    as it did before this feature -- issue #21's core constraint."""

    def test_expected_endpoint_unaffected(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        routine = _routine_session(client, admin_headers, anet["id"])
        client.post(
            f"/sessions/{routine['id']}/checkins",
            json={"callsign": "W1ABC", "evac_zone": "Zone A"},
            headers=admin_headers,
        )
        resp = client.get(f"/nets/{anet['id']}/expected?min_checkins=1", headers=admin_headers)
        assert resp.status_code == 200

    def test_checkin_has_no_tactical_fields_set(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        routine = _routine_session(client, admin_headers, anet["id"])
        client.post(
            f"/sessions/{routine['id']}/checkins",
            json={"callsign": "W1ABC", "evac_zone": "Zone A"},
            headers=admin_headers,
        )
        resp = client.get(f"/sessions/{routine['id']}/checkins", headers=admin_headers)
        checkin = resp.json()[0]
        assert checkin["tactical_position_id"] is None
        assert checkin["tactical_callsign"] is None
        assert checkin["evac_zone"] == "Zone A"


class TestPermissions:
    def test_non_member_cannot_create_position(self, client, admin_headers, user_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        resp = client.post(
            f"/sessions/{activation['id']}/tactical-positions",
            json={"tactical_callsign": "SHELTER 1"},
            headers=user_headers,
        )
        assert resp.status_code == 403

    def test_non_member_cannot_sign_on(self, client, admin_headers, user_headers):
        _anet, _activation, position = _position(client, admin_headers)
        resp = client.post(
            f"/tactical-positions/{position['id']}/sign-on",
            json={"callsign": "W1ABC"},
            headers=user_headers,
        )
        assert resp.status_code == 403

    def test_non_member_cannot_list_positions(self, client, admin_headers, user_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        resp = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=user_headers)
        assert resp.status_code == 403


class TestScheduledStart:
    def test_scheduled_start_stored_and_returned(self, client, admin_headers):
        _anet, _activation, position = _position(
            client, admin_headers, scheduled_start="2026-09-01T14:00:00Z",
        )
        assert position["scheduled_start"] is not None
        assert position["scheduled_start"].startswith("2026-09-01T14:00:00")

    def test_scheduled_start_optional(self, client, admin_headers):
        _anet, _activation, position = _position(client, admin_headers)
        assert position["scheduled_start"] is None


class TestNetControlPosition:
    """Net Control is auto-created as a tactical position at activation session
    start and hands off through the same sign-on/off flow as any other position
    (issue #21 follow-up: a single day-level NCS wasn't enough once Net Control
    itself rotates mid-activation)."""

    def test_auto_created_on_activation_session_start(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        assert len(positions) == 1
        nc = positions[0]
        assert nc["is_net_control"] is True
        assert nc["tactical_callsign"] == "NET CONTROL"

    def test_auto_signed_on_from_session_starter_when_no_schedule_signup(self, client, admin_headers):
        # No schedule sign-up exists, so _duty_labels_for_session falls back to
        # whoever started the session (W1ADMIN) -- Net Control should already be
        # live the moment the activation begins, not sitting vacant.
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        nc = positions[0]
        assert nc["current_callsign"] == "W1ADMIN"
        assert nc["current_checkin_id"] is not None

    def test_not_created_for_routine_session(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        routine = _routine_session(client, admin_headers, anet["id"])
        # No tactical-positions access at all on a routine session (400), so there's
        # no way a NET CONTROL position could have been created for it either.
        resp = client.get(f"/sessions/{routine['id']}/tactical-positions", headers=admin_headers)
        assert resp.status_code == 400

    def test_cannot_be_deleted(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        nc = positions[0]
        resp = client.delete(f"/tactical-positions/{nc['id']}", headers=admin_headers)
        assert resp.status_code == 400

    def test_sorted_first_regardless_of_creation_order(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        client.post(f"/sessions/{activation['id']}/tactical-positions", json={"tactical_callsign": "SHELTER 1"}, headers=admin_headers)
        client.post(f"/sessions/{activation['id']}/tactical-positions", json={"tactical_callsign": "COMMAND"}, headers=admin_headers)
        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        assert positions[0]["is_net_control"] is True
        assert [p["tactical_callsign"] for p in positions[1:]] == ["SHELTER 1", "COMMAND"]

    def test_hands_off_via_sign_on_like_any_other_position(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        nc = positions[0]
        assert nc["current_callsign"] == "W1ADMIN"

        resp = client.post(f"/tactical-positions/{nc['id']}/sign-on", json={"callsign": "W2NEXT"}, headers=admin_headers)
        assert resp.status_code == 201

        positions = client.get(f"/sessions/{activation['id']}/tactical-positions", headers=admin_headers).json()
        nc = positions[0]
        assert nc["current_callsign"] == "W2NEXT"

        shifts = client.get(f"/tactical-positions/{nc['id']}/shifts", headers=admin_headers).json()
        assert [s["callsign"] for s in shifts] == ["W1ADMIN", "W2NEXT"]
        assert shifts[0]["signed_off_at"] is not None
        assert shifts[1]["signed_off_at"] is None

    def test_list_checkins_includes_net_control_signon(self, client, admin_headers):
        anet = _ares_net(client, admin_headers)
        activation = _activation_session(client, admin_headers, anet["id"])
        checkins = client.get(f"/sessions/{activation['id']}/checkins", headers=admin_headers).json()
        assert len(checkins) == 1
        assert checkins[0]["callsign"] == "W1ADMIN"
        assert checkins[0]["tactical_callsign"] == "NET CONTROL"
