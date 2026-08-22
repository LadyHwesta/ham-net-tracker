#!/usr/bin/env python3
"""
NetControl Online — DMR relay script

Polls a local WPSD, Pi-Star, or BrandMeister hotspot and forwards raw last-heard
data to the NetControl Online backend.  All normalization happens server-side; this
script is intentionally a thin fetch-and-forward proxy.

Usage
-----
    python3 dmr_relay.py [options]

Required options (or set matching environment variables):
    --server        NetControl Online base URL, e.g. https://tracker.netcontrol.online
    --token         API token (create one under Account → API Tokens)
    --net-id        Net ID to push data to (shown in the net's URL)
    --hotspot-url   Full URL of the hotspot API endpoint

Optional:
    --source        wpsd | pistar | brandmeister  (default: wpsd)
    --interval      Poll interval in seconds (default: 30)
    --limit         Max entries to fetch (default: 30)

Environment variables
---------------------
    NT_SERVER, NT_TOKEN, NT_NET_ID, NT_HOTSPOT_URL, NT_SOURCE, NT_INTERVAL, NT_LIMIT

Example
-------
    python3 dmr_relay.py \\
        --server https://tracker.netcontrol.online \\
        --token nt_abc123... \\
        --net-id 1 \\
        --hotspot-url http://wpsd.local/api/live/lastheard \\
        --source wpsd \\
        --interval 30
"""

import argparse
import os
import sys
import time
import logging

try:
    import requests
except ImportError:
    sys.exit("requests not installed — run: pip install requests")

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [dmr-relay] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger("dmr_relay")


def parse_args():
    p = argparse.ArgumentParser(description="NetControl Online DMR relay script")
    p.add_argument("--server",      default=os.getenv("NT_SERVER", ""))
    p.add_argument("--token",       default=os.getenv("NT_TOKEN", ""))
    p.add_argument("--net-id",      default=os.getenv("NT_NET_ID", ""), type=int)
    p.add_argument("--hotspot-url", default=os.getenv("NT_HOTSPOT_URL", ""))
    p.add_argument("--source",      default=os.getenv("NT_SOURCE", "wpsd"),
                   choices=["wpsd", "pistar", "brandmeister"])
    p.add_argument("--interval",    default=int(os.getenv("NT_INTERVAL", "30")), type=int)
    p.add_argument("--limit",       default=int(os.getenv("NT_LIMIT", "30")), type=int)
    args = p.parse_args()

    missing = [f"--{k}" for k, v in {
        "server": args.server,
        "token": args.token,
        "net-id": args.net_id,
        "hotspot-url": args.hotspot_url,
    }.items() if not v]
    if missing:
        p.error(f"Missing required options: {', '.join(missing)}")

    return args


def fetch_entries(hotspot_url: str, source: str, limit: int) -> list[dict]:
    """Fetch raw last-heard entries from the hotspot API."""
    params: dict = {}
    if source in ("wpsd", "pistar"):
        params = {"limit": limit, "names": "true", "country": "true"}
    elif source == "brandmeister":
        # BrandMeister talkgroup/rx endpoint — caller supplies the full URL
        params = {"limit": limit}

    r = requests.get(hotspot_url, params=params, timeout=10)
    r.raise_for_status()
    data = r.json()
    if not isinstance(data, list):
        log.warning("Hotspot returned non-list response: %s", type(data).__name__)
        return []
    return data[:limit]


def push_entries(server: str, token: str, net_id: int, source: str, entries: list[dict]) -> bool:
    """POST raw entries to the backend's /push/raw endpoint. Returns True on success."""
    url = f"{server.rstrip('/')}/nets/{net_id}/dmr/push/raw"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    payload = {"source": source, "entries": entries}
    try:
        r = requests.post(url, json=payload, headers=headers, timeout=15)
        if r.status_code == 204:
            return True
        log.warning("Push failed: HTTP %s — %s", r.status_code, r.text[:200])
        return False
    except Exception as exc:
        log.warning("Push error: %s", exc)
        return False


def main():
    args = parse_args()
    log.info(
        "Starting DMR relay: net=%s source=%s interval=%ss hotspot=%s",
        args.net_id, args.source, args.interval, args.hotspot_url,
    )

    consecutive_errors = 0
    while True:
        try:
            entries = fetch_entries(args.hotspot_url, args.source, args.limit)
            if entries:
                ok = push_entries(args.server, args.token, args.net_id, args.source, entries)
                if ok:
                    log.info("Pushed %d entries", len(entries))
                    consecutive_errors = 0
                else:
                    consecutive_errors += 1
            else:
                log.info("No entries from hotspot")
                consecutive_errors = 0
        except Exception as exc:
            consecutive_errors += 1
            log.error("Relay error (%d consecutive): %s", consecutive_errors, exc)
            if consecutive_errors >= 10:
                log.error("Too many consecutive errors — check hotspot and server connectivity")

        time.sleep(args.interval)


if __name__ == "__main__":
    main()
