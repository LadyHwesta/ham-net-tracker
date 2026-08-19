# Technical Debt

Known issues, shortcuts, and areas for future improvement. Not bugs — the app works — but things worth addressing before this scales or gets wider use.

---

## High Priority

*All high-priority items resolved — see Resolved section below.*

---

## Medium Priority

~~DMR push cache is in-memory only~~ — resolved; see Resolved section.

~~No test suite~~ — resolved; see Resolved section.

~~Migration SQL must be kept in sync manually~~ — resolved; see Resolved section.
~~Single-file frontend (`index.html`)~~ — resolved; see Resolved section.

~~`httpx` imported twice under different names~~ — resolved; see Resolved section.

---

## Low Priority

~~No email verification on registration~~ — resolved; see Resolved section.

~~FCC callsign lookup depends on an external service~~ — resolved; see Resolved section.

### SQLAlchemy is used synchronously
All database calls use synchronous SQLAlchemy with a thread-per-request model. This is fine for a small club deployment but won't scale well under concurrent load. Migrating to `asyncpg` + async SQLAlchemy would be the path forward if this ever sees heavier use.

~~Relay script normalizes WPSD data differently from the backend~~ — resolved; see Resolved section.

---

## Resolved

- ~~`passlib` dependency on an unmaintained library~~ — replaced with direct `bcrypt` calls (2026-08-13)
- ~~`_net_to_out` manually constructs the response object~~ — refactored to `NetOut.model_validate(net)` with sharing metadata patched in (2026-08-13)
- ~~No rate limiting on authentication endpoints~~ — `slowapi` added; `POST /auth/login` limited to 10/minute, `POST /auth/register` to 5/minute (2026-08-13)
- ~~DMR relay script uses expiring JWT tokens~~ — `api_tokens` table + `GET/POST/DELETE /auth/tokens` endpoints added; `get_current_user` accepts `nt_` prefixed tokens; relay script updated to use API token (2026-08-13)
- ~~No fail2ban-compatible auth failure log~~ — `AUTH_LOG_FILE` env var; failed logins write `AUTH_FAIL ip=… reason=…` to a `WatchedFileHandler` log (2026-08-13)
- ~~Migration SQL must be kept in sync manually~~ — `migrate.py` is now the single source of truth; `index.html` and `README.md` updated to point to it (2026-08-14)
- ~~Single-file frontend (`index.html`)~~ — CSS extracted to `static/app.css`; JS split into 15 feature modules under `static/js/`; Admin/Tokens/Help/Report split into standalone HTML pages; `index.html` reduced from 3800 to 624 lines (SPA core only); FastAPI serves `/static` via `StaticFiles` (2026-08-14)
- ~~No test suite~~ — 59-test pytest suite covering auth, nets, sessions, and check-ins; runs against SQLite in-memory via `python -m pytest tests/`; `requirements-dev.txt` has the test deps (2026-08-16)
- ~~DMR push cache is in-memory only~~ — `_dmr_cache_write` now persists each push to `SystemSetting` as JSON; `_dmr_cache_read` falls back to `SystemSetting` on an in-memory miss (e.g., after restart); no new table or migration needed (2026-08-17)
- ~~`httpx` imported twice under different names~~ — duplicate `import httpx as _httpx` removed; all DMR proxy calls use the top-level `httpx` import (2026-08-16)
- ~~FCC callsign lookup depends on an external service~~ — `CallsignCache` table added; results cached for 30 days (found) or 7 days (not_found); `_callsign_cache_read/write` helpers wrap all four return paths in `lookup_callsign`; 4 cache-hit tests added (2026-08-17)
- ~~Relay script normalizes WPSD data differently from the backend~~ — new `POST /nets/{id}/dmr/push/raw` endpoint accepts raw hotspot JSON + `source` tag and normalizes server-side using existing `_dmr_normalize_wpsd/brandmeister()` functions; `dmr_relay.py` added to repo as a thin fetch-and-forward proxy; old `/push` endpoint kept for backward compat; 5 tests added (2026-08-17)
- ~~No email verification on registration~~ — `users.email_verified`/`verification_token`/`verification_sent_at` added; registration sends a verify-your-email link (skipped for the bootstrap first/admin user, and silently skipped like all other email if SMTP isn't configured); `GET /auth/verify-email` consumes the token; login now rejects unverified accounts before checking approval status; pending-approval list in Admin shows a Verified/Unverified badge so an admin can see at a glance. 16 tests added (2026-08-19)
- ~~`create_support_ticket` duplicated `send_email`'s entire SMTP-sending logic~~ (found while working the item above) — just to set a `Reply-To` header `send_email()` didn't support; added a `reply_to` param to `send_email()` instead and removed the ~30-line duplicate implementation (2026-08-19)
- ~~Admin had no way to unblock a user stuck on email verification~~ (found via self-review of the item above) — `PATCH /admin/users/{id}/approve` now also sets `email_verified=True`; an admin's approval is a stronger trust signal than the automated link-click, and it's the only lever available when `APP_BASE_URL` isn't configured (so the verify email has no working link) or the email never arrives. `verification_token` is now stored as a sha256 hash rather than raw (matches the existing `api_tokens.token_hash` pattern) and expires after 7 days. 3 more tests added (2026-08-19)
- ~~`GET /users` was defined twice~~ (found via self-review, pre-existing since the initial commit — unrelated to any recent work) — a second `list_users`/`UserSummary` pair later in `main.py` was fully unreachable (FastAPI matches routes in registration order); deleted the dead route and model (2026-08-19)
- ~~4 unused CSS custom properties~~ (`--accent`, `--danger`, `--warn`, `--lc-teal` — pre-existing, zero references anywhere in the app) — removed from `:root` and all 3 theme blocks added for the theme engine, where they'd been faithfully but pointlessly propagated (2026-08-19)
- ~~No app-wide logging configuration~~ (found while live-verifying the Net Repository push against production — a successful push's `INFO` log line was invisible, with no clue why) — nothing called `logging.basicConfig()`, so only `_auth_log` (which sets up its own optional `AUTH_LOG_FILE` handler) had a real handler; everything else silently relied on Python's WARNING-level "handler of last resort," so `INFO` messages (sent email, pushed net) never appeared anywhere, even in the systemd journal, while the equivalent `WARNING`-level failures already did. Added `logging.basicConfig()` at startup, level configurable via `LOG_LEVEL` (default `INFO`) (2026-08-19)
