#!/usr/bin/env python3
"""
Demo Database Reset
===================
Wipes all application data and recreates a clean database with a single
demo admin account, then reloads the GMRS licence database from a local
archive (faster than re-downloading 54 MB each time).

Intended for the public demo site at nettrackerdemo.meskis.net.

Usage
-----
    python3 demo_reset.py

Cron (every 4 hours):
    0 */4 * * *  /opt/netcontrol/venv/bin/python3 /opt/netcontrol/demo_reset.py \\
                 >> /var/log/demo_reset.log 2>&1

Environment variables (read from .env)
---------------------------------------
    DATABASE_URL      PostgreSQL connection string (required)
    GMRS_ZIP_PATH     Path to the local l_gmrs.zip archive
                      Default: /opt/netcontrol/data/l_gmrs.zip
"""

import os
import subprocess
import sys
from datetime import datetime, timezone
from urllib.parse import urlparse

# ── Bootstrap ────────────────────────────────────────────────────────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("python-dotenv not found — activate the virtualenv first.")

load_dotenv(os.path.join(_HERE, ".env"))

DATABASE_URL = os.getenv("DATABASE_URL", "")
if not DATABASE_URL:
    sys.exit("DATABASE_URL not set in .env")

GMRS_ZIP_PATH = os.getenv("GMRS_ZIP_PATH", "/opt/netcontrol/data/l_gmrs.zip")
PYTHON = sys.executable   # same virtualenv python

# Demo admin credentials
DEMO_CALLSIGN = "D3MO"
DEMO_NAME     = "Demo Account"
DEMO_EMAIL    = "demo@nettrackerdemo.meskis.net"
DEMO_PASSWORD = "Abcd1234"


# ── Helpers ───────────────────────────────────────────────────────────────────

def log(msg: str):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"[{ts}] {msg}", flush=True)


# ── Steps ─────────────────────────────────────────────────────────────────────

def drop_and_recreate_schema():
    """Nuke all tables by dropping and recreating the public schema."""
    import psycopg2

    log("Dropping public schema…")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    cur = conn.cursor()
    cur.execute("DROP SCHEMA public CASCADE")
    cur.execute("CREATE SCHEMA public")
    cur.execute("GRANT ALL ON SCHEMA public TO PUBLIC")
    # Also grant to the DB user so the app can write
    db_user = urlparse(DATABASE_URL).username
    if db_user:
        cur.execute(f"GRANT ALL ON SCHEMA public TO {db_user}")
    cur.close()
    conn.close()
    log("Schema reset done")


def create_tables():
    """Recreate all tables via SQLAlchemy models."""
    log("Creating tables…")
    from database import engine, init_db
    init_db()
    log("Tables created")


def run_migrations():
    """Apply ALTER TABLE migrations that create_all() cannot handle."""
    log("Running migrations…")
    result = subprocess.run(
        [PYTHON, os.path.join(_HERE, "migrate.py")],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"Migration error (non-fatal):\n{result.stdout}{result.stderr}")
    else:
        log("Migrations applied")


def create_demo_user():
    """Insert the demo admin account."""
    log(f"Creating demo user {DEMO_CALLSIGN}…")
    try:
        from passlib.context import CryptContext
    except ImportError:
        sys.exit("passlib not found — activate the virtualenv first.")

    from database import SessionLocal
    from models import User

    pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
    hashed = pwd_context.hash(DEMO_PASSWORD)

    db = SessionLocal()
    try:
        user = User(
            callsign=DEMO_CALLSIGN,
            name=DEMO_NAME,
            email=DEMO_EMAIL,
            hashed_password=hashed,
            is_active=True,
            is_admin=True,
        )
        db.add(user)
        db.commit()
        log(f"Demo user created — callsign: {DEMO_CALLSIGN} / email: {DEMO_EMAIL}")
    except Exception as exc:
        db.rollback()
        log(f"ERROR creating demo user: {exc}")
        raise
    finally:
        db.close()


def load_gmrs_data():
    """Load GMRS licence database from local zip archive."""
    if not os.path.exists(GMRS_ZIP_PATH):
        log(f"WARNING: GMRS zip not found at {GMRS_ZIP_PATH} — skipping GMRS load")
        log("  To create the archive: python3 gmrs_sync.py --mode full")
        log(f"  Then move l_gmrs.zip to {GMRS_ZIP_PATH}")
        return

    log(f"Loading GMRS data from {GMRS_ZIP_PATH} …")
    result = subprocess.run(
        [PYTHON, os.path.join(_HERE, "gmrs_sync.py"), "--zip", GMRS_ZIP_PATH],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        log(f"GMRS load failed:\n{result.stderr}")
    else:
        # Print gmrs_sync output (already timestamped)
        for line in result.stdout.strip().splitlines():
            print(line, flush=True)
        log("GMRS data loaded")


# ── Main ──────────────────────────────────────────────────────────────────────

def main():
    log("=== Demo reset starting ===")
    try:
        drop_and_recreate_schema()
        create_tables()
        run_migrations()
        create_demo_user()
        load_gmrs_data()
        log("=== Demo reset complete ===")
    except Exception as exc:
        log(f"FATAL: {exc}")
        raise SystemExit(1)


if __name__ == "__main__":
    main()
