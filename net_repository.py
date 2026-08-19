"""
Push a net listing to the central Net Repository directory
(https://github.com/LadyHwesta/Net-Repository) — the "discover" side of
issue #12/#8. Shared by main.py (push on create/update) and the standalone
push_to_net_repository.py backfill script, so the request-building logic
lives in exactly one place.

Environment variables (read from .env)
---------------------------------------
    NET_REPOSITORY_URL        Base URL of the Net Repository instance, e.g.
                               https://mynetrepo.meskis.net
    NET_REPOSITORY_API_KEY    nr_-prefixed API key issued by that instance's
                               admin. The key's configured instance_url is
                               what Net Repository uses (server-side, not
                               anything sent here) to deduplicate submissions
                               by (instance_url, source_net_id) — so a net
                               already submitted is silently skipped rather
                               than resubmitted, making pushes safe to repeat.

Net Repository's submission endpoint has no "update" path — once a net has
a pending or published entry there, later edits here (schedule changes,
description tweaks, etc.) won't propagate automatically. Pushing again just
gets reported back as a no-op duplicate. See README.md for details.
"""

import logging
import os

import httpx

_log = logging.getLogger("ham_net_tracker.net_repository")

NET_REPOSITORY_URL = os.getenv("NET_REPOSITORY_URL", "").rstrip("/")
NET_REPOSITORY_API_KEY = os.getenv("NET_REPOSITORY_API_KEY", "")


def net_repository_configured() -> bool:
    return bool(NET_REPOSITORY_URL and NET_REPOSITORY_API_KEY)


def push_net(net) -> bool:
    """POST a net to Net Repository's submission queue. Returns True if the
    request was sent and accepted (202 — including the already-submitted/
    duplicate case), False if not configured, the net isn't public, or the
    request failed. Never raises — a Net Repository outage or misconfiguration
    must not block creating or editing a net locally."""
    if not net_repository_configured():
        return False
    if not net.public_listed:
        return False

    owner_callsign = net.owner.callsign if net.owner else None
    payload = {
        "name": net.name,
        "net_type": net.net_type,
        "frequency": net.frequency,
        "description": net.description,
        "dmr_talkgroup": net.dmr_talkgroup,
        "is_ares": net.is_ares,
        "contact_callsign": owner_callsign,
        "submitted_by_callsign": owner_callsign,
        "source_net_id": net.id,
        "schedules": [
            {
                "day_of_week": s.day_of_week,
                "start_time": s.start_time,
                "timezone": s.timezone,
            }
            for s in net.schedules
        ],
    }
    try:
        resp = httpx.post(
            f"{NET_REPOSITORY_URL}/nets/submit",
            json=payload,
            headers={"Authorization": f"Bearer {NET_REPOSITORY_API_KEY}"},
            timeout=10,
        )
        resp.raise_for_status()
        message = resp.json().get("message", "")
        _log.info("Pushed net %r (id=%s) to Net Repository: %s", net.name, net.id, message)
        return True
    except Exception as exc:
        _log.warning("Failed to push net %r (id=%s) to Net Repository: %s", net.name, net.id, exc)
        return False
