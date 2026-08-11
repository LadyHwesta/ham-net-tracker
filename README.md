# Ham Radio Net Tracker

A web-based net control logging application for amateur radio operators. Designed for Net Control Stations (NCS) to efficiently manage check-ins, track traffic, and log net sessions.

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)

## Features

- **Net & session management** — create nets, start/end sessions, log check-ins with signal reports
- **Callsign lookup** — FCC database lookup with local history suffix search
- **Traffic management** — flag stations with traffic, interactive "called" tracking, formal traffic message log
- **ARES/ACES mode** — evacuation zone tracking per station, zone roster panel
- **Expected stations** — pre-built check-in list from historical attendees with pre-flag support
- **Station remarks** — persistent per-net notes on any callsign
- **Session summary & ICS-205** — automatic summary card on session end, printable net log export
- **Session clock** — live local/UTC time and elapsed session timer
- **Net sharing** — share nets with individual operators or all registered users
- **Scheduling** — weekly recurring time slots with net control operator sign-ups
- **Session history** — attendance statistics, filtering, and CSV export
- **Public live page** — unauthenticated `/live` page showing active nets and check-in rosters in real time
- **In-app problem reporting** — users can submit bug reports and enhancement requests directly to the administrator
- **User management** — registration with admin approval, email notifications, admin panel

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy (sync), PostgreSQL
- **Frontend**: Single-file vanilla JS SPA (no framework), LCARS-inspired dark theme
- **Auth**: JWT (PyJWT), bcrypt passwords
- **Email**: SMTP (configurable — Gmail, SendGrid, local Postfix, etc.)
- **Deployment**: systemd + Apache reverse proxy + Let's Encrypt

## Requirements

- Python 3.11+
- PostgreSQL 14+
- Apache2 (or any reverse proxy)

## Installation

### 1. Clone the repository

```bash
git clone https://github.com/LadyHwesta/ham-net-tracker.git
cd ham-net-tracker
```

### 2. Install Python dependencies

```bash
pip install fastapi uvicorn sqlalchemy psycopg2-binary pyjwt passlib[bcrypt] python-dotenv httpx pydantic[email]
```

Or with a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
pip install fastapi uvicorn sqlalchemy psycopg2-binary pyjwt passlib[bcrypt] python-dotenv httpx pydantic[email]
```

### 3. Create the database

```bash
sudo -u postgres psql
```

```sql
CREATE USER netcontrol WITH PASSWORD 'yourpassword';
CREATE DATABASE netcontrol OWNER netcontrol;
\q
```

### 4. Configure environment

```bash
cp .env.example .env
nano .env
```

Fill in your `DATABASE_URL`, `SECRET_KEY`, and SMTP settings. See `.env.example` for all options.

### 5. Initialize the database schema

```bash
python3 -c "from database import init_db; init_db()"
```

### 6. Run the application

**Development:**
```bash
uvicorn main:app --host 127.0.0.1 --port 8000 --reload
```

**Production (systemd):** See [Deployment](#deployment) below.

## Database Migrations

If upgrading from an earlier version, run any applicable migrations:

```sql
ALTER TABLE nets ADD COLUMN IF NOT EXISTS is_ares BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS has_traffic BOOLEAN NOT NULL DEFAULT FALSE;
ALTER TABLE checkins ADD COLUMN IF NOT EXISTS evac_zone VARCHAR(100);
ALTER TABLE users ADD COLUMN IF NOT EXISTS notify_new_registrations BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS evac_zones (
    id SERIAL PRIMARY KEY,
    net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
    callsign VARCHAR(12) NOT NULL,
    zone VARCHAR(100) NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_evac_zone_net_callsign UNIQUE (net_id, callsign));

CREATE TABLE IF NOT EXISTS traffic_messages (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES net_sessions(id) ON DELETE CASCADE,
    msg_number VARCHAR(50), origin_callsign VARCHAR(12) NOT NULL, dest_info VARCHAR(200),
    msg_type VARCHAR(20) NOT NULL DEFAULT 'formal',
    status VARCHAR(20) NOT NULL DEFAULT 'received',
    notes TEXT, created_at TIMESTAMPTZ NOT NULL DEFAULT NOW());

CREATE TABLE IF NOT EXISTS station_remarks (
    id SERIAL PRIMARY KEY,
    callsign VARCHAR(12) NOT NULL,
    net_id INTEGER REFERENCES nets(id) ON DELETE CASCADE,
    remark TEXT NOT NULL,
    updated_by INTEGER REFERENCES users(id) ON DELETE SET NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_station_remark_callsign_net UNIQUE (callsign, net_id));

CREATE TABLE IF NOT EXISTS net_shares (
    id SERIAL PRIMARY KEY,
    net_id INTEGER NOT NULL REFERENCES nets(id) ON DELETE CASCADE,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_net_share_net_user UNIQUE (net_id, user_id));
```

## Deployment

### systemd service

Create `/etc/systemd/system/nettracker.service`:

```ini
[Unit]
Description=Ham Radio Net Tracker
After=network.target postgresql.service

[Service]
Type=simple
User=netcontrol
WorkingDirectory=/opt/netcontrol
ExecStart=/usr/bin/uvicorn main:app --host 127.0.0.1 --port 8000
Restart=on-failure
RestartSec=5
EnvironmentFile=/opt/netcontrol/.env
StandardOutput=journal
StandardError=journal
SyslogIdentifier=nettracker

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable nettracker
sudo systemctl start nettracker
```

### Apache reverse proxy

See the `apache/` directory for a ready-to-use virtual host configuration with Let's Encrypt SSL.

```bash
sudo cp apache/netcontrol.example.conf /etc/apache2/sites-available/mysite.conf
# Edit ServerName and paths, then:
sudo a2ensite mysite
sudo systemctl reload apache2
sudo certbot --apache -d yourdomain.example.com
```

## First Run

The first user to register is automatically granted admin privileges. Subsequent registrations require admin approval before login is permitted.

## Public Live Page

A public, unauthenticated page showing all currently active nets is available at `/live`. Share this URL with club members or post it on your club website — it auto-refreshes every 30 seconds and shows the real-time check-in roster for each active net.

## Contributing

Pull requests are welcome. For major changes please open an issue first to discuss what you'd like to change.

1. Fork the repository
2. Create a feature branch (`git checkout -b feature/my-feature`)
3. Commit your changes (`git commit -am 'Add my feature'`)
4. Push to the branch (`git push origin feature/my-feature`)
5. Open a pull request

## License

This program is free software: you can redistribute it and/or modify it under the terms of the GNU General Public License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.

See the [LICENSE](LICENSE) file for the full license text.

## 73

Built for the ham radio community. If you deploy this for your club, we'd love to hear about it — open an issue and tell us where it's running!
