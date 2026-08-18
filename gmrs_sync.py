#!/usr/bin/env python3
"""
GMRS License Database Sync
===========================
Downloads the FCC Universal Licensing System (ULS) weekly full database for
the General Mobile Radio Service (service code ZA) and upserts the active
licences into the local gmrs_licenses table.

Usage
-----
    python3 gmrs_sync.py              # sync once
    python3 gmrs_sync.py --dry-run    # download and parse but don't write DB

Schedule via cron for weekly updates (Sunday midnight, for example):
    0 3 * * 0 /opt/netcontrol/venv/bin/python3 /opt/netcontrol/gmrs_sync.py >> /var/log/gmrs_sync.log 2>&1

FCC ULS file format (pipe-delimited, no header row)
----------------------------------------------------
HD.dat — License Header
  [0]  record_type         ("HD")
  [1]  unique_system_identifier
  [4]  call_sign
  [5]  license_status      A=Active  E=Expired  C=Cancelled  T=Terminated
  [8]  expired_date        MM/DD/YYYY

EN.dat — Entity (name / address)
  [0]  record_type         ("EN")
  [1]  unique_system_identifier
  [4]  call_sign
  [5]  entity_type         L=Licensee  (others are contacts, etc.)
  [7]  entity_name         (used for club/organisation licences)
  [8]  first_name
  [10] last_name
  [17] state               two-letter abbreviation
"""

import argparse
import io
import os
import sys
import zipfile
from datetime import datetime, timezone

# ---------------------------------------------------------------------------
# FCC ULS download URL for GMRS (service code ZA) — weekly full database
# If the FCC ever changes this URL update it here.
# ---------------------------------------------------------------------------
# FCC ULS weekly full download for GMRS (General Mobile Radio Service).
# Source: https://www.fcc.gov/uls/transactions/daily-weekly
# The Saturday file is the weekly full database rebuild.
GMRS_ULS_URL = "https://data.fcc.gov/download/pub/uls/daily/l_gm_sat.zip"


# ---------------------------------------------------------------------------
# Bootstrap dependencies
# ---------------------------------------------------------------------------
try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("python-dotenv not found — activate the virtualenv first.")

try:
    import psycopg2
    import psycopg2.extras
except ImportError:
    sys.exit("psycopg2 not found — activate the virtualenv first.")

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set in .env")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


def download_gmrs_zip() -> bytes:
    """
    Download the FCC GMRS weekly full database zip.
    Uses system curl with no extra headers — the FCC blocks Python's requests
    and curl with browser-like headers, but accepts plain curl.
    """
    import subprocess, shutil, tempfile

    _log(f"Downloading GMRS database …")
    _log(f"  URL: {GMRS_ULS_URL}")

    curl = shutil.which("curl")
    if not curl:
        raise RuntimeError(
            "curl not found on PATH. Install it or download the file manually and use --zip."
        )

    with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp:
        tmp_path = tmp.name

    try:
        result = subprocess.run(
            [curl, "-L", "--fail", "--silent", "--show-error",
             "--max-time", "120", "-o", tmp_path, GMRS_ULS_URL],
            capture_output=True,
            timeout=130,
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"curl exited {result.returncode}: {result.stderr.decode().strip()}\n"
                "Download the file manually from "
                "https://www.fcc.gov/uls/transactions/daily-weekly and use --zip."
            )
        with open(tmp_path, "rb") as fh:
            data = fh.read()
        _log(f"  Downloaded {len(data):,} bytes")
        return data
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def parse_gmrs_zip(zip_bytes: bytes) -> dict:
    """
    Parse HD.dat and EN.dat from the ULS zip.

    Returns a dict:  callsign → {"name": str, "state": str, "expires": str, "status": str}
    Only includes records where license_status == 'A' (Active).
    """
    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zf:
        names_lower = {n.lower(): n for n in zf.namelist()}

        # ── HD.dat — license header ──────────────────────────────────────────
        hd_name = names_lower.get("hd.dat")
        if not hd_name:
            raise RuntimeError("HD.dat not found in ULS zip")

        hd: dict[str, dict] = {}   # call_sign → {status, expires}
        _log("Parsing HD.dat …")
        with zf.open(hd_name) as f:
            for raw in f:
                line = raw.decode("latin-1").rstrip("\r\n")
                cols = line.split("|")
                if len(cols) < 9:
                    continue
                if cols[0] != "HD":
                    continue
                call   = cols[4].strip().upper()
                status = cols[5].strip()
                if not call:
                    continue
                # Keep active licences; also track expired ones so we can
                # update status in the DB if a licence was once active.
                hd[call] = {
                    "status":  status,
                    "expires": cols[8].strip() or None,
                }
        _log(f"  {len(hd):,} HD records read")

        # ── EN.dat — entity (name + address) ────────────────────────────────
        en_name = names_lower.get("en.dat")
        if not en_name:
            raise RuntimeError("EN.dat not found in ULS zip")

        en: dict[str, dict] = {}   # call_sign → {name, state}
        _log("Parsing EN.dat …")
        with zf.open(en_name) as f:
            for raw in f:
                line = raw.decode("latin-1").rstrip("\r\n")
                cols = line.split("|")
                if len(cols) < 18:
                    continue
                if cols[0] != "EN":
                    continue
                entity_type = cols[5].strip()
                if entity_type != "L":    # L = Licensee (skip contacts etc.)
                    continue
                call = cols[4].strip().upper()
                if not call:
                    continue
                entity_name = cols[7].strip()
                first_name  = cols[8].strip()
                last_name   = cols[10].strip()
                state       = cols[17].strip() or None
                if entity_name:
                    name = entity_name.title()
                elif first_name or last_name:
                    name = f"{first_name} {last_name}".strip().title() or None
                else:
                    name = None
                en[call] = {"name": name, "state": state}
        _log(f"  {len(en):,} EN records read")

    # ── Join HD + EN ─────────────────────────────────────────────────────────
    records: dict[str, dict] = {}
    for call, hd_row in hd.items():
        en_row  = en.get(call, {})
        records[call] = {
            "name":    en_row.get("name"),
            "state":   en_row.get("state"),
            "expires": hd_row["expires"],
            "status":  hd_row["status"],
        }
    _log(f"  {len(records):,} combined records (active + inactive)")
    active = sum(1 for r in records.values() if r["status"] == "A")
    _log(f"  {active:,} active GMRS licences")
    return records


def upsert_to_db(records: dict, dry_run: bool = False):
    """Upsert parsed records into the gmrs_licenses table."""
    now = datetime.now(timezone.utc)

    if dry_run:
        _log(f"[DRY RUN] Would upsert {len(records):,} records — skipping DB write")
        return

    _log(f"Connecting to database …")
    conn = psycopg2.connect(DATABASE_URL)
    cur  = conn.cursor()

    _log(f"Upserting {len(records):,} records into gmrs_licenses …")
    rows = [
        (call, r["name"], r["state"], r["expires"], r["status"], now)
        for call, r in records.items()
    ]

    psycopg2.extras.execute_values(
        cur,
        """
        INSERT INTO gmrs_licenses (callsign, licensee_name, state, expires, status, synced_at)
        VALUES %s
        ON CONFLICT (callsign) DO UPDATE SET
            licensee_name = EXCLUDED.licensee_name,
            state         = EXCLUDED.state,
            expires       = EXCLUDED.expires,
            status        = EXCLUDED.status,
            synced_at     = EXCLUDED.synced_at
        """,
        rows,
        page_size=5000,
    )

    # Record sync timestamp in system_settings
    cur.execute(
        """
        INSERT INTO system_settings (key, value, updated_at)
        VALUES ('gmrs_db_synced_at', %s, NOW())
        ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, updated_at = NOW()
        """,
        (now.isoformat(),),
    )

    conn.commit()
    cur.close()
    conn.close()
    _log(f"Done — {len(records):,} records written")


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync FCC GMRS database into local DB")
    parser.add_argument("--dry-run", action="store_true",
                        help="Download and parse but do not write to the database")
    parser.add_argument("--url", default=GMRS_ULS_URL,
                        help="Override the FCC ULS download URL")
    parser.add_argument("--zip", metavar="FILE",
                        help="Use a local zip file instead of downloading (e.g. manually saved l_gm_sat.zip)")
    args = parser.parse_args()

    if args.url != GMRS_ULS_URL:
        globals()["GMRS_ULS_URL"] = args.url

    _log("=== GMRS Sync starting ===")
    try:
        if args.zip:
            _log(f"Using local file: {args.zip}")
            with open(args.zip, "rb") as fh:
                zip_bytes = fh.read()
            _log(f"  Read {len(zip_bytes):,} bytes")
        else:
            zip_bytes = download_gmrs_zip()
        records = parse_gmrs_zip(zip_bytes)
        upsert_to_db(records, dry_run=args.dry_run)
        _log("=== GMRS Sync complete ===")
    except Exception as exc:
        _log(f"ERROR: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
