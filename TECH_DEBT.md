# Technical Debt

Known issues, shortcuts, and areas for future improvement. Not bugs — the app works — but things worth addressing before this scales or gets wider use.

---

## High Priority

*All high-priority items resolved — see Resolved section below.*

---

## Medium Priority

### DMR push cache is in-memory only
`_dmr_push_cache` in `main.py` is a plain Python dict. It is lost on every server restart. For a net that runs while the server is restarted (e.g., during an update), the relay data disappears and the heard panel goes dark. Options: persist to a `dmr_push_cache` DB table with a TTL, or use Redis if the deployment ever warrants it.

### No test suite
There are no automated tests. Changes are validated manually by deploying and clicking around. At minimum, unit tests for the auth flow, checkin logic, and DMR normalization would catch regressions. FastAPI's `TestClient` makes this straightforward.

~~Migration SQL must be kept in sync manually~~ — resolved; see Resolved section.
~~Single-file frontend (`index.html`)~~ — resolved; see Resolved section.

### Single-file frontend (`index.html`)
`index.html` is a 3000+ line file containing HTML, CSS, and JavaScript all in one. This made bootstrapping fast but is increasingly hard to navigate and will make onboarding contributors difficult. A build step (Vite or even just vanilla ES modules) would let the JS be split into logical modules without changing the single-file deployment story.

### `httpx` imported twice under different names
`httpx` is imported at the top of `main.py` as `httpx` (used in one async context) and again later as `import httpx as _httpx` (used in the DMR proxy functions). Should be consolidated to a single import.

---

## Low Priority

### No email verification on registration
New accounts require admin approval but no email verification step. An admin could approve a typo'd email address, and the user would never receive notifications. Adding a verification token sent on registration (before the approval step) would close this gap.

### FCC callsign lookup depends on an external service
Callsign lookup hits an external API. If that service is down or rate-limits the instance, lookups silently fail. There's no fallback, no caching of previously resolved callsigns, and no clear error shown to the user. A local cache of previously seen callsigns (already stored in checkin history) would reduce dependency on the external service for known callsigns.

### SQLAlchemy is used synchronously
All database calls use synchronous SQLAlchemy with a thread-per-request model. This is fine for a small club deployment but won't scale well under concurrent load. Migrating to `asyncpg` + async SQLAlchemy would be the path forward if this ever sees heavier use.

### Relay script normalizes WPSD data differently from the backend
The Python relay script (`dmr_relay.py`) and the backend's `_dmr_normalize_wpsd()` function both normalize WPSD API responses but are separate implementations that could drift. The relay script should ideally call the same normalization logic, or the backend should accept raw WPSD JSON and normalize it server-side.

---

## Resolved

- ~~`passlib` dependency on an unmaintained library~~ — replaced with direct `bcrypt` calls (2026-08-13)
- ~~`_net_to_out` manually constructs the response object~~ — refactored to `NetOut.model_validate(net)` with sharing metadata patched in (2026-08-13)
- ~~No rate limiting on authentication endpoints~~ — `slowapi` added; `POST /auth/login` limited to 10/minute, `POST /auth/register` to 5/minute (2026-08-13)
- ~~DMR relay script uses expiring JWT tokens~~ — `api_tokens` table + `GET/POST/DELETE /auth/tokens` endpoints added; `get_current_user` accepts `nt_` prefixed tokens; relay script updated to use API token (2026-08-13)
- ~~No fail2ban-compatible auth failure log~~ — `AUTH_LOG_FILE` env var; failed logins write `AUTH_FAIL ip=… reason=…` to a `WatchedFileHandler` log (2026-08-13)
- ~~Migration SQL must be kept in sync manually~~ — `migrate.py` is now the single source of truth; `index.html` and `README.md` updated to point to it (2026-08-14)
- ~~Single-file frontend (`index.html`)~~ — CSS extracted to `static/app.css`; JS split into 15 feature modules under `static/js/`; `index.html` reduced from 3800 to 945 lines; FastAPI serves `/static` via `StaticFiles` (2026-08-14)
