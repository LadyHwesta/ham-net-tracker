# Ham Radio Net Tracker

A web-based net control logging application for amateur radio operators. Designed for Net Control Stations (NCS) to efficiently manage check-ins, track traffic, and log net sessions.

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)

## Live Demo

A public demo is available at **[nettrackerdemo.meskis.net](https://nettrackerdemo.meskis.net)**. Feel free to explore all features — the database resets automatically every 4 hours.

| Field | Value |
|-------|-------|
| URL | https://nettrackerdemo.meskis.net |
| Callsign | `D3MO` |
| Password | `Abcd1234` |

> The demo account has full admin access. Any nets, sessions, or check-ins you create will be wiped on the next reset.

## Features

- **Net & session management** — create nets, start/end sessions, log check-ins with signal reports
- **Focused live session view** — sidebar auto-collapses and session navigation hides while a session is live to cut clutter, restoring automatically once it ends
- **Callsign lookup** — FCC database lookup with local history suffix search
- **Traffic management** — flag stations with traffic, interactive "called" tracking, formal traffic message log
- **ARES/ACES mode** — evacuation zone tracking per station, zone roster panel
- **Expected stations** — pre-built check-in list from historical attendees with pre-flag support
- **Station remarks** — persistent per-net notes on any callsign
- **Session summary & ICS-205** — automatic summary card on session end, printable net log export
- **Net control script** — attach a script to a net with basic formatting and live `{{variable}}` substitution (Net Control / Broadcaster callsign and name), pinned to the top of the live check-in screen so you don't need a second window open
- **Session clock** — live local/UTC time and elapsed session timer
- **Net sharing** — share nets with individual operators or all registered users
- **Scheduling** — weekly recurring time slots with sign-ups for Net Control and, on nets with an additional broadcast (e.g. Amateur Radio Newsline), a separate Broadcaster role; confirmation emails include a `.ics` calendar attachment
- **Session history** — attendance statistics, filtering, and CSV export
- **Public live page** — unauthenticated `/live` page showing active nets and check-in rosters in real time
- **In-app problem reporting** — users can submit bug reports and enhancement requests directly to the administrator
- **User management** — registration with admin approval, email notifications, admin panel
- **Configurable branding** — set organization name, tagline, website URL, and logo from the Admin panel
- **DMR hotspot integration** — connect a net to a WPSD, Pi-Star, or BrandMeister talk group; see a live "last heard" panel during the session, quick-check-in heard stations, and log Talk Group + Region per check-in

## Tech Stack

- **Backend**: Python 3.11+, FastAPI, SQLAlchemy (sync), PostgreSQL
- **Frontend**: Vanilla JS SPA (no framework), LCARS-inspired dark theme — CSS in `static/app.css`, JS split into feature modules under `static/js/`
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

> **Note:** The initial `CREATE USER` and `CREATE DATABASE` must be run as the `postgres` superuser. All subsequent commands — including migrations — should be run as the `netcontrol` user (`sudo -u netcontrol psql netcontrol`) so that created tables are automatically owned by the app user and no GRANTs are needed.

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

> **Fresh installs:** the app creates the full current schema automatically on first startup. No migration step needed.

**Upgrading an existing install** — `./deploy.sh` runs `migrate.py` automatically as part of every deploy, so no manual step is needed if you use it. Running it by hand:

```bash
sudo -u netcontrol python3 /opt/netcontrol/migrate.py
```

`migrate.py` is the single source of truth for all schema changes. It is safe to re-run at any time — every step uses `IF NOT EXISTS` or is otherwise idempotent. When adding a new column or table to `models.py`, add the corresponding statement to `MIGRATIONS` in `migrate.py` — that's the only file that needs updating.

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
EnvironmentFile=/opt/netcontrol/.env
ExecStart=/usr/bin/uvicorn main:app --host 127.0.0.1 --port ${PORT}
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal
SyslogIdentifier=nettracker

[Install]
WantedBy=multi-user.target
```

`${PORT}` is substituted by systemd from `PORT` in `.env` (see `.env.example`) — the unit file itself doesn't need to change per instance.

```bash
sudo systemctl daemon-reload
sudo systemctl enable nettracker
sudo systemctl start nettracker
```

#### Running more than one instance on the same server

The template above, plus `PORT` and `SYSTEMD_SERVICE` in `.env`, is all `deploy.sh` needs to work unmodified for multiple instances (e.g. a production site and a demo site) checked out separately on one server — no per-instance edits to `deploy.sh` or the unit file's `ExecStart` line. For each instance:

1. Check out the repo to its own directory (e.g. `/opt/netcontrol` and `/opt/netcontrol-demo`).
2. Give each its own `.env` with a distinct `DATABASE_URL`, `PORT`, and `SYSTEMD_SERVICE` (e.g. `nettracker` and `nettrackerdemo`).
3. Copy the unit file template above to `/etc/systemd/system/<SYSTEMD_SERVICE>.service` for each instance — the file contents are identical except the description/working directory; only `.env` needs to differ.
4. Add a matching sudoers `NOPASSWD` line for each `SYSTEMD_SERVICE` value (see the prerequisite comment at the top of `deploy.sh`) — `deploy.sh` reads `SYSTEMD_SERVICE` from the `.env` in its own checkout and restarts exactly that unit.
5. Point each Apache vhost's reverse proxy at the matching `PORT`.

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

## Net Control Script

Net owners can attach a script to a net from its **Edit** form — a **Net Script** field below Description. It's shown in a collapsible **📜 NET SCRIPT** panel pinned to the top of the live check-in screen (open by default whenever a script is set), so you don't need a second window or a printed sheet next to the keyboard.

### Markup

A small, deliberately limited set of formatting is supported — write it in any external editor and paste it in:

| Syntax | Result |
|--------|--------|
| `**bold**` | **bold** |
| `*italic*` | *italic* |
| `# Heading`, `## Heading`, `### Heading` | three heading sizes |
| `- item` or `* item` | bullet list |
| `---` or `===` (alone on a line) | horizontal rule |

Anything else — blank lines, indentation, plain text — renders exactly as typed. This is not full Markdown or HTML; unrecognized syntax (including literal `<` / `>`) is shown as plain text rather than interpreted, so a script can never inject markup or scripts of its own.

### Variables

`{{variable}}` placeholders are substituted with live session info when the script is displayed:

| Variable | Value |
|----------|-------|
| `{{net_name}}` | The net's name |
| `{{net_control}}` | Net Control name — callsign |
| `{{net_control_callsign}}` / `{{net_control_name}}` | Just the callsign / just the name |
| `{{broadcaster}}` | Broadcaster name — callsign (only meaningful on nets with [Additional Broadcast](#broadcaster-role-additional-broadcast) enabled) |
| `{{broadcaster_callsign}}` / `{{broadcaster_name}}` | Just the callsign / just the name |
| `{{broadcast_label}}` | The net's custom broadcast name (e.g. "Amateur Radio Newsline") |
| `{{net_control_next}}` | **Next week's** Net Control name — callsign, from the Schedule tab |
| `{{net_control_next_callsign}}` / `{{net_control_next_name}}` | Just the callsign / just the name |
| `{{broadcaster_next}}` | **Next week's** Broadcaster name — callsign |
| `{{broadcaster_next_callsign}}` / `{{broadcaster_next_name}}` | Just the callsign / just the name |

Net Control falls back to whoever actually started the session if no one signed up on the Schedule tab for that date; Broadcaster is only filled from a Schedule sign-up (there's no session-operator fallback, since a session has one operator but two possible roles). The `_next` variables look at the date exactly one week after this session and are **never** filled by fallback — next week hasn't happened yet, so there's no operator to fall back to — they stay blank until someone actually signs up on the Schedule tab. An unrecognized `{{...}}` is left as-is rather than silently dropped, so a typo is easy to spot. For example:

```
# Monday Night Net Script

Good evening, this is **{{net_control}}**, your net control operator
for the {{net_name}}.

Coming up: tonight's {{broadcast_label}} segment, read by {{broadcaster}}.

- If you would like to check in, please call now with your callsign.
- Traffic? Let us know when you check in.

---

Next week's net control will be {{net_control_next}}.

Thank you all for checking in. This net is now closed.
```

Leave the field blank to hide the panel entirely.

## Broadcaster Role (Additional Broadcast)

Some nets carry a second segment alongside net control — for example, a member reading the latest **Amateur Radio Newsline** bulletin. Enable **Additional Broadcast** in the net's Edit form and give it a name (e.g. "Amateur Radio Newsline"); this adds a **Broadcaster** role to that net's Schedule sign-ups, separate from Net Control.

On the Schedule tab, each upcoming date shows Net Control and Broadcaster as independent sign-up slots — different operators can claim each one, or a single operator can claim **Cover Both Roles**. The net owner can assign either role to a registered operator the same way they assign Net Control today.

Whoever is signed up for a date appears — callsign and name — in the duty bar on the live check-in screen and on the public `/live` page, so anyone checking in (or watching the public page) can see who's running the net and who's carrying the broadcast that day. If no one has signed up for a date, Net Control on the public page falls back to whoever actually started the session.

## DMR Hotspot Integration

Net owners can configure DMR last-heard data in the net's Edit form. Three source types are supported: **WPSD**, **Pi-Star**, and **BrandMeister** (by talk group).

### Fetch modes

| Mode | How it works | Use when |
|------|-------------|----------|
| **Proxy** (default) | Server fetches the hotspot URL | Hotspot is internet-accessible |
| **Direct** | Browser fetches the hotspot directly | Hotspot is on local LAN; browser has CORS/insecure-content permissions set |
| **Relay script** | Small Python script on the LAN pushes data to the server | Hotspot is local-only and CORS is blocked (most common home setup) |

### DMR relay script

If your hotspot is on a local network and browser CORS restrictions prevent direct fetching, download `dmr_relay.py` from the net's DMR config section in the app. It runs on any machine that can reach the hotspot (the Pi itself works well) and pushes last-heard data to the server every 30 seconds.

**Setup:**

1. Go to **🪙 API Tokens** in the sidebar and create a token (e.g. "DMR Relay - shack Pi"). Copy the token — it is shown only once.
2. Download `dmr_relay.py` from the net's DMR config section.
3. Paste the token into the `API_TOKEN` line in the script.
4. Run it on any machine that can reach the hotspot:

```bash
sudo apt install python3-requests   # on Raspberry Pi / WPSD
python3 dmr_relay.py
```

The script uses a long-lived API token (no password stored, no re-authentication needed). Leave it running for the duration of the net. The app shows "Via relay script (Xs ago)" in the DMR panel when using cached relay data. Revoke the token any time from the API Tokens page.

### API Tokens

Long-lived API tokens are available for service accounts and scripts. They are prefixed with `nt_` and work anywhere a Bearer token is accepted. Tokens are stored as SHA-256 hashes — the raw value is shown only at creation.

- **Create:** `POST /auth/tokens` `{"name": "label"}`
- **List:** `GET /auth/tokens`
- **Revoke:** `DELETE /auth/tokens/{id}`

### fail2ban integration

Set `AUTH_LOG_FILE=/var/log/nettracker/auth.log` in `.env` to write structured auth failure lines:

```
2026-08-13T19:42:01 AUTH_FAIL ip=1.2.3.4 reason=bad_credentials username='W1AW'
```

Example fail2ban filter (`/etc/fail2ban/filter.d/nettracker.conf`):

```ini
[Definition]
failregex = AUTH_FAIL ip=<HOST>
ignoreregex =
```

And jail entry:

```ini
[nettracker]
enabled  = true
port     = http,https
filter   = nettracker
logpath  = /var/log/nettracker/auth.log
maxretry = 5
bantime  = 600
```

Create the log directory first: `sudo mkdir -p /var/log/nettracker && sudo chown netcontrol: /var/log/nettracker`

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
