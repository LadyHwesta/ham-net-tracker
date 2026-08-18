#!/usr/bin/env python3
"""
Ham Net Tracker — database migration runner

Applies all schema changes that SQLAlchemy's create_all() cannot handle
(i.e. adding columns to existing tables).  Safe to re-run at any time;
every statement uses IF NOT EXISTS or is otherwise idempotent.

New tables are created automatically by the app on startup (via
Base.metadata.create_all).  This script only manages ALTER TABLE work.

Usage
-----
    python3 migrate.py

Reads DATABASE_URL from the .env file in the same directory (same as
the main app).  Run as the service user so DB permissions match:

    sudo -u netcontrol python3 /opt/netcontrol/migrate.py
"""

import os
import sys

try:
    import psycopg2
except ImportError:
    sys.exit("psycopg2 not found — activate the virtualenv first, or:\n"
             "  pip install psycopg2-binary --break-system-packages")

try:
    from dotenv import load_dotenv
except ImportError:
    sys.exit("python-dotenv not found — activate the virtualenv first.")

# ---------------------------------------------------------------------------
# Load config
# ---------------------------------------------------------------------------

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
DATABASE_URL = os.getenv("DATABASE_URL", "")

if not DATABASE_URL:
    sys.exit("DATABASE_URL not set in .env")

# ---------------------------------------------------------------------------
# Migration steps
# Each entry is (description, sql).  All statements must be idempotent.
# Add new entries at the bottom when extending the schema.
# ---------------------------------------------------------------------------

MIGRATIONS = [
    # ── Columns added after initial launch ──────────────────────────────────
    ("nets: ARES net flag",
     "ALTER TABLE nets ADD COLUMN IF NOT EXISTS is_ares BOOLEAN NOT NULL DEFAULT FALSE"),

    ("checkins: traffic flag",
     "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS has_traffic BOOLEAN NOT NULL DEFAULT FALSE"),

    ("checkins: ARES evacuation zone",
     "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS evac_zone VARCHAR(100)"),

    ("users: registration notification opt-in",
     "ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_new_registrations BOOLEAN NOT NULL DEFAULT FALSE"),

    # ── DMR integration ─────────────────────────────────────────────────────
    ("nets: default DMR talk group",
     "ALTER TABLE nets ADD COLUMN IF NOT EXISTS dmr_talkgroup VARCHAR(20)"),

    ("checkins: DMR talk group",
     "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS dmr_talkgroup VARCHAR(20)"),

    ("checkins: DMR region/country",
     "ALTER TABLE checkins ADD COLUMN IF NOT EXISTS dmr_region VARCHAR(100)"),

    # ── Tables created after some instances were already running ─────────────
    # (create_all handles these on fresh installs; listed here as documentation
    #  and as a safety net for instances that may have missed them)

    ("table: evac_zones",
     """CREATE TABLE IF NOT EXISTS evac_zones (
         id SERIAL PRIMARY KEY,
         net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
         callsign VARCHAR(12) NOT NULL,
         zone VARCHAR(100) NOT NULL,
         updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CONSTRAINT uq_evac_zone_net_callsign UNIQUE (net_id, callsign))"""),

    ("table: traffic_messages",
     """CREATE TABLE IF NOT EXISTS traffic_messages (
         id SERIAL PRIMARY KEY,
         session_id INTEGER NOT NULL REFERENCES net_sessions(id) ON DELETE CASCADE,
         msg_number VARCHAR(50),
         origin_callsign VARCHAR(12) NOT NULL,
         dest_info VARCHAR(200),
         msg_type VARCHAR(20) NOT NULL DEFAULT 'formal',
         status VARCHAR(20) NOT NULL DEFAULT 'received',
         notes TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""),

    ("table: station_remarks",
     """CREATE TABLE IF NOT EXISTS station_remarks (
         id SERIAL PRIMARY KEY,
         callsign VARCHAR(12) NOT NULL,
         net_id INTEGER REFERENCES nets(id) ON DELETE CASCADE,
         remark TEXT NOT NULL,
         updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
         updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CONSTRAINT uq_station_remark_callsign_net UNIQUE (callsign, net_id))"""),

    ("table: net_shares",
     """CREATE TABLE IF NOT EXISTS net_shares (
         id SERIAL PRIMARY KEY,
         net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
         user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CONSTRAINT uq_net_share_net_user UNIQUE (net_id, user_id))"""),

    ("table: net_schedules",
     """CREATE TABLE IF NOT EXISTS net_schedules (
         id SERIAL PRIMARY KEY,
         net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
         day_of_week INTEGER NOT NULL,
         start_time VARCHAR(5) NOT NULL,
         timezone VARCHAR(60) NOT NULL DEFAULT 'UTC',
         notes TEXT,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""),

    ("table: system_settings",
     """CREATE TABLE IF NOT EXISTS system_settings (
         key VARCHAR(100) PRIMARY KEY,
         value TEXT,
         updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""),

    ("table: net_control_signups",
     """CREATE TABLE IF NOT EXISTS net_control_signups (
         id SERIAL PRIMARY KEY,
         schedule_id INTEGER NOT NULL REFERENCES net_schedules(id) ON DELETE CASCADE,
         net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
         slot_date DATE NOT NULL,
         user_id INTEGER REFERENCES users(id) ON DELETE SET NULL,
         callsign VARCHAR(12) NOT NULL,
         name VARCHAR(100),
         email VARCHAR(255),
         notes TEXT,
         signed_up_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CONSTRAINT uq_signup_schedule_date UNIQUE (schedule_id, slot_date))"""),

    ("table: dmr_configs",
     """CREATE TABLE IF NOT EXISTS dmr_configs (
         id SERIAL PRIMARY KEY,
         net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
         source_type VARCHAR(20) NOT NULL DEFAULT 'wpsd',
         hotspot_url TEXT,
         talkgroup_id INTEGER,
         filter_callsign VARCHAR(12),
         direct_mode BOOLEAN NOT NULL DEFAULT FALSE,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         CONSTRAINT uq_dmr_config_net UNIQUE (net_id))"""),

    ("table: api_tokens",
     """CREATE TABLE IF NOT EXISTS api_tokens (
         id SERIAL PRIMARY KEY,
         user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
         name VARCHAR(100) NOT NULL,
         token_hash VARCHAR(64) NOT NULL UNIQUE,
         created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
         last_used_at TIMESTAMPTZ)"""),

    ("table: callsign_cache",
     """CREATE TABLE IF NOT EXISTS callsign_cache (
         callsign VARCHAR(12) PRIMARY KEY,
         status VARCHAR(10) NOT NULL,
         name VARCHAR(200),
         license_class VARCHAR(10),
         state VARCHAR(10),
         grid VARCHAR(10),
         expires VARCHAR(20),
         source VARCHAR(50),
         cached_at TIMESTAMPTZ NOT NULL DEFAULT NOW())"""),
]

# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------

def run():
    print(f"Connecting to database…")
    try:
        conn = psycopg2.connect(DATABASE_URL)
    except Exception as e:
        sys.exit(f"Could not connect: {e}")

    conn.autocommit = False
    cur = conn.cursor()

    ok = 0
    errors = 0
    for description, sql in MIGRATIONS:
        try:
            cur.execute(sql)
            conn.commit()
            print(f"  ✓  {description}")
            ok += 1
        except Exception as e:
            conn.rollback()
            print(f"  ✗  {description}")
            print(f"     {e}")
            errors += 1

    cur.close()
    conn.close()

    print()
    if errors:
        print(f"Completed with {errors} error(s). Review output above.")
        sys.exit(1)
    else:
        print(f"All {ok} migration(s) applied successfully.")


if __name__ == "__main__":
    run()
