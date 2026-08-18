"""
Tests for the /dmr/push/raw endpoint.

Verifies that raw hotspot JSON is normalized server-side correctly,
so the relay script can be a pure proxy with no normalization logic.
"""


RAW_WPSD_ENTRY = {
    "callsign": "w1aw",
    "src": "3109999",
    "name": "Hiram Maxim",
    "dst": "3100",
    "slot": "2",
    "country": "United States",
    "start": "2026-08-17 12:00:00",
    "duration": "5",
}

RAW_BRANDMEISTER_ENTRY = {
    "callsign": "W2XYZ",
    "SourceID": "3109998",
    "sourceName": "Jane Smith",
    "DestinationID": "3100",
    "slot": 1,
    "start": 1750000000,
    "stop": 1750000007,
    "sourceState": "CT",
    "sourceCountry": "US",
}


class TestDmrPushRaw:
    def test_wpsd_entries_normalized_correctly(self, client, admin_headers, net):
        # Configure DMR for this net
        client.put(f"/nets/{net['id']}/dmr/config", json={
            "source_type": "wpsd",
            "hotspot_url": "http://wpsd.local/api",
            "direct_mode": False,
        }, headers=admin_headers)

        resp = client.post(f"/nets/{net['id']}/dmr/push/raw", json={
            "source": "wpsd",
            "entries": [RAW_WPSD_ENTRY],
        }, headers=admin_headers)
        assert resp.status_code == 204

        # Verify cached data is normalized
        cache = client.get(f"/nets/{net['id']}/dmr/cache", headers=admin_headers)
        assert cache.status_code == 200
        entry = cache.json()["entries"][0]
        assert entry["callsign"] == "W1AW"          # uppercased
        assert entry["dmr_id"] == "3109999"          # src → dmr_id
        assert entry["talk_group"] == "3100"         # dst → talk_group
        assert entry["timeslot"] == "TS2"            # slot → TS{slot}
        assert entry["region"] == "United States"    # country → region

    def test_brandmeister_entries_normalized_correctly(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/dmr/config", json={
            "source_type": "brandmeister",
            "talkgroup_id": 3100,
            "direct_mode": False,
        }, headers=admin_headers)

        resp = client.post(f"/nets/{net['id']}/dmr/push/raw", json={
            "source": "brandmeister",
            "entries": [RAW_BRANDMEISTER_ENTRY],
        }, headers=admin_headers)
        assert resp.status_code == 204

        cache = client.get(f"/nets/{net['id']}/dmr/cache", headers=admin_headers)
        entry = cache.json()["entries"][0]
        assert entry["callsign"] == "W2XYZ"
        assert entry["dmr_id"] == "3109998"          # SourceID → dmr_id
        assert entry["talk_group"] == "3100"         # DestinationID → talk_group
        assert entry["timeslot"] == "TS1"

    def test_unknown_source_returns_400(self, client, admin_headers, net):
        client.put(f"/nets/{net['id']}/dmr/config", json={
            "source_type": "wpsd", "hotspot_url": "http://x", "direct_mode": False,
        }, headers=admin_headers)

        resp = client.post(f"/nets/{net['id']}/dmr/push/raw", json={
            "source": "invalid_source",
            "entries": [RAW_WPSD_ENTRY],
        }, headers=admin_headers)
        assert resp.status_code == 400

    def test_filter_callsign_applied_after_normalization(self, client, admin_headers, net):
        """NCS callsign should be filtered out even when sent as raw lowercase."""
        client.put(f"/nets/{net['id']}/dmr/config", json={
            "source_type": "wpsd",
            "hotspot_url": "http://wpsd.local/api",
            "filter_callsign": "W1AW",
            "direct_mode": False,
        }, headers=admin_headers)

        resp = client.post(f"/nets/{net['id']}/dmr/push/raw", json={
            "source": "wpsd",
            "entries": [RAW_WPSD_ENTRY],  # callsign is "w1aw" (lowercase)
        }, headers=admin_headers)
        assert resp.status_code == 204

        cache = client.get(f"/nets/{net['id']}/dmr/cache", headers=admin_headers)
        assert cache.json()["entries"] == []

    def test_unauthenticated_cannot_push_raw(self, client, net):
        resp = client.post(f"/nets/{net['id']}/dmr/push/raw", json={
            "source": "wpsd", "entries": [],
        })
        assert resp.status_code == 401
