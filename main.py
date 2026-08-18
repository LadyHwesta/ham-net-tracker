"""
Ham Radio Net Tracker — FastAPI backend

Endpoints
---------
Auth
  POST /auth/register   — create a net-control operator account
  POST /auth/login      — returns JWT access token

Nets
  GET    /nets          — list nets owned by current user
  POST   /nets          — create a net
  GET    /nets/{id}     — get net details
  PUT    /nets/{id}     — update net
  DELETE /nets/{id}     — delete net

Net Sessions
  GET    /nets/{id}/sessions          — list sessions for a net
  POST   /nets/{id}/sessions          — start a new session
  GET    /sessions/{id}               — get session details
  PATCH  /sessions/{id}/end           — end (close) a session
  DELETE /sessions/{id}               — delete session

Checkins
  GET    /sessions/{id}/checkins      — list checkins in a session
  POST   /sessions/{id}/checkins      — add a checkin
  DELETE /checkins/{id}               — remove a checkin

History / Stats
  GET    /nets/{id}/history           — checkin counts per callsign across all sessions
  GET    /sessions/{id}/export        — CSV export of all checkins in a session
  GET    /nets/{id}/export            — CSV export of all checkins across all sessions

Callsign Lookup
  GET    /callsign/{callsign}/lookup  — look up FCC license data (name, class, state, grid)
"""

import csv
import hashlib
import io
import logging
import json
import logging.handlers
import os
import pathlib
import secrets
import smtplib
import time as _time
from contextlib import asynccontextmanager
from datetime import date, datetime, timedelta, timezone
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from typing import Optional

import httpx

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, File, HTTPException, Query, Request, Response, UploadFile, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
import jwt
import bcrypt as _bcrypt
from pydantic import BaseModel, EmailStr, field_validator
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from database import get_db, init_db
from models import ApiToken, CallsignCache, Checkin, DmrConfig, EvacZone, GmrsLicense, Net, NetControlSignup, NetSchedule, NetSession, NetShare, StationRemark, SystemSetting, TrafficMessage, User, utcnow

load_dotenv()

# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------
SECRET_KEY = os.getenv("SECRET_KEY", "change-me-in-production-use-a-long-random-string")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "480"))  # 8 hours

# ---------------------------------------------------------------------------
# SMTP / Email config
# ---------------------------------------------------------------------------
SMTP_HOST     = os.getenv("SMTP_HOST", "")
SMTP_PORT     = int(os.getenv("SMTP_PORT", "587"))
SMTP_USER     = os.getenv("SMTP_USER", "")
SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", "")
SMTP_FROM     = os.getenv("SMTP_FROM", "")        # e.g. "Ham Net Tracker <noreply@example.com>"
SMTP_USE_TLS  = os.getenv("SMTP_USE_TLS", "true").lower() == "true"   # STARTTLS (port 587)
SMTP_USE_SSL  = os.getenv("SMTP_USE_SSL", "false").lower() == "true"  # SSL/TLS (port 465)
ADMIN_CONTACT_EMAIL = os.getenv("ADMIN_CONTACT_EMAIL", "")  # shown in approval emails as human contact

_email_log = logging.getLogger("ham_net_tracker.email")


def _smtp_configured() -> bool:
    return bool(SMTP_HOST and SMTP_USER and SMTP_PASSWORD)


def send_email(
    to: list[str],
    subject: str,
    body_html: str,
    body_text: str = "",
    ics_content: str | None = None,
    ics_filename: str = "netcontrol.ics",
) -> None:
    """Send an HTML email, optionally with an ICS calendar attachment.
    Silently skips (logs warning) if SMTP is not configured."""
    if not _smtp_configured():
        _email_log.debug("SMTP not configured — skipping email: %s", subject)
        return
    if not to:
        return

    from_addr = SMTP_FROM or SMTP_USER

    if ics_content:
        # multipart/mixed wraps alternative body + ics attachment
        outer = MIMEMultipart("mixed")
        outer["Subject"] = subject
        outer["From"]    = from_addr
        outer["To"]      = ", ".join(to)

        alt = MIMEMultipart("alternative")
        if body_text:
            alt.attach(MIMEText(body_text, "plain"))
        alt.attach(MIMEText(body_html, "html"))
        outer.attach(alt)

        ics_part = MIMEBase("text", "calendar", method="REQUEST", charset="UTF-8")
        ics_part.set_payload(ics_content.encode("utf-8"))
        ics_part["Content-Disposition"] = f'attachment; filename="{ics_filename}"'
        outer.attach(ics_part)
        msg = outer
    else:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = subject
        msg["From"]    = from_addr
        msg["To"]      = ", ".join(to)
        if body_text:
            msg.attach(MIMEText(body_text, "plain"))
        msg.attach(MIMEText(body_html, "html"))

    try:
        if SMTP_USE_SSL:
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as srv:
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.sendmail(from_addr, to, msg.as_string())
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as srv:
                if SMTP_USE_TLS:
                    srv.starttls()
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.sendmail(from_addr, to, msg.as_string())
        _email_log.info("Email sent to %s — %s", to, subject)
    except Exception as exc:
        _email_log.warning("Failed to send email to %s: %s", to, exc)


def _build_ics(net: "Net", schedule: "NetSchedule", signup: "NetControlSignup", role_label: str = "Net Control") -> str:
    """Build an iCalendar (ICS) event string for a net control / broadcaster signup."""
    import re
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

    tz_str = schedule.timezone or "UTC"
    try:
        tz = ZoneInfo(tz_str)
    except ZoneInfoNotFoundError:
        tz = ZoneInfo("UTC")
        tz_str = "UTC"

    h, m = map(int, schedule.start_time.split(":"))
    naive_start = datetime(
        signup.slot_date.year, signup.slot_date.month, signup.slot_date.day, h, m
    )
    local_start = naive_start.replace(tzinfo=tz)
    utc_start   = local_start.astimezone(ZoneInfo("UTC"))
    utc_end     = utc_start + timedelta(hours=1)   # default 1-hour block

    dtstamp = datetime.now(ZoneInfo("UTC")).strftime("%Y%m%dT%H%M%SZ")
    dtstart = utc_start.strftime("%Y%m%dT%H%M%SZ")
    dtend   = utc_end.strftime("%Y%m%dT%H%M%SZ")

    uid = f"netcontrol-{signup.id}-{signup.slot_date}@hamnettracker"

    # Build description (escape commas and newlines per RFC 5545)
    desc_parts = [f"You are scheduled as {role_label} for {net.name}."]
    if net.frequency:
        desc_parts.append(f"Frequency: {net.frequency}")
    desc_parts.append(f"Date: {signup.slot_date}")
    desc_parts.append(f"Time: {schedule.start_time} {tz_str}")
    if schedule.notes:
        desc_parts.append(f"Net notes: {schedule.notes}")
    if signup.notes:
        desc_parts.append(f"Your notes: {signup.notes}")
    description = "\\n".join(desc_parts)

    # Organizer — strip display name if present
    organizer_raw = SMTP_FROM or SMTP_USER or ""
    m2 = re.search(r"<(.+?)>", organizer_raw)
    organizer_email = m2.group(1) if m2 else organizer_raw

    attendee_name  = signup.name or signup.callsign
    attendee_email = signup.email or ""

    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Ham Net Tracker//Ham Radio//EN",
        "METHOD:REQUEST",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        f"DTSTAMP:{dtstamp}",
        f"DTSTART:{dtstart}",
        f"DTEND:{dtend}",
        f"SUMMARY:{net.name} – {role_label}",
        f"DESCRIPTION:{description}",
    ]
    if organizer_email:
        lines.append(f"ORGANIZER:mailto:{organizer_email}")
    if attendee_email:
        lines.append(f"ATTENDEE;CN={attendee_name};RSVP=FALSE:mailto:{attendee_email}")
    lines += ["END:VEVENT", "END:VCALENDAR"]

    return "\r\n".join(lines) + "\r\n"

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# ---------------------------------------------------------------------------
# Auth failure logger (for fail2ban)
# ---------------------------------------------------------------------------
AUTH_LOG_FILE = os.getenv("AUTH_LOG_FILE", "")   # e.g. /var/log/nettracker/auth.log

_auth_log = logging.getLogger("ham_net_tracker.auth")
if AUTH_LOG_FILE:
    _auth_handler = logging.handlers.WatchedFileHandler(AUTH_LOG_FILE)
    _auth_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%Y-%m-%dT%H:%M:%S"))
    _auth_log.addHandler(_auth_handler)
_auth_log.setLevel(logging.WARNING)


def _log_auth_fail(request: Request, reason: str) -> None:
    ip = request.headers.get("X-Forwarded-For", request.client.host if request.client else "unknown").split(",")[0].strip()
    _auth_log.warning("AUTH_FAIL ip=%s reason=%s", ip, reason)


# ---------------------------------------------------------------------------
# Rate limiter
# ---------------------------------------------------------------------------
limiter = Limiter(key_func=get_remote_address)

# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
UPLOADS_DIR = pathlib.Path(__file__).parent / "uploads"
LOGO_PATH   = UPLOADS_DIR / "logo"
STATIC_DIR  = pathlib.Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app):
    init_db()
    UPLOADS_DIR.mkdir(exist_ok=True)
    yield


app = FastAPI(title="Ham Radio Net Tracker", version="1.9.0", lifespan=lifespan)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


# ---------------------------------------------------------------------------
# Frontend
# ---------------------------------------------------------------------------

_HTML_FILE = Path(__file__).parent / "index.html"
_STATIC_DIR = Path(__file__).parent


def _serve_html(name: str) -> HTMLResponse:
    """Read and serve a standalone HTML page from the app directory."""
    return HTMLResponse(content=(_STATIC_DIR / name).read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
def serve_frontend():
    """Serve the SPA (My Nets + Session views)."""
    return HTMLResponse(content=_HTML_FILE.read_text(encoding="utf-8"))


@app.get("/admin", response_class=HTMLResponse, include_in_schema=False)
def serve_admin():
    return _serve_html("admin.html")


@app.get("/tokens", response_class=HTMLResponse, include_in_schema=False)
def serve_tokens():
    return _serve_html("tokens.html")


@app.get("/help", response_class=HTMLResponse, include_in_schema=False)
def serve_help():
    return _serve_html("help.html")


@app.get("/report", response_class=HTMLResponse, include_in_schema=False)
def serve_report():
    return _serve_html("report.html")


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------

class UserCreate(BaseModel):
    callsign: str
    name: str
    email: EmailStr
    password: str

    @field_validator("callsign")
    @classmethod
    def callsign_upper(cls, v):
        return v.upper().strip()


class UserOut(BaseModel):
    id: int
    callsign: str
    name: str
    email: str
    is_active: bool
    is_admin: bool
    notify_new_registrations: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class Token(BaseModel):
    access_token: str
    token_type: str
    user: UserOut


class NetCreate(BaseModel):
    name: str
    frequency: Optional[str] = None
    description: Optional[str] = None
    net_type: str = "ham"       # "ham" | "gmrs"
    is_ares: bool = False       # ham only; ignored (forced False) for GMRS nets
    dmr_talkgroup: Optional[str] = None   # ham only
    script: Optional[str] = None   # net control script, shown alongside the check-in screen
    has_broadcast: bool = False    # e.g. a Newsline segment carried during the net
    broadcast_label: Optional[str] = None   # e.g. "Amateur Radio Newsline"


class NetOut(BaseModel):
    id: int
    name: str
    frequency: Optional[str]
    description: Optional[str]
    net_type: str
    is_ares: bool
    dmr_talkgroup: Optional[str] = None
    script: Optional[str] = None
    has_broadcast: bool = False
    broadcast_label: Optional[str] = None
    owner_id: int
    created_at: datetime
    # Sharing fields (populated by helper, not from ORM attributes directly)
    is_owner: bool = True
    shared_with_all: bool = False
    shared_user_ids: list[int] = []
    owner_callsign: Optional[str] = None

    model_config = {"from_attributes": True}


class UserPublicOut(BaseModel):
    id: int
    callsign: str
    name: str

    model_config = {"from_attributes": True}


class NetShareUpdate(BaseModel):
    share_with_all: bool = False
    user_ids: list[int] = []   # specific user IDs to share with (ignored when share_with_all=True)


class BrandingOut(BaseModel):
    org_name: Optional[str] = None
    tagline: Optional[str] = None
    website_url: Optional[str] = None
    has_logo: bool = False


class BrandingUpdate(BaseModel):
    org_name: Optional[str] = None
    tagline: Optional[str] = None
    website_url: Optional[str] = None


class SessionCreate(BaseModel):
    name: Optional[str] = None
    notes: Optional[str] = None


class SessionRename(BaseModel):
    name: Optional[str] = None


class SessionOut(BaseModel):
    id: int
    net_id: int
    operator_id: Optional[int]
    name: Optional[str]
    notes: Optional[str]
    started_at: datetime
    ended_at: Optional[datetime]
    checkin_count: int = 0
    # Scheduled duty for this session's date, from the Schedule sign-up if one exists
    # (net control falls back to whoever started the session when no sign-up matches)
    ncs_callsign: Optional[str] = None
    ncs_name: Optional[str] = None
    broadcaster_callsign: Optional[str] = None
    broadcaster_name: Optional[str] = None
    broadcast_label: Optional[str] = None

    model_config = {"from_attributes": True}


class CheckinCreate(BaseModel):
    callsign: str
    name: Optional[str] = None
    signal_report: Optional[str] = None
    comments: Optional[str] = None
    has_traffic: bool = False
    evac_zone: Optional[str] = None
    dmr_talkgroup: Optional[str] = None
    dmr_region: Optional[str] = None

    @field_validator("callsign")
    @classmethod
    def callsign_upper(cls, v):
        return v.upper().strip()


class CheckinOut(BaseModel):
    id: int
    session_id: int
    callsign: str
    name: Optional[str]
    signal_report: Optional[str]
    comments: Optional[str]
    has_traffic: bool
    evac_zone: Optional[str]
    dmr_talkgroup: Optional[str] = None
    dmr_region: Optional[str] = None
    checked_in_at: datetime

    model_config = {"from_attributes": True}


class DmrConfigCreate(BaseModel):
    source_type: str = "wpsd"           # wpsd | pistar | brandmeister
    hotspot_url: Optional[str] = None   # for wpsd/pistar
    talkgroup_id: Optional[int] = None  # for brandmeister
    filter_callsign: Optional[str] = None
    direct_mode: bool = False


class DmrConfigOut(BaseModel):
    source_type: str
    hotspot_url: Optional[str] = None
    talkgroup_id: Optional[int] = None
    filter_callsign: Optional[str] = None
    direct_mode: bool

    model_config = {"from_attributes": True}


class DmrHeardEntry(BaseModel):
    callsign: str
    dmr_id: Optional[str] = None
    name: Optional[str] = None
    talk_group: Optional[str] = None
    timeslot: Optional[str] = None
    region: Optional[str] = None
    heard_at: Optional[str] = None
    duration: Optional[str] = None


class EvacZoneOut(BaseModel):
    callsign: str
    zone: str
    updated_at: datetime

    model_config = {"from_attributes": True}


class EvacZoneUpdate(BaseModel):
    zone: str


class ExpectedStation(BaseModel):
    callsign: str
    name: Optional[str]
    checkin_count: int   # in the requested window
    last_checkin: datetime


class CallsignHistoryItem(BaseModel):
    callsign: str
    name: Optional[str]
    total_checkins: int
    recent_checkins: int           # checkins in the past 14 days
    recent_4w_checkins: int        # checkins in the past 28 days
    checked_in_last_session: bool  # present in the most recent ended session
    last_checkin: datetime


# ── Traffic messages ─────────────────────────────────────────────────────────

class TrafficMessageCreate(BaseModel):
    origin_callsign: str
    dest_info: Optional[str] = None
    msg_number: Optional[str] = None
    msg_type: str = "formal"       # formal | informal | health_welfare
    status: str = "received"       # received | relayed | delivered | undeliverable
    notes: Optional[str] = None


class TrafficMessageUpdate(BaseModel):
    dest_info: Optional[str] = None
    msg_number: Optional[str] = None
    msg_type: Optional[str] = None
    status: Optional[str] = None
    notes: Optional[str] = None


class TrafficMessageOut(BaseModel):
    id: int
    session_id: int
    msg_number: Optional[str]
    origin_callsign: str
    dest_info: Optional[str]
    msg_type: str
    status: str
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Station remarks ──────────────────────────────────────────────────────────

class StationRemarkUpsert(BaseModel):
    remark: str


class StationRemarkOut(BaseModel):
    callsign: str
    net_id: int
    remark: str
    updated_at: datetime

    model_config = {"from_attributes": True}


# ── API Tokens ───────────────────────────────────────────────────────────────

class ApiTokenCreate(BaseModel):
    name: str   # human label, e.g. "DMR Relay - shack Pi"


class ApiTokenOut(BaseModel):
    id: int
    name: str
    created_at: datetime
    last_used_at: Optional[datetime]

    model_config = {"from_attributes": True}


class ApiTokenCreated(BaseModel):
    """Returned once at creation — includes the raw token (never stored)."""
    id: int
    name: str
    token: str          # raw token — show to user once, then discard
    created_at: datetime


# ── Session summary ──────────────────────────────────────────────────────────

class SessionSummary(BaseModel):
    session_id: int
    net_name: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_minutes: Optional[int]
    total_checkins: int
    traffic_count: int
    new_stations: int      # callsigns appearing for the first time on this net
    operator_callsign: Optional[str]
    net_frequency: Optional[str]


# ---------------------------------------------------------------------------
# Auth helpers
# ---------------------------------------------------------------------------

def verify_password(plain: str, hashed: str) -> bool:
    return _bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))


def hash_password(password: str) -> str:
    return _bcrypt.hashpw(password.encode("utf-8"), _bcrypt.gensalt()).decode("utf-8")


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(minutes=15))
    to_encode["exp"] = expire
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


def get_current_user(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> User:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )

    # --- Try long-lived API token first (format: "nt_<64 hex chars>") ---
    if token.startswith("nt_"):
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        api_token = db.query(ApiToken).filter(ApiToken.token_hash == token_hash).first()
        if api_token is None:
            raise credentials_exception
        user = db.query(User).filter(User.id == api_token.user_id).first()
        if user is None or not user.is_active:
            raise credentials_exception
        # Update last_used_at (fire-and-forget; don't fail the request if this errors)
        try:
            api_token.last_used_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            db.rollback()
        return user

    # --- Fall back to JWT ---
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: int = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except jwt.PyJWTError:
        raise credentials_exception

    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None or not user.is_active:
        raise credentials_exception
    return user


# ---------------------------------------------------------------------------
# Auth routes
# ---------------------------------------------------------------------------

@app.post("/auth/register", response_model=UserOut, status_code=201)
@limiter.limit("5/minute")
def register(request: Request, data: UserCreate, db: Session = Depends(get_db)):
    if db.query(User).filter(User.callsign == data.callsign).first():
        raise HTTPException(400, "Callsign already registered")
    if db.query(User).filter(User.email == data.email).first():
        raise HTTPException(400, "Email already registered")

    # First registered user becomes admin and is immediately active
    is_first_user = db.query(User).count() == 0
    user = User(
        callsign=data.callsign,
        name=data.name,
        email=data.email,
        hashed_password=hash_password(data.password),
        is_active=is_first_user,
        is_admin=is_first_user,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Notify opted-in admins about the new registration (skip for the first/admin user)
    if not is_first_user:
        notify_admins = (
            db.query(User)
            .filter(User.is_admin == True, User.notify_new_registrations == True, User.is_active == True)
            .all()
        )
        if notify_admins:
            send_email(
                to=[a.email for a in notify_admins],
                subject=f"[Ham Net Tracker] New Registration: {user.callsign}",
                body_html=f"""<div style="font-family:sans-serif;max-width:520px">
  <h2 style="color:#FF9900">New Operator Registration</h2>
  <p>A new user has registered and is awaiting your approval:</p>
  <table style="border-collapse:collapse;width:100%">
    <tr><td style="padding:6px 12px 6px 0;font-weight:bold">Callsign</td><td>{user.callsign}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:bold">Name</td><td>{user.name}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:bold">Email</td><td>{user.email}</td></tr>
    <tr><td style="padding:6px 12px 6px 0;font-weight:bold">Registered</td><td>{user.created_at.strftime('%Y-%m-%d %H:%M UTC')}</td></tr>
  </table>
  <p style="margin-top:16px">Log in to the <strong>Admin</strong> panel to approve or reject this account.</p>
</div>""",
                body_text=(
                    f"New registration pending approval:\n"
                    f"  Callsign : {user.callsign}\n"
                    f"  Name     : {user.name}\n"
                    f"  Email    : {user.email}\n\n"
                    f"Log in to the Admin panel to approve or reject this account."
                ),
            )

    return user


@app.post("/auth/login", response_model=Token)
@limiter.limit("10/minute")
def login(request: Request, form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    # Accept callsign or email as username
    user = (
        db.query(User).filter(User.callsign == form_data.username.upper()).first()
        or db.query(User).filter(User.email == form_data.username.lower()).first()
    )
    if not user or not verify_password(form_data.password, user.hashed_password):
        _log_auth_fail(request, f"bad_credentials username={form_data.username!r}")
        raise HTTPException(status_code=401, detail="Incorrect callsign/email or password")
    if not user.is_active:
        _log_auth_fail(request, f"inactive_account username={form_data.username!r}")
        raise HTTPException(status_code=403, detail="Account pending approval. Please contact the net administrator.")

    token = create_access_token(
        {"sub": str(user.id)},
        timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES),
    )
    return {"access_token": token, "token_type": "bearer", "user": user}


@app.get("/auth/me", response_model=UserOut)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@app.get("/stats")
def get_stats(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Quick stats for the sidebar dashboard panel."""
    from datetime import date, datetime, timezone

    # Net IDs the user can see (owned + shared)
    owned_ids = [r[0] for r in db.query(Net.id).filter(Net.owner_id == current_user.id).all()]
    shared_ids = [r[0] for r in db.query(NetShare.net_id).filter(NetShare.user_id == current_user.id).all()]
    all_net_ids = list(set(owned_ids + shared_ids))

    total_nets = len(all_net_ids)

    active_sessions = 0
    checkins_today = 0
    if all_net_ids:
        active_sessions = (
            db.query(func.count(NetSession.id))
            .filter(NetSession.net_id.in_(all_net_ids), NetSession.ended_at.is_(None))
            .scalar() or 0
        )
        today_start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
        checkins_today = (
            db.query(func.count(Checkin.id))
            .join(NetSession, Checkin.session_id == NetSession.id)
            .filter(NetSession.net_id.in_(all_net_ids), Checkin.checked_in_at >= today_start)
            .scalar() or 0
        )

    gmrs_row = db.query(SystemSetting).filter(SystemSetting.key == "gmrs_db_synced_at").first()

    return {
        "total_nets": total_nets,
        "active_sessions": active_sessions,
        "checkins_today": checkins_today,
        "gmrs_synced_at": gmrs_row.value[:10] if gmrs_row and gmrs_row.value else None,
    }


# ---------------------------------------------------------------------------
# API Token management
# ---------------------------------------------------------------------------

@app.post("/auth/tokens", response_model=ApiTokenCreated, status_code=201)
def create_api_token(
    data: ApiTokenCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Create a long-lived API token. The raw token is returned once — store it securely."""
    raw_token = "nt_" + secrets.token_hex(32)   # 64 hex chars → 256 bits
    token_hash = hashlib.sha256(raw_token.encode()).hexdigest()
    api_token = ApiToken(
        user_id=current_user.id,
        name=data.name,
        token_hash=token_hash,
    )
    db.add(api_token)
    db.commit()
    db.refresh(api_token)
    return ApiTokenCreated(id=api_token.id, name=api_token.name, token=raw_token, created_at=api_token.created_at)


@app.get("/auth/tokens", response_model=list[ApiTokenOut])
def list_api_tokens(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    return db.query(ApiToken).filter(ApiToken.user_id == current_user.id).all()


@app.delete("/auth/tokens/{token_id}", status_code=204)
def delete_api_token(
    token_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    api_token = db.query(ApiToken).filter(ApiToken.id == token_id, ApiToken.user_id == current_user.id).first()
    if not api_token:
        raise HTTPException(404, "Token not found")
    db.delete(api_token)
    db.commit()


# ---------------------------------------------------------------------------
# Public live page
# ---------------------------------------------------------------------------

@app.get("/live", response_class=HTMLResponse, include_in_schema=False)
def public_live_page():
    """Serve the public live nets page."""
    import pathlib
    p = pathlib.Path(__file__).parent / "public.html"
    return HTMLResponse(p.read_text())


@app.get("/public/active")
def public_active_sessions(db: Session = Depends(get_db)):
    """Return all currently active net sessions — no auth required."""
    sessions = (
        db.query(NetSession)
        .filter(NetSession.ended_at == None)
        .order_by(NetSession.started_at)
        .all()
    )
    result = []
    for s in sessions:
        net = db.query(Net).filter(Net.id == s.net_id).first()
        if not net:
            continue
        count = db.query(func.count(Checkin.id)).filter(Checkin.session_id == s.id).scalar()
        result.append({
            "session_id": s.id,
            "net_name": net.name,
            "frequency": net.frequency,
            "started_at": s.started_at.isoformat(),
            "checkin_count": count,
            **_duty_labels_for_session(net, s, db),
        })
    return result


@app.get("/public/sessions/{session_id}")
def public_session_detail(session_id: int, db: Session = Depends(get_db)):
    """Return session info + checkin list — no auth required."""
    s = db.query(NetSession).filter(NetSession.id == session_id, NetSession.ended_at == None).first()
    if not s:
        raise HTTPException(404, "Session not found or no longer active")
    net = db.query(Net).filter(Net.id == s.net_id).first()
    checkins = (
        db.query(Checkin)
        .filter(Checkin.session_id == session_id)
        .order_by(Checkin.checked_in_at)
        .all()
    )
    duty = _duty_labels_for_session(net, s, db) if net else {
        "ncs_callsign": None, "ncs_name": None,
        "broadcaster_callsign": None, "broadcaster_name": None, "broadcast_label": None,
    }
    return {
        "session_id": s.id,
        "net_name": net.name if net else "Unknown Net",
        "frequency": net.frequency if net else None,
        "started_at": s.started_at.isoformat(),
        "checkins": [
            {"callsign": c.callsign, "name": c.name}
            for c in checkins
        ],
        **duty,
    }


# ---------------------------------------------------------------------------
# Branding
# ---------------------------------------------------------------------------

BRANDING_KEYS = ("org_name", "tagline", "website_url")


def _get_setting(key: str, db: Session) -> Optional[str]:
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    return row.value if row else None


def _set_setting(key: str, value: Optional[str], db: Session):
    row = db.query(SystemSetting).filter(SystemSetting.key == key).first()
    if row:
        row.value = value
        row.updated_at = utcnow()
    else:
        db.add(SystemSetting(key=key, value=value))


def _logo_file() -> Optional[pathlib.Path]:
    """Return the logo file path if one exists (any image extension)."""
    for ext in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        p = UPLOADS_DIR / f"logo.{ext}"
        if p.exists():
            return p
    return None


@app.get("/branding", response_model=BrandingOut)
def get_branding(db: Session = Depends(get_db)):
    """Public endpoint — returns current branding settings."""
    return BrandingOut(
        org_name=_get_setting("org_name", db),
        tagline=_get_setting("tagline", db),
        website_url=_get_setting("website_url", db),
        has_logo=_logo_file() is not None,
    )


@app.get("/logo")
def get_logo():
    """Public endpoint — serves the uploaded logo file."""
    p = _logo_file()
    if not p:
        raise HTTPException(404, "No logo uploaded")
    ext = p.suffix.lstrip(".")
    mime = {
        "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
        "gif": "image/gif", "webp": "image/webp", "svg": "image/svg+xml",
    }.get(ext, "application/octet-stream")
    return Response(content=p.read_bytes(), media_type=mime)


@app.put("/admin/branding", response_model=BrandingOut)
def update_branding(
    data: BrandingUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Admin only — update branding text settings."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin only")
    _set_setting("org_name", data.org_name or None, db)
    _set_setting("tagline", data.tagline or None, db)
    _set_setting("website_url", data.website_url or None, db)
    db.commit()
    return BrandingOut(
        org_name=_get_setting("org_name", db),
        tagline=_get_setting("tagline", db),
        website_url=_get_setting("website_url", db),
        has_logo=_logo_file() is not None,
    )


@app.post("/admin/branding/logo", status_code=204)
async def upload_logo(
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
):
    """Admin only — upload a logo image (PNG, JPG, GIF, WebP, SVG)."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin only")
    ext = (file.filename or "").rsplit(".", 1)[-1].lower()
    if ext not in ("png", "jpg", "jpeg", "gif", "webp", "svg"):
        raise HTTPException(400, "Unsupported file type — use PNG, JPG, GIF, WebP, or SVG")
    # Remove any old logo files
    for old in UPLOADS_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)
    dest = UPLOADS_DIR / f"logo.{ext}"
    dest.write_bytes(await file.read())


@app.delete("/admin/branding/logo", status_code=204)
def delete_logo(current_user: User = Depends(get_current_user)):
    """Admin only — remove the current logo."""
    if not current_user.is_admin:
        raise HTTPException(403, "Admin only")
    for old in UPLOADS_DIR.glob("logo.*"):
        old.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Support tickets
# ---------------------------------------------------------------------------

class SupportTicketCreate(BaseModel):
    type: str
    subject: str
    body: str


SUPPORT_EMAIL = os.getenv("SUPPORT_EMAIL", "")   # helpdesk address for support tickets

@app.post("/support/ticket", status_code=204)
def create_support_ticket(
    data: SupportTicketCreate,
    current_user: User = Depends(get_current_user),
):
    if not _smtp_configured():
        raise HTTPException(503, "Email is not configured on this server")
    if not SUPPORT_EMAIL:
        raise HTTPException(503, "Support email address is not configured on this server")
    if not data.subject.strip() or not data.body.strip():
        raise HTTPException(400, "Subject and body are required")

    subject = f"[Net Tracker] {data.type}: {data.subject.strip()}"
    body_html = f"""
<p><strong>From:</strong> {current_user.name} ({current_user.callsign})<br>
<strong>Email:</strong> {current_user.email}<br>
<strong>Type:</strong> {data.type}</p>
<hr>
<p>{data.body.replace(chr(10), '<br>')}</p>
<hr>
<p style="color:#888;font-size:12px">Sent from Ham Radio Net Tracker by {current_user.callsign} — reply to this email to respond directly to the user.</p>
"""
    body_text = (
        f"From: {current_user.name} ({current_user.callsign})\n"
        f"Email: {current_user.email}\n"
        f"Type: {data.type}\n\n"
        f"{data.body}\n\n"
        f"---\nReply to: {current_user.email}"
    )

    from email.mime.multipart import MIMEMultipart as _MM
    from email.mime.text import MIMEText as _MT
    import smtplib as _smtp

    from_addr = SMTP_FROM or SMTP_USER
    msg = _MM("alternative")
    msg["Subject"] = subject
    msg["From"]    = from_addr
    msg["To"]      = SUPPORT_EMAIL
    msg["Reply-To"] = f"{current_user.name} <{current_user.email}>"
    if body_text:
        msg.attach(_MT(body_text, "plain"))
    msg.attach(_MT(body_html, "html"))

    try:
        if SMTP_USE_SSL:
            with _smtp.SMTP_SSL(SMTP_HOST, SMTP_PORT) as srv:
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.sendmail(from_addr, [SUPPORT_EMAIL], msg.as_string())
        else:
            with _smtp.SMTP(SMTP_HOST, SMTP_PORT) as srv:
                if SMTP_USE_TLS:
                    srv.starttls()
                srv.login(SMTP_USER, SMTP_PASSWORD)
                srv.sendmail(from_addr, [SUPPORT_EMAIL], msg.as_string())
        _email_log.info("Support ticket sent from %s — %s", current_user.callsign, subject)
    except Exception as exc:
        _email_log.warning("Failed to send support ticket: %s", exc)
        raise HTTPException(500, "Failed to send email — please try again later")


# ---------------------------------------------------------------------------
# Net routes
# ---------------------------------------------------------------------------

@app.get("/users", response_model=list[UserPublicOut])
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all active users (for share-with-user UI). Excludes the calling user."""
    users = (
        db.query(User)
        .filter(User.is_active == True, User.id != current_user.id)
        .order_by(User.callsign)
        .all()
    )
    return users


@app.get("/nets", response_model=list[NetOut])
def list_nets(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if current_user.is_admin:
        # Admins see every net
        nets = db.query(Net).order_by(Net.name).all()
    else:
        # Owned nets + nets shared with this user + nets shared with all
        shared_net_ids = (
            db.query(NetShare.net_id)
            .filter(or_(NetShare.user_id == current_user.id, NetShare.user_id == None))
            .subquery()
        )
        nets = (
            db.query(Net)
            .filter(or_(Net.owner_id == current_user.id, Net.id.in_(shared_net_ids)))
            .order_by(Net.name)
            .all()
        )
    return [_net_to_out(n, current_user, db) for n in nets]


@app.post("/nets", response_model=NetOut, status_code=201)
def create_net(data: NetCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net_type = data.net_type if data.net_type in ("ham", "gmrs") else "ham"
    net = Net(
        name=data.name,
        frequency=data.frequency,
        description=data.description,
        net_type=net_type,
        is_ares=data.is_ares if net_type == "ham" else False,
        dmr_talkgroup=data.dmr_talkgroup or None if net_type == "ham" else None,
        script=data.script,
        has_broadcast=data.has_broadcast,
        broadcast_label=(data.broadcast_label or None) if data.has_broadcast else None,
        owner_id=current_user.id,
    )
    db.add(net)
    db.commit()
    db.refresh(net)
    return net


@app.get("/nets/{net_id}", response_model=NetOut)
def get_net(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_net_for_user(net_id, current_user, db)
    return _net_to_out(net, current_user, db)


@app.put("/nets/{net_id}", response_model=NetOut)
def update_net(net_id: int, data: NetCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_owned_net(net_id, current_user, db)
    net_type = data.net_type if data.net_type in ("ham", "gmrs") else "ham"
    net.name = data.name
    net.frequency = data.frequency
    net.description = data.description
    net.net_type = net_type
    net.is_ares = data.is_ares if net_type == "ham" else False
    net.dmr_talkgroup = data.dmr_talkgroup or None if net_type == "ham" else None
    net.script = data.script
    net.has_broadcast = data.has_broadcast
    net.broadcast_label = (data.broadcast_label or None) if data.has_broadcast else None
    db.commit()
    db.refresh(net)
    return _net_to_out(net, current_user, db)


@app.delete("/nets/{net_id}", status_code=204)
def delete_net(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_owned_net(net_id, current_user, db)
    db.delete(net)
    db.commit()


# ---------------------------------------------------------------------------
# Session routes
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/sessions", response_model=list[SessionOut])
def list_sessions(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_net_for_user(net_id, current_user, db)
    sessions = (
        db.query(NetSession)
        .filter(NetSession.net_id == net_id)
        .order_by(NetSession.started_at.desc())
        .all()
    )
    result = []
    for s in sessions:
        count = db.query(func.count(Checkin.id)).filter(Checkin.session_id == s.id).scalar()
        out = SessionOut.model_validate(s)
        out.checkin_count = count
        result.append(out)
    return result


@app.post("/nets/{net_id}/sessions", response_model=SessionOut, status_code=201)
def start_session(net_id: int, data: SessionCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_net_for_user(net_id, current_user, db)
    session = NetSession(net_id=net_id, operator_id=current_user.id, name=data.name, notes=data.notes)
    db.add(session)
    db.commit()
    db.refresh(session)
    out = SessionOut.model_validate(session)
    out.checkin_count = 0
    return out


@app.get("/sessions/{session_id}", response_model=SessionOut)
def get_session(session_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    count = db.query(func.count(Checkin.id)).filter(Checkin.session_id == session.id).scalar()
    out = SessionOut.model_validate(session)
    out.checkin_count = count
    net = db.query(Net).filter(Net.id == session.net_id).first()
    if net:
        for k, v in _duty_labels_for_session(net, session, db).items():
            setattr(out, k, v)
    return out


@app.patch("/sessions/{session_id}/end", response_model=SessionOut)
def end_session(session_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    if session.ended_at is not None:
        raise HTTPException(400, "Session already ended")
    session.ended_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(session)
    count = db.query(func.count(Checkin.id)).filter(Checkin.session_id == session.id).scalar()
    out = SessionOut.model_validate(session)
    out.checkin_count = count
    return out


@app.patch("/sessions/{session_id}/rename", response_model=SessionOut)
def rename_session(session_id: int, data: SessionRename, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    session.name = data.name
    db.commit()
    db.refresh(session)
    count = db.query(func.count(Checkin.id)).filter(Checkin.session_id == session.id).scalar()
    out = SessionOut.model_validate(session)
    out.checkin_count = count
    return out


@app.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    db.delete(session)
    db.commit()


# ---------------------------------------------------------------------------
# Users (public directory for assignment dropdowns)
# ---------------------------------------------------------------------------

class UserSummary(BaseModel):
    id: int
    callsign: str
    name: str

    model_config = {"from_attributes": True}


@app.get("/users", response_model=list[UserSummary])
def list_users(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all active registered operators (callsign + name only, no emails)."""
    return (
        db.query(User)
        .filter(User.is_active == True)
        .order_by(User.callsign)
        .all()
    )


# ---------------------------------------------------------------------------
# Admin helpers
# ---------------------------------------------------------------------------

def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return current_user


# ---------------------------------------------------------------------------
# Admin routes
# ---------------------------------------------------------------------------

@app.get("/admin/users", response_model=list[UserOut])
def admin_list_users(admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """List all users (active, pending, and inactive)."""
    return db.query(User).order_by(User.created_at.desc()).all()


@app.patch("/admin/users/{user_id}/approve", response_model=UserOut)
def admin_approve_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Activate a pending user account and notify them by email."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.is_active = True
    db.commit()
    db.refresh(user)

    send_email(
        to=[user.email],
        subject="[Ham Net Tracker] Your Account Has Been Approved",
        body_html=f"""<div style="font-family:sans-serif;max-width:520px">
  <h2 style="color:#FF9900">Account Approved!</h2>
  <p>Hello <strong>{user.name}</strong> ({user.callsign}),</p>
  <p>Your Ham Net Tracker account has been reviewed and approved. You can now log in and start using the system.</p>
  {f'<p style="color:#888;font-size:12px">This email box is not monitored. If you have any questions please email <a href="mailto:{ADMIN_CONTACT_EMAIL}" style="color:#FF9900">{ADMIN_CONTACT_EMAIL}</a>.</p>' if ADMIN_CONTACT_EMAIL else ''}
  <p style="color:#888;font-size:12px">If you did not request this account, please disregard this message.</p>
</div>""",
        body_text=(
            f"Hello {user.name} ({user.callsign}),\n\n"
            f"Your Ham Net Tracker account has been approved. You can now log in.\n\n"
            + (f"This email box is not monitored. If you have any questions please email {ADMIN_CONTACT_EMAIL}.\n\n" if ADMIN_CONTACT_EMAIL else "")
            + "If you did not request this account, please disregard this message."
        ),
    )

    return user


class RejectUserBody(BaseModel):
    message: Optional[str] = None   # optional custom note to include in the rejection email


GITHUB_URL = os.getenv("GITHUB_URL", "https://github.com/LadyHwesta/ham-net-tracker")


@app.post("/admin/users/{user_id}/reject", status_code=204)
def admin_reject_user(user_id: int, body: RejectUserBody = RejectUserBody(), admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Send a rejection email then permanently delete the pending account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot reject your own account")

    custom_block_html = ""
    custom_block_text = ""
    if body.message and body.message.strip():
        msg = body.message.strip()
        custom_block_html = f'<p style="margin:12px 0"><strong>Message from the administrator:</strong><br>{msg}</p>'
        custom_block_text = f"\nMessage from the administrator:\n{msg}\n"

    github_block_html = (
        f'<p style="margin:12px 0;font-size:12px;color:#888">'
        f'Ham Net Tracker is open source. If you\'d like to run your own instance, '
        f'the code is available at <a href="{GITHUB_URL}" style="color:#FF9900">{GITHUB_URL}</a>.</p>'
    )
    github_block_text = (
        f"\nHam Net Tracker is open source. If you'd like to run your own instance, "
        f"the code is available at {GITHUB_URL}.\n"
    )

    send_email(
        to=[user.email],
        subject="[Ham Net Tracker] Registration Not Approved",
        body_html=f"""<div style="font-family:sans-serif;max-width:520px">
  <h2 style="color:#FF9900">Registration Not Approved</h2>
  <p>Hello <strong>{user.name}</strong> ({user.callsign}),</p>
  <p>Thank you for registering. Unfortunately your account request has not been approved at this time.</p>
  {custom_block_html}
  {f'<p style="color:#888;font-size:12px">If you have questions, please contact <a href="mailto:{ADMIN_CONTACT_EMAIL}" style="color:#FF9900">{ADMIN_CONTACT_EMAIL}</a>.</p>' if ADMIN_CONTACT_EMAIL else ''}
  {github_block_html}
</div>""",
        body_text=(
            f"Hello {user.name} ({user.callsign}),\n\n"
            f"Thank you for registering. Unfortunately your account request has not been approved at this time.\n"
            f"{custom_block_text}"
            + (f"\nIf you have questions, please contact {ADMIN_CONTACT_EMAIL}.\n" if ADMIN_CONTACT_EMAIL else "")
            + github_block_text
        ),
    )

    db.delete(user)
    db.commit()


@app.patch("/admin/users/{user_id}/deactivate", response_model=UserOut)
def admin_deactivate_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Deactivate a user account (they can no longer log in)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot deactivate your own account")
    user.is_active = False
    db.commit()
    db.refresh(user)
    return user


@app.patch("/admin/users/{user_id}/make-admin", response_model=UserOut)
def admin_make_admin(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Grant admin privileges to a user."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    user.is_admin = True
    user.is_active = True   # admins must be active
    db.commit()
    db.refresh(user)
    return user


@app.delete("/admin/users/{user_id}", status_code=204)
def admin_delete_user(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Permanently delete a user account."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if user.id == admin.id:
        raise HTTPException(400, "Cannot delete your own account")
    db.delete(user)
    db.commit()


@app.patch("/admin/users/{user_id}/notify", response_model=UserOut)
def admin_toggle_notify(user_id: int, admin: User = Depends(require_admin), db: Session = Depends(get_db)):
    """Toggle email notification opt-in for new registrations (admin accounts only)."""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if not user.is_admin:
        raise HTTPException(400, "Only admins can receive registration notifications")
    user.notify_new_registrations = not user.notify_new_registrations
    db.commit()
    db.refresh(user)
    return user


@app.get("/admin/email-status")
def admin_email_status(admin: User = Depends(require_admin)):
    """Return whether SMTP is configured (no credentials exposed)."""
    return {
        "configured": _smtp_configured(),
        "from_address": SMTP_FROM or SMTP_USER or None,
        "host": SMTP_HOST or None,
    }


# ---------------------------------------------------------------------------
# Checkin routes
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/checkins", response_model=list[CheckinOut])
def list_checkins(session_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_session_for_user(session_id, current_user, db)
    return db.query(Checkin).filter(Checkin.session_id == session_id).order_by(Checkin.checked_in_at).all()


@app.post("/sessions/{session_id}/checkins", response_model=CheckinOut, status_code=201)
def add_checkin(session_id: int, data: CheckinCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    if session.ended_at is not None:
        raise HTTPException(400, "Cannot add checkins to an ended session")

    # Prevent duplicate callsign in the same session — except for GMRS nets where a
    # single family licence is shared among multiple stations.
    net = db.query(Net).filter(Net.id == session.net_id).first()
    is_gmrs = net and net.net_type == "gmrs"
    if not is_gmrs:
        existing = db.query(Checkin).filter(
            Checkin.session_id == session_id,
            Checkin.callsign == data.callsign,
        ).first()
        if existing:
            raise HTTPException(409, f"{data.callsign} has already checked in to this session")

    checkin = Checkin(
        session_id=session_id,
        callsign=data.callsign,
        name=data.name,
        signal_report=data.signal_report,
        comments=data.comments,
        has_traffic=data.has_traffic,
        evac_zone=data.evac_zone or None,
        dmr_talkgroup=data.dmr_talkgroup or None,
        dmr_region=data.dmr_region or None,
    )
    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    # Auto-upsert evac zone when provided (ARES/ACES nets)
    if data.evac_zone:
        existing_ez = db.query(EvacZone).filter(
            EvacZone.net_id == session.net_id,
            EvacZone.callsign == data.callsign,
        ).first()
        if existing_ez:
            existing_ez.zone = data.evac_zone
            existing_ez.updated_at = datetime.now(timezone.utc)
        else:
            db.add(EvacZone(net_id=session.net_id, callsign=data.callsign, zone=data.evac_zone))
        db.commit()

    return checkin


@app.delete("/checkins/{checkin_id}", status_code=204)
def delete_checkin(checkin_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    checkin = db.query(Checkin).filter(Checkin.id == checkin_id).first()
    if not checkin:
        raise HTTPException(404, "Checkin not found")
    # Verify ownership via session → net
    _get_session_for_user(checkin.session_id, current_user, db)
    db.delete(checkin)
    db.commit()


# ---------------------------------------------------------------------------
# Evacuation Zone routes (ARES/ACES)
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/evac-zones", response_model=list[EvacZoneOut])
def list_evac_zones(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return all known evacuation zones for this net, sorted by zone then callsign."""
    _get_owned_net(net_id, current_user, db)
    return (
        db.query(EvacZone)
        .filter(EvacZone.net_id == net_id)
        .order_by(EvacZone.zone, EvacZone.callsign)
        .all()
    )


@app.patch("/nets/{net_id}/evac-zones/{callsign}", response_model=EvacZoneOut)
def update_evac_zone(
    net_id: int,
    callsign: str,
    data: EvacZoneUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Manually set or update the evac zone for a callsign on this net."""
    _get_owned_net(net_id, current_user, db)
    callsign = callsign.upper().strip()
    existing = db.query(EvacZone).filter(EvacZone.net_id == net_id, EvacZone.callsign == callsign).first()
    if existing:
        existing.zone = data.zone
        existing.updated_at = datetime.now(timezone.utc)
    else:
        existing = EvacZone(net_id=net_id, callsign=callsign, zone=data.zone)
        db.add(existing)
    db.commit()
    db.refresh(existing)
    return existing


@app.delete("/nets/{net_id}/evac-zones/{callsign}", status_code=204)
def delete_evac_zone(
    net_id: int,
    callsign: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Remove a callsign's evac zone record."""
    _get_owned_net(net_id, current_user, db)
    ez = db.query(EvacZone).filter(EvacZone.net_id == net_id, EvacZone.callsign == callsign.upper()).first()
    if ez:
        db.delete(ez)
        db.commit()


@app.patch("/checkins/{checkin_id}/traffic", response_model=CheckinOut)
def toggle_traffic(checkin_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Toggle has_traffic flag on an existing checkin."""
    checkin = db.query(Checkin).filter(Checkin.id == checkin_id).first()
    if not checkin:
        raise HTTPException(404, "Checkin not found")
    _get_session_for_user(checkin.session_id, current_user, db)
    checkin.has_traffic = not checkin.has_traffic
    db.commit()
    db.refresh(checkin)
    return checkin


# ---------------------------------------------------------------------------
# Expected Stations
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/expected", response_model=list[ExpectedStation])
def expected_stations(
    net_id: int,
    weeks: int = Query(4, ge=1, le=52),
    min_checkins: int = Query(2, ge=1),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return callsigns that checked in >= min_checkins times in the past N weeks for this net."""
    _get_owned_net(net_id, current_user, db)

    cutoff = datetime.now(timezone.utc) - timedelta(weeks=weeks)

    rows = (
        db.query(
            Checkin.callsign,
            func.max(Checkin.name).label("name"),
            func.count(Checkin.id).label("cnt"),
            func.max(Checkin.checked_in_at).label("last_checkin"),
        )
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == net_id, Checkin.checked_in_at >= cutoff)
        .group_by(Checkin.callsign)
        .having(func.count(Checkin.id) >= min_checkins)
        .order_by(func.count(Checkin.id).desc())
        .all()
    )

    import re as _re
    def _suffix(cs: str) -> str:
        """Return just the letter suffix after the district digit for sorting."""
        m = _re.search(r'\d([A-Z]+)$', cs.upper())
        return m.group(1) if m else cs

    stations = [
        ExpectedStation(
            callsign=r.callsign,
            name=r.name,
            checkin_count=r.cnt,
            last_checkin=r.last_checkin,
        )
        for r in rows
    ]
    stations.sort(key=lambda s: _suffix(s.callsign))
    return stations


# ---------------------------------------------------------------------------
# Session summary & ICS-205
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/summary", response_model=SessionSummary)
def session_summary(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = _get_session_for_user(session_id, current_user, db)
    net = db.query(Net).filter(Net.id == session.net_id).first()
    checkins = db.query(Checkin).filter(Checkin.session_id == session_id).all()

    duration_minutes = None
    if session.started_at and session.ended_at:
        delta = session.ended_at - session.started_at
        duration_minutes = int(delta.total_seconds() / 60)

    # New stations: callsigns that appear in this session but not in any prior session for this net
    this_callsigns = {c.callsign for c in checkins}
    prior = (
        db.query(Checkin.callsign)
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == session.net_id, NetSession.id != session_id)
        .distinct()
        .all()
    )
    prior_callsigns = {r.callsign for r in prior}
    new_stations = len(this_callsigns - prior_callsigns)

    operator_callsign = None
    if session.operator_id:
        op = db.query(User).filter(User.id == session.operator_id).first()
        operator_callsign = op.callsign if op else None

    return SessionSummary(
        session_id=session_id,
        net_name=net.name if net else "Unknown",
        started_at=session.started_at,
        ended_at=session.ended_at,
        duration_minutes=duration_minutes,
        total_checkins=len(checkins),
        traffic_count=sum(1 for c in checkins if c.has_traffic),
        new_stations=new_stations,
        operator_callsign=operator_callsign,
        net_frequency=net.frequency if net else None,
    )


@app.get("/sessions/{session_id}/ics205")
def session_ics205(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return a printable HTML ICS-205 / net log for this session."""
    session = _get_session_for_user(session_id, current_user, db)
    net = db.query(Net).filter(Net.id == session.net_id).first()
    checkins = db.query(Checkin).filter(Checkin.session_id == session_id).order_by(Checkin.checked_in_at).all()
    traffic_msgs = db.query(TrafficMessage).filter(TrafficMessage.session_id == session_id).order_by(TrafficMessage.created_at).all()

    op_callsign = ""
    if session.operator_id:
        op = db.query(User).filter(User.id == session.operator_id).first()
        op_callsign = op.callsign if op else ""

    started = session.started_at.strftime("%Y-%m-%d %H%MZ") if session.started_at else ""
    ended   = session.ended_at.strftime("%H%MZ") if session.ended_at else "—"
    freq    = net.frequency if net and net.frequency else "—"

    checkin_rows = ""
    for i, c in enumerate(checkins, 1):
        traffic_flag = " 📢" if c.has_traffic else ""
        zone_cell = f"<td>{c.evac_zone or '—'}</td>" if net and net.is_ares else ""
        checkin_rows += (
            f"<tr><td>{i}</td><td>{c.checked_in_at.strftime('%H%MZ')}</td>"
            f"<td><strong>{c.callsign}</strong></td><td>{c.name or ''}</td>"
            f"<td>{c.signal_report or ''}</td><td>{c.comments or ''}{traffic_flag}</td>"
            f"{zone_cell}</tr>\n"
        )

    traffic_rows = ""
    for i, m in enumerate(traffic_msgs, 1):
        traffic_rows += (
            f"<tr><td>{i}</td><td>{m.msg_number or '—'}</td>"
            f"<td>{m.origin_callsign}</td><td>{m.dest_info or '—'}</td>"
            f"<td>{m.msg_type.replace('_',' ').title()}</td>"
            f"<td>{m.status.title()}</td><td>{m.notes or ''}</td></tr>\n"
        )

    zone_th = "<th>Zone</th>" if net and net.is_ares else ""
    traffic_section = ""
    if traffic_msgs:
        traffic_section = f"""
        <h3>Traffic Log ({len(traffic_msgs)} message{'s' if len(traffic_msgs)!=1 else ''})</h3>
        <table><thead><tr><th>#</th><th>Msg #</th><th>Origin</th><th>Destination</th>
        <th>Type</th><th>Status</th><th>Notes</th></tr></thead>
        <tbody>{traffic_rows}</tbody></table>"""

    html = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"/>
<title>ICS-205 Net Log — {net.name if net else 'Net'}</title>
<style>
  body {{ font-family: Arial, sans-serif; font-size: 11pt; margin: 20mm; color: #000; }}
  h1 {{ font-size: 16pt; margin-bottom: 4px; }}
  h2 {{ font-size: 13pt; margin-top: 16px; margin-bottom: 4px; border-bottom: 1px solid #000; }}
  h3 {{ font-size: 12pt; margin-top: 16px; margin-bottom: 4px; }}
  table {{ width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 10pt; }}
  th {{ background: #ddd; border: 1px solid #999; padding: 4px 6px; text-align: left; }}
  td {{ border: 1px solid #ccc; padding: 3px 6px; }}
  .header-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 8px; margin: 12px 0; }}
  .field {{ margin-bottom: 4px; }}
  .label {{ font-weight: bold; font-size: 9pt; color: #555; display: block; }}
  .value {{ font-size: 11pt; border-bottom: 1px solid #aaa; padding-bottom: 2px; min-height: 18px; }}
  @media print {{ body {{ margin: 10mm; }} }}
</style>
</head><body>
<h1>ICS-205 — Amateur Radio Net Log</h1>
<div class="header-grid">
  <div>
    <div class="field"><span class="label">Net Name / Incident</span>
      <span class="value">{net.name if net else ''}</span></div>
    <div class="field"><span class="label">Frequency / Mode</span>
      <span class="value">{freq}</span></div>
    <div class="field"><span class="label">Net Control Station</span>
      <span class="value">{op_callsign}</span></div>
  </div>
  <div>
    <div class="field"><span class="label">Session Start (UTC)</span>
      <span class="value">{started}</span></div>
    <div class="field"><span class="label">Session End (UTC)</span>
      <span class="value">{ended}</span></div>
    <div class="field"><span class="label">Total Check-Ins</span>
      <span class="value">{len(checkins)}</span></div>
  </div>
</div>

<h2>Station Check-In Log</h2>
<table>
  <thead><tr><th>#</th><th>Time (UTC)</th><th>Callsign</th><th>Name</th>
    <th>Signal</th><th>Comments / Traffic</th>{zone_th}</tr></thead>
  <tbody>{checkin_rows}</tbody>
</table>
{traffic_section}

<p style="margin-top:24px;font-size:9pt;color:#555">
  Prepared by: {op_callsign} &nbsp;|&nbsp; Printed: <span id="print-ts"></span>
</p>
<script>document.getElementById('print-ts').textContent = new Date().toUTCString();</script>
</body></html>"""

    from fastapi.responses import HTMLResponse
    return HTMLResponse(content=html)


# ---------------------------------------------------------------------------
# Traffic messages
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/traffic-messages", response_model=list[TrafficMessageOut])
def list_traffic_messages(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_session_for_user(session_id, current_user, db)
    return db.query(TrafficMessage).filter(TrafficMessage.session_id == session_id).order_by(TrafficMessage.created_at).all()


@app.post("/sessions/{session_id}/traffic-messages", response_model=TrafficMessageOut, status_code=201)
def create_traffic_message(
    session_id: int,
    body: TrafficMessageCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_session_for_user(session_id, current_user, db)
    msg = TrafficMessage(
        session_id=session_id,
        origin_callsign=body.origin_callsign.upper().strip(),
        dest_info=body.dest_info,
        msg_number=body.msg_number,
        msg_type=body.msg_type,
        status=body.status,
        notes=body.notes,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@app.patch("/traffic-messages/{msg_id}", response_model=TrafficMessageOut)
def update_traffic_message(
    msg_id: int,
    body: TrafficMessageUpdate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = db.query(TrafficMessage).filter(TrafficMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    _get_session_for_user(msg.session_id, current_user, db)
    for field, val in body.model_dump(exclude_none=True).items():
        setattr(msg, field, val)
    db.commit()
    db.refresh(msg)
    return msg


@app.delete("/traffic-messages/{msg_id}", status_code=204)
def delete_traffic_message(
    msg_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    msg = db.query(TrafficMessage).filter(TrafficMessage.id == msg_id).first()
    if not msg:
        raise HTTPException(404, "Message not found")
    _get_session_for_user(msg.session_id, current_user, db)
    db.delete(msg)
    db.commit()


# ---------------------------------------------------------------------------
# Station remarks
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/stations/{callsign}/remark", response_model=Optional[StationRemarkOut])
def get_station_remark(
    net_id: int,
    callsign: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_net(net_id, current_user, db)
    remark = db.query(StationRemark).filter(
        StationRemark.net_id == net_id,
        StationRemark.callsign == callsign.upper(),
    ).first()
    return remark  # None returns as null → 200 with null body; frontend handles


@app.put("/nets/{net_id}/stations/{callsign}/remark", response_model=StationRemarkOut)
def upsert_station_remark(
    net_id: int,
    callsign: str,
    body: StationRemarkUpsert,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_net(net_id, current_user, db)
    cs = callsign.upper().strip()
    remark = db.query(StationRemark).filter(
        StationRemark.net_id == net_id,
        StationRemark.callsign == cs,
    ).first()
    if remark:
        remark.remark = body.remark
        remark.updated_by = current_user.id
        remark.updated_at = datetime.now(timezone.utc)
    else:
        remark = StationRemark(
            net_id=net_id,
            callsign=cs,
            remark=body.remark,
            updated_by=current_user.id,
        )
        db.add(remark)
    db.commit()
    db.refresh(remark)
    return remark


@app.delete("/nets/{net_id}/stations/{callsign}/remark", status_code=204)
def delete_station_remark(
    net_id: int,
    callsign: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _get_owned_net(net_id, current_user, db)
    remark = db.query(StationRemark).filter(
        StationRemark.net_id == net_id,
        StationRemark.callsign == callsign.upper(),
    ).first()
    if remark:
        db.delete(remark)
        db.commit()


# ---------------------------------------------------------------------------
# History / Stats
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/history", response_model=list[CallsignHistoryItem])
def net_history(
    net_id: int,
    limit: int = Query(100, ge=1, le=1000),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return checkin counts per callsign across all sessions of a net.
    Also includes recent_checkins: count of checkins in the past 14 days.
    """
    _get_owned_net(net_id, current_user, db)

    rows = (
        db.query(
            Checkin.callsign,
            func.max(Checkin.name).label("name"),
            func.count(Checkin.id).label("total_checkins"),
            func.max(Checkin.checked_in_at).label("last_checkin"),
        )
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == net_id)
        .group_by(Checkin.callsign)
        .order_by(func.count(Checkin.id).desc())
        .limit(limit)
        .all()
    )

    now = datetime.now(timezone.utc)

    # Recent 14-day counts
    cutoff_2w = now - timedelta(days=14)
    recent_2w = {
        r.callsign: r.cnt
        for r in db.query(Checkin.callsign, func.count(Checkin.id).label("cnt"))
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == net_id, Checkin.checked_in_at >= cutoff_2w)
        .group_by(Checkin.callsign).all()
    }

    # Recent 28-day counts
    cutoff_4w = now - timedelta(days=28)
    recent_4w = {
        r.callsign: r.cnt
        for r in db.query(Checkin.callsign, func.count(Checkin.id).label("cnt"))
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == net_id, Checkin.checked_in_at >= cutoff_4w)
        .group_by(Checkin.callsign).all()
    }

    # Who checked in to the most recent ended session?
    last_session = (
        db.query(NetSession)
        .filter(NetSession.net_id == net_id, NetSession.ended_at.isnot(None))
        .order_by(NetSession.started_at.desc())
        .first()
    )
    last_session_callsigns: set = set()
    if last_session:
        last_session_callsigns = {
            c.callsign for c in
            db.query(Checkin).filter(Checkin.session_id == last_session.id).all()
        }

    return [
        CallsignHistoryItem(
            callsign=r.callsign,
            name=r.name,
            total_checkins=r.total_checkins,
            recent_checkins=recent_2w.get(r.callsign, 0),
            recent_4w_checkins=recent_4w.get(r.callsign, 0),
            checked_in_last_session=(r.callsign in last_session_callsigns),
            last_checkin=r.last_checkin,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# CSV Export
# ---------------------------------------------------------------------------

@app.get("/sessions/{session_id}/export")
def export_session_csv(session_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    session = _get_session_for_user(session_id, current_user, db)
    checkins = db.query(Checkin).filter(Checkin.session_id == session_id).order_by(Checkin.checked_in_at).all()
    net = db.query(Net).filter(Net.id == session.net_id).first()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["#", "Callsign", "Name", "Signal Report", "Comments", "Checked In At"])
    for i, c in enumerate(checkins, start=1):
        writer.writerow([i, c.callsign, c.name or "", c.signal_report or "", c.comments or "", c.checked_in_at.isoformat()])

    filename = f"session_{session_id}_{net.name.replace(' ', '_')}.csv"
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.get("/nets/{net_id}/export")
def export_net_csv(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_net_for_user(net_id, current_user, db)

    rows = (
        db.query(Checkin, NetSession)
        .join(NetSession, NetSession.id == Checkin.session_id)
        .filter(NetSession.net_id == net_id)
        .order_by(NetSession.started_at.desc(), Checkin.checked_in_at)
        .all()
    )

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Session ID", "Session Started", "Callsign", "Name", "Signal Report", "Comments", "Checked In At"])
    for checkin, session in rows:
        writer.writerow([
            session.id,
            session.started_at.isoformat(),
            checkin.callsign,
            checkin.name or "",
            checkin.signal_report or "",
            checkin.comments or "",
            checkin.checked_in_at.isoformat(),
        ])

    filename = f"net_{net_id}_{net.name.replace(' ', '_')}_all_sessions.csv"
    output.seek(0)
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# ---------------------------------------------------------------------------
# Callsign Lookup
# ---------------------------------------------------------------------------

class CallsignLookupResult(BaseModel):
    callsign: str
    status: str          # "found" | "not_found" | "error"
    name: Optional[str] = None
    license_class: Optional[str] = None
    state: Optional[str] = None
    grid: Optional[str] = None
    expires: Optional[str] = None
    source: Optional[str] = None


class CallsignSearchResult(BaseModel):
    callsign: str
    name: Optional[str] = None
    license_class: Optional[str] = None
    state: Optional[str] = None


@app.get("/callsign/search", response_model=list[CallsignSearchResult])
def search_callsigns(
    q: str = Query(..., min_length=2, max_length=12),
    net_id: Optional[int] = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Search checkin history for callsigns whose suffix matches q.
    Searches the current net first (if net_id provided), then all nets owned by the user.
    Results are sorted by callsign suffix.
    MUST be defined before /callsign/{callsign}/lookup so 'search' is not captured as a path param."""
    import re as _re

    q = q.upper().strip()

    def _suffix(cs: str) -> str:
        m = _re.search(r'\d([A-Z]+)$', cs.upper())
        return m.group(1) if m else cs

    def _run_query(extra_filter) -> list[CallsignSearchResult]:
        rows = (
            db.query(
                Checkin.callsign,
                func.max(Checkin.name).label("name"),
            )
            .join(NetSession, NetSession.id == Checkin.session_id)
            .join(Net, Net.id == NetSession.net_id)
            .filter(Net.owner_id == current_user.id)
            .filter(extra_filter)
            # suffix match: callsign ends with q (case-insensitive)
            .filter(Checkin.callsign.ilike(f"%{q}"))
            .group_by(Checkin.callsign)
            .all()
        )
        results = [
            CallsignSearchResult(callsign=r.callsign, name=r.name, license_class=None)
            for r in rows
        ]
        results.sort(key=lambda r: _suffix(r.callsign))
        return results[:20]

    # 1. Search current net's history first
    if net_id:
        results = _run_query(Net.id == net_id)
        if results:
            return results

    # 2. Fall back to all nets owned by this user
    results = _run_query(True)
    return results


# Cache TTLs for callsign lookups
_CALLSIGN_CACHE_TTL_FOUND = 30 * 24 * 3600      # 30 days — licenses rarely change
_CALLSIGN_CACHE_TTL_NOT_FOUND = 7 * 24 * 3600   # 7 days — callsign might get issued


def _callsign_cache_read(callsign: str, db: Session) -> Optional[CallsignLookupResult]:
    """Return a cached lookup result if still within TTL, else None."""
    row = db.query(CallsignCache).filter(CallsignCache.callsign == callsign).first()
    if not row:
        return None
    ttl = _CALLSIGN_CACHE_TTL_FOUND if row.status == "found" else _CALLSIGN_CACHE_TTL_NOT_FOUND
    # SQLite returns tz-naive datetimes; PostgreSQL returns tz-aware — normalize to UTC.
    cached_at = row.cached_at
    if cached_at.tzinfo is None:
        cached_at = cached_at.replace(tzinfo=timezone.utc)
    if (utcnow() - cached_at).total_seconds() > ttl:
        return None
    return CallsignLookupResult(
        callsign=row.callsign,
        status=row.status,
        name=row.name,
        license_class=row.license_class,
        state=row.state,
        grid=row.grid,
        expires=row.expires,
        source=row.source,
    )


def _callsign_cache_write(result: CallsignLookupResult, db: Session) -> None:
    """Upsert a lookup result into the local cache."""
    row = db.query(CallsignCache).filter(CallsignCache.callsign == result.callsign).first()
    if row:
        row.status = result.status
        row.name = result.name
        row.license_class = result.license_class
        row.state = result.state
        row.grid = result.grid
        row.expires = result.expires
        row.source = result.source
        row.cached_at = utcnow()
    else:
        db.add(CallsignCache(
            callsign=result.callsign,
            status=result.status,
            name=result.name,
            license_class=result.license_class,
            state=result.state,
            grid=result.grid,
            expires=result.expires,
            source=result.source,
        ))
    db.commit()


import re as _re
_GMRS_CS_RE = _re.compile(r'^[A-Z]{3,4}\d{3,4}$')

def _is_gmrs_callsign(cs: str) -> bool:
    """Return True if callsign matches the FCC GMRS format (e.g. WQXH7777)."""
    return bool(_GMRS_CS_RE.match(cs))


@app.get("/callsign/{callsign}/lookup", response_model=CallsignLookupResult)
async def lookup_callsign(
    callsign: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """
    Resolve a callsign to FCC license data.

    GMRS callsigns (e.g. WQXH7777):
      1. Local gmrs_licenses table (populated by gmrs_sync.py from FCC bulk download)
      2. FCC ULS API fallback (if local DB is empty / callsign not found locally)

    Ham callsigns (e.g. W1AW):
      1. Local callsign_cache (30-day TTL found / 7-day not_found)
      2. FCC ULS API
      3. HamDB.org
      4. callook.info
    """
    import logging
    log = logging.getLogger("callsign_lookup")
    callsign = callsign.upper().strip()

    # ── GMRS branch ──────────────────────────────────────────────────────────
    if _is_gmrs_callsign(callsign):
        # 1. Local gmrs_licenses table (fast, no external call)
        row = db.query(GmrsLicense).filter(GmrsLicense.callsign == callsign).first()
        log.info("GMRS lookup: callsign=%s row_found=%s status=%r", callsign, row is not None, row.status if row else None)
        if row:
            status = "found" if (row.status or "").strip() == "A" else "not_found"
            return CallsignLookupResult(
                callsign=row.callsign,
                status=status,
                name=row.licensee_name,
                license_class=None,   # GMRS has no license classes
                state=row.state,
                grid=None,
                expires=row.expires,
                source="FCC GMRS DB",
            )

        # 2. FCC ULS API fallback (when local DB hasn't been synced yet, or callsign
        #    is very newly issued between weekly syncs)
        log.info("GMRS %s not in local DB — trying FCC ULS API", callsign)
        cached = _callsign_cache_read(callsign, db)
        if cached:
            return cached

        def _save(result: CallsignLookupResult) -> CallsignLookupResult:
            _callsign_cache_write(result, db)
            return result

        async with httpx.AsyncClient(timeout=8.0) as client:
            try:
                r = await client.get(
                    "https://data.fcc.gov/api/license-view/basicSearch/getLicenses",
                    params={"format": "json", "searchValue": callsign},
                    headers={"User-Agent": "HamNetTracker/1.0"},
                )
                if r.status_code == 200:
                    data = r.json()
                    rows = data.get("Licenses", {}).get("License", [])
                    match = next(
                        (lic for lic in rows if lic.get("callsign", "").upper() == callsign),
                        None,
                    )
                    if match and match.get("statusDesc", "").lower() == "active":
                        name = (match.get("licenseeName") or "").strip().title() or None
                        return _save(CallsignLookupResult(
                            callsign=match["callsign"],
                            status="found",
                            name=name,
                            license_class=None,
                            state=None,
                            grid=None,
                            expires=match.get("expiredDate") or None,
                            source="FCC ULS",
                        ))
                    elif match:
                        log.info("FCC ULS: GMRS %s found but status=%s", callsign, match.get("statusDesc"))
                else:
                    log.warning("FCC ULS HTTP %s for GMRS %s", r.status_code, callsign)
            except Exception as exc:
                log.warning("FCC ULS error for GMRS %s: %s", callsign, exc)

        log.warning("GMRS lookup exhausted for %s", callsign)
        return _save(CallsignLookupResult(callsign=callsign, status="not_found"))

    # ── Ham branch ───────────────────────────────────────────────────────────
    # Return cached result if still fresh
    cached = _callsign_cache_read(callsign, db)
    if cached:
        return cached

    def _save(result: CallsignLookupResult) -> CallsignLookupResult:
        """Persist to cache then return."""
        _callsign_cache_write(result, db)
        return result

    async with httpx.AsyncClient(timeout=8.0) as client:

        # --- 1. FCC ULS (official database) ---
        try:
            r = await client.get(
                "https://data.fcc.gov/api/license-view/basicSearch/getLicenses",
                params={"format": "json", "searchValue": callsign, "licenseType": "Amateur"},
                headers={"User-Agent": "HamNetTracker/1.0"},
            )
            if r.status_code == 200:
                data = r.json()
                licenses = data.get("Licenses", {})
                rows = licenses.get("License", [])
                # Find exact callsign match (search can return partial matches)
                match = next(
                    (lic for lic in rows if lic.get("callsign", "").upper() == callsign),
                    None,
                )
                if match and match.get("statusDesc", "").lower() == "active":
                    name = (match.get("licenseeName") or "").strip().title() or None
                    # FCC returns "JOHN DOE" — title-case it to "John Doe"
                    return _save(CallsignLookupResult(
                        callsign=match["callsign"],
                        status="found",
                        name=name,
                        license_class=match.get("licenseClass") or None,
                        state=None,   # not in basic FCC search result
                        grid=None,
                        expires=match.get("expiredDate") or None,
                        source="FCC ULS",
                    ))
                elif match:
                    # Callsign exists but licence is not active
                    log.info("FCC ULS: %s found but status=%s", callsign, match.get("statusDesc"))
            else:
                log.warning("FCC ULS HTTP %s for %s", r.status_code, callsign)
        except Exception as exc:
            log.warning("FCC ULS error for %s: %s", callsign, exc)

        # --- 2. HamDB.org ---
        try:
            r = await client.get(
                f"https://hamdb.org/api/{callsign}/json",
                headers={"User-Agent": "HamNetTracker/1.0"},
            )
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, dict):
                    log.info("HamDB: unexpected response type %s for %s", type(data).__name__, callsign)
                    raise ValueError("unexpected response")
                hamdb = data.get("hamdb", {})
                msgs = hamdb.get("messages", {})
                cs = hamdb.get("callsign", {})
                if msgs.get("status") == "OK" and cs.get("call"):
                    fname = (cs.get("fname") or "").strip()
                    lname = (cs.get("name") or "").strip()
                    name = f"{fname} {lname}".strip() or None
                    return _save(CallsignLookupResult(
                        callsign=cs["call"],
                        status="found",
                        name=name,
                        license_class=cs.get("class") or None,
                        state=cs.get("state") or None,
                        grid=cs.get("grid") or None,
                        expires=cs.get("expires") or None,
                        source="HamDB",
                    ))
                else:
                    log.info("HamDB: no result for %s (status=%s)", callsign, msgs.get("status"))
            else:
                log.warning("HamDB HTTP %s for %s", r.status_code, callsign)
        except Exception as exc:
            log.warning("HamDB error for %s: %s", callsign, exc)

        # --- 3. callook.info ---
        try:
            r = await client.get(
                f"https://callook.info/{callsign}/json",
                headers={"User-Agent": "HamNetTracker/1.0"},
            )
            if r.status_code == 200:
                data = r.json()
                if not isinstance(data, dict):
                    log.info("callook.info: unexpected top-level type %s for %s", type(data).__name__, callsign)
                elif data.get("status") == "VALID":
                    # Each nested field may be a dict OR a plain string depending
                    # on license type — use _safe_get() throughout.
                    def _safe_get(obj, key, default=None):
                        if isinstance(obj, dict):
                            return obj.get(key, default)
                        return default

                    name_obj  = data.get("name", {})
                    current   = data.get("current", {})
                    addr      = data.get("address", {})
                    loc       = data.get("location", {})
                    other     = data.get("otherInfo", {})

                    # Name: might be {"first":..,"last":..} or a plain string
                    if isinstance(name_obj, dict):
                        first = (_safe_get(name_obj, "first") or "").strip()
                        last  = (_safe_get(name_obj, "last")  or "").strip()
                        name  = f"{first} {last}".strip() or None
                    else:
                        name = str(name_obj).strip() or None

                    # State from address line2 e.g. "NEWINGTON, CT 06111"
                    state = None
                    line2 = _safe_get(addr, "line2") or ""
                    if "," in line2:
                        parts = line2.split(",")
                        state_zip = parts[-1].strip().split()
                        state = state_zip[0] if state_zip else None

                    return _save(CallsignLookupResult(
                        callsign=_safe_get(current, "callsign") or callsign,
                        status="found",
                        name=name,
                        license_class=_safe_get(current, "operClass") or None,
                        state=state,
                        grid=_safe_get(loc, "gridsquare") or None,
                        expires=_safe_get(other, "expiryDate") or None,
                        source="callook.info",
                    ))
                else:
                    log.info("callook.info: status=%s for %s", data.get("status") if isinstance(data, dict) else data, callsign)
            else:
                log.warning("callook.info HTTP %s for %s", r.status_code, callsign)
        except Exception as exc:
            log.warning("callook.info error for %s: %s", callsign, exc)

    log.warning("All sources exhausted for %s — returning not_found", callsign)
    return _save(CallsignLookupResult(callsign=callsign, status="not_found"))


# ---------------------------------------------------------------------------
# Net Schedules
# ---------------------------------------------------------------------------

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class ScheduleCreate(BaseModel):
    day_of_week: int        # 0=Monday … 6=Sunday
    start_time: str         # "HH:MM"
    timezone: str = "UTC"
    notes: Optional[str] = None

    @field_validator("day_of_week")
    @classmethod
    def valid_day(cls, v):
        if not 0 <= v <= 6:
            raise ValueError("day_of_week must be 0 (Monday) through 6 (Sunday)")
        return v

    @field_validator("start_time")
    @classmethod
    def valid_time(cls, v):
        import re
        if not re.match(r"^\d{2}:\d{2}$", v):
            raise ValueError("start_time must be HH:MM")
        return v


class ScheduleOut(BaseModel):
    id: int
    net_id: int
    day_of_week: int
    day_name: str
    start_time: str
    timezone: str
    notes: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class SignupCreate(BaseModel):
    schedule_id: int
    slot_date: date
    role: str = "net_control"   # 'net_control' | 'broadcaster' | 'both'
    # Self sign-up: provide callsign directly.
    # Assignment: provide assigned_user_id and callsign/name/email are pulled from that user.
    callsign: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    notes: Optional[str] = None
    assigned_user_id: Optional[int] = None   # set when net owner assigns another operator

    @field_validator("callsign")
    @classmethod
    def callsign_upper(cls, v):
        if v:
            return v.upper().strip()
        return v

    @field_validator("role")
    @classmethod
    def valid_role(cls, v):
        if v not in ("net_control", "broadcaster", "both"):
            raise ValueError("role must be net_control, broadcaster, or both")
        return v


class SignupOut(BaseModel):
    id: int
    schedule_id: int
    net_id: int
    slot_date: date
    role: str = "net_control"
    callsign: str
    name: Optional[str]
    email: Optional[str]
    notes: Optional[str]
    signed_up_at: datetime
    is_mine: bool = False   # True if current user owns this signup

    model_config = {"from_attributes": True}


class UpcomingSlot(BaseModel):
    slot_date: date
    day_name: str
    schedule_id: int
    signups: list[SignupOut] = []   # empty = fully open


def _next_occurrences(day_of_week: int, weeks: int = 8) -> list[date]:
    """Return the next `weeks` dates (including today if it matches) for a given weekday."""
    today = date.today()
    days_ahead = (day_of_week - today.weekday()) % 7
    first = today + timedelta(days=days_ahead)
    return [first + timedelta(weeks=i) for i in range(weeks)]


def _signup_to_out(s: NetControlSignup, current_user: User) -> SignupOut:
    return SignupOut(
        id=s.id, schedule_id=s.schedule_id, net_id=s.net_id,
        slot_date=s.slot_date, role=s.role, callsign=s.callsign, name=s.name,
        email=s.email, notes=s.notes, signed_up_at=s.signed_up_at,
        is_mine=(s.user_id == current_user.id),
    )


def _duty_for_date(net_id: int, slot_date: date, db: Session) -> tuple:
    """Return (net_control_signup, broadcaster_signup) ORM rows for this net on slot_date,
    across all of its schedules. A signup with role='both' fills both."""
    signups = (
        db.query(NetControlSignup)
        .filter(NetControlSignup.net_id == net_id, NetControlSignup.slot_date == slot_date)
        .all()
    )
    nc = next((s for s in signups if s.role in ("net_control", "both")), None)
    bc = next((s for s in signups if s.role in ("broadcaster", "both")), None)
    return nc, bc


def _duty_labels_for_session(net: Net, session: NetSession, db: Session) -> dict:
    """Net Control / Broadcaster display info for a session, sourced from the schedule
    sign-up matching the session's date when one exists, falling back to whoever
    actually started the session for Net Control."""
    nc, bc = _duty_for_date(net.id, session.started_at.date(), db)
    operator = db.query(User).filter(User.id == session.operator_id).first() if session.operator_id else None
    return {
        "ncs_callsign": nc.callsign if nc else (operator.callsign if operator else None),
        "ncs_name": nc.name if nc else (operator.name if operator else None),
        "broadcaster_callsign": bc.callsign if bc else None,
        "broadcaster_name": bc.name if bc else None,
        "broadcast_label": net.broadcast_label if (net.has_broadcast and bc) else None,
    }


def _schedule_to_out(s: NetSchedule) -> ScheduleOut:
    return ScheduleOut(
        id=s.id,
        net_id=s.net_id,
        day_of_week=s.day_of_week,
        day_name=DAYS[s.day_of_week],
        start_time=s.start_time,
        timezone=s.timezone,
        notes=s.notes,
        created_at=s.created_at,
    )


@app.get("/nets/{net_id}/schedules", response_model=list[ScheduleOut])
def list_schedules(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_owned_net(net_id, current_user, db)
    schedules = db.query(NetSchedule).filter(NetSchedule.net_id == net_id).order_by(NetSchedule.day_of_week).all()
    return [_schedule_to_out(s) for s in schedules]


@app.post("/nets/{net_id}/schedules", response_model=ScheduleOut, status_code=201)
def create_schedule(net_id: int, data: ScheduleCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_owned_net(net_id, current_user, db)
    sched = NetSchedule(
        net_id=net_id,
        day_of_week=data.day_of_week,
        start_time=data.start_time,
        timezone=data.timezone,
        notes=data.notes,
    )
    db.add(sched)
    db.commit()
    db.refresh(sched)
    return _schedule_to_out(sched)


@app.delete("/schedules/{schedule_id}", status_code=204)
def delete_schedule(schedule_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    sched = db.query(NetSchedule).filter(NetSchedule.id == schedule_id).first()
    if not sched:
        raise HTTPException(404, "Schedule not found")
    _get_owned_net(sched.net_id, current_user, db)
    db.delete(sched)
    db.commit()


@app.get("/nets/{net_id}/upcoming", response_model=list[UpcomingSlot])
def upcoming_slots(
    net_id: int,
    weeks: int = Query(8, ge=1, le=26),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return the next `weeks` scheduled dates across all schedules for a net, with signup info."""
    _get_owned_net(net_id, current_user, db)
    schedules = db.query(NetSchedule).filter(NetSchedule.net_id == net_id).all()

    # Gather all upcoming dates across all schedules
    slots: list[UpcomingSlot] = []
    for sched in schedules:
        for slot_date in _next_occurrences(sched.day_of_week, weeks):
            signup_rows = db.query(NetControlSignup).filter(
                NetControlSignup.schedule_id == sched.id,
                NetControlSignup.slot_date == slot_date,
            ).all()
            slots.append(UpcomingSlot(
                slot_date=slot_date,
                day_name=DAYS[sched.day_of_week],
                schedule_id=sched.id,
                signups=[_signup_to_out(s, current_user) for s in signup_rows],
            ))

    # Sort chronologically
    slots.sort(key=lambda s: s.slot_date)
    return slots


# ---------------------------------------------------------------------------
# DMR Integration
# ---------------------------------------------------------------------------

# In-memory cache for relay-pushed DMR data { net_id: {"entries": [...], "pushed_at": float} }
# This is backed by SystemSetting so it survives server restarts.
_dmr_push_cache: dict = {}

_DMR_CACHE_TTL = 300  # seconds — matches the stale-data check in dmr_cache()


def _dmr_cache_key(net_id: int) -> str:
    return f"dmr_cache_{net_id}"


def _dmr_cache_write(net_id: int, entries: list, db: Session) -> None:
    """Write relay entries to both the in-memory dict and SystemSetting (survives restarts)."""
    now = _time.time()
    _dmr_push_cache[net_id] = {"entries": entries, "pushed_at": now}
    _set_setting(_dmr_cache_key(net_id), json.dumps({"entries": entries, "pushed_at": now}), db)
    db.commit()


def _dmr_cache_read(net_id: int, db: Session) -> Optional[dict]:
    """Return the relay cache for net_id, restoring from DB if the in-memory dict was wiped."""
    cached = _dmr_push_cache.get(net_id)
    if cached:
        return cached
    # Fallback: load from SystemSetting (e.g., after a server restart)
    raw = _get_setting(_dmr_cache_key(net_id), db)
    if raw:
        try:
            data = json.loads(raw)
            _dmr_push_cache[net_id] = data  # repopulate in-memory cache
            return data
        except Exception:
            pass
    return None


def _dmr_normalize_wpsd(entry: dict) -> dict:
    """Normalize a WPSD/Pi-Star last-heard entry to a common dict."""
    slot = str(entry.get("slot", "")).strip()
    return {
        "callsign": str(entry.get("callsign", "")).upper().strip(),
        "dmr_id":   str(entry.get("src", entry.get("id", ""))).strip() or None,
        "name":     entry.get("name") or None,
        "talk_group": str(entry.get("dst", "")).strip() or None,
        "timeslot": f"TS{slot}" if slot else None,
        "region":   entry.get("country") or None,
        "heard_at": entry.get("start") or None,
        "duration": str(entry.get("duration", "")).strip() or None,
    }


def _dmr_normalize_brandmeister(entry: dict) -> dict:
    """Normalize a BrandMeister talkgroup/rx entry to a common dict."""
    slot = entry.get("slot")
    start_ts = entry.get("start")
    stop_ts  = entry.get("stop")
    duration = None
    if start_ts and stop_ts and stop_ts > start_ts:
        duration = f"{stop_ts - start_ts}s"
    heard_at = None
    if start_ts:
        try:
            heard_at = datetime.fromtimestamp(start_ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            pass
    region = entry.get("sourceState") or entry.get("sourceCountry") or None
    return {
        "callsign":   str(entry.get("callsign", "")).upper().strip(),
        "dmr_id":     str(entry.get("SourceID", "")).strip() or None,
        "name":       entry.get("sourceName") or None,
        "talk_group": str(entry.get("DestinationID", "")).strip() or None,
        "timeslot":   f"TS{slot}" if slot else None,
        "region":     region,
        "heard_at":   heard_at,
        "duration":   duration,
    }


def _dmr_fetch_proxy(cfg: DmrConfig) -> list[dict]:
    """Fetch last-heard from hotspot via backend proxy (non-direct mode)."""
    try:
        if cfg.source_type == "brandmeister":
            if not cfg.talkgroup_id:
                return []
            r = httpx.get(
                "https://api.brandmeister.network/v2/talkgroup/rx/",
                params={"talkgroup": cfg.talkgroup_id, "limit": 30},
                timeout=10,
            )
            r.raise_for_status()
            raw = r.json() if isinstance(r.json(), list) else []
            return [_dmr_normalize_brandmeister(e) for e in raw]

        elif cfg.source_type == "pistar":
            if not cfg.hotspot_url:
                return []
            base = cfg.hotspot_url.rstrip("/")
            # Pi-Star endpoint
            url = base if base.endswith("lastheard") else base + "/api/local/lastheard"
            r = httpx.get(url, timeout=10)
            r.raise_for_status()
            raw = r.json() if isinstance(r.json(), list) else []
            return [_dmr_normalize_wpsd(e) for e in raw[:30]]

        else:  # wpsd (default)
            if not cfg.hotspot_url:
                return []
            r = httpx.get(cfg.hotspot_url, params={"limit": 30, "names": "true", "country": "true"}, timeout=10)
            r.raise_for_status()
            raw = r.json() if isinstance(r.json(), list) else []
            return [_dmr_normalize_wpsd(e) for e in raw]

    except httpx.ConnectError as exc:
        raise HTTPException(502, f"Cannot reach hotspot: {exc}. If your hotspot is on a local network, enable direct mode so the browser fetches it instead.")
    except httpx.TimeoutException:
        raise HTTPException(504, "Hotspot request timed out. Check that the URL is correct and the hotspot is online.")
    except Exception as exc:
        _email_log.warning("DMR fetch error: %s", exc)
        raise HTTPException(502, f"DMR fetch failed: {exc}")


def _assert_ham_net(net: Net):
    """Raise 400 if the net is GMRS — DMR is not permitted on GMRS frequencies."""
    if net and net.net_type == "gmrs":
        raise HTTPException(400, "DMR integration is not available for GMRS nets")


@app.get("/nets/{net_id}/dmr/config", response_model=Optional[DmrConfigOut])
def get_dmr_config(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_net_for_user(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    return cfg  # None → null in JSON → frontend shows "not configured"


@app.put("/nets/{net_id}/dmr/config", response_model=DmrConfigOut)
def save_dmr_config(net_id: int, data: DmrConfigCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_owned_net(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    if cfg:
        cfg.source_type     = data.source_type
        cfg.hotspot_url     = data.hotspot_url or None
        cfg.talkgroup_id    = data.talkgroup_id
        cfg.filter_callsign = (data.filter_callsign or "").upper().strip() or None
        cfg.direct_mode     = data.direct_mode
    else:
        cfg = DmrConfig(
            net_id          = net_id,
            source_type     = data.source_type,
            hotspot_url     = data.hotspot_url or None,
            talkgroup_id    = data.talkgroup_id,
            filter_callsign = (data.filter_callsign or "").upper().strip() or None,
            direct_mode     = data.direct_mode,
        )
        db.add(cfg)
    db.commit()
    db.refresh(cfg)
    return cfg


@app.delete("/nets/{net_id}/dmr/config", status_code=204)
def delete_dmr_config(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_owned_net(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    if cfg:
        db.delete(cfg)
        db.commit()


@app.get("/nets/{net_id}/dmr/lastheard", response_model=list[DmrHeardEntry])
def dmr_lastheard(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Backend-proxy last-heard fetch. Only used when direct_mode=False."""
    net = _get_net_for_user(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    if not cfg:
        raise HTTPException(404, "DMR not configured for this net")
    entries = _dmr_fetch_proxy(cfg)

    # Filter out the NCS callsign
    skip = (cfg.filter_callsign or "").upper()
    if skip:
        entries = [e for e in entries if e["callsign"] != skip]

    return entries


class DmrPushPayload(BaseModel):
    entries: list[DmrHeardEntry]


@app.post("/nets/{net_id}/dmr/push", status_code=204)
def dmr_push(
    net_id: int,
    data: DmrPushPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept last-heard data pushed from a local relay script (bypasses CORS entirely)."""
    net = _get_net_for_user(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    if not cfg:
        raise HTTPException(404, "DMR not configured for this net")
    # Filter out NCS callsign server-side too
    skip = (cfg.filter_callsign or "").upper()
    entries = [e.model_dump() for e in data.entries]
    if skip:
        entries = [e for e in entries if (e.get("callsign") or "").upper() != skip]
    _dmr_cache_write(net_id, entries, db)


class DmrRawPushPayload(BaseModel):
    """Raw (un-normalized) last-heard entries from a hotspot API.

    The relay script should send whatever the hotspot returns directly, along with
    the source type so the backend can apply the correct normalizer.  This keeps all
    normalization logic in one place and prevents relay ↔ backend drift.
    """
    source: str = "wpsd"   # wpsd | pistar | brandmeister
    entries: list[dict]


@app.post("/nets/{net_id}/dmr/push/raw", status_code=204)
def dmr_push_raw(
    net_id: int,
    data: DmrRawPushPayload,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Accept raw hotspot JSON from a relay script and normalize server-side.

    Prefer this endpoint over /dmr/push — it keeps normalization logic in the backend
    so relay scripts stay simple fetch-and-forward proxies.
    """
    net = _get_net_for_user(net_id, current_user, db)
    _assert_ham_net(net)
    cfg = db.query(DmrConfig).filter(DmrConfig.net_id == net_id).first()
    if not cfg:
        raise HTTPException(404, "DMR not configured for this net")

    source = data.source.lower()
    if source in ("wpsd", "pistar"):
        entries = [_dmr_normalize_wpsd(e) for e in data.entries]
    elif source == "brandmeister":
        entries = [_dmr_normalize_brandmeister(e) for e in data.entries]
    else:
        raise HTTPException(400, f"Unknown source type '{source}'. Use wpsd, pistar, or brandmeister.")

    # Filter out NCS callsign and any entries with no callsign after normalization
    skip = (cfg.filter_callsign or "").upper()
    entries = [e for e in entries if e.get("callsign")]
    if skip:
        entries = [e for e in entries if e["callsign"].upper() != skip]

    _dmr_cache_write(net_id, entries, db)


@app.get("/nets/{net_id}/dmr/cache")
def dmr_cache(
    net_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Return relay-pushed DMR data with freshness info."""
    net = _get_net_for_user(net_id, current_user, db)
    _assert_ham_net(net)
    cached = _dmr_cache_read(net_id, db)
    if not cached:
        raise HTTPException(404, "No relay data for this net — is the relay script running?")
    age = int(_time.time() - cached["pushed_at"])
    if age > _DMR_CACHE_TTL:
        raise HTTPException(
            404,
            f"Relay data is stale ({age}s old). Is the relay script still running?",
        )
    return {"entries": cached["entries"], "age_seconds": age}


# ---------------------------------------------------------------------------
# Net Control Signups
# ---------------------------------------------------------------------------

@app.post("/nets/{net_id}/signups", response_model=SignupOut, status_code=201)
def create_signup(net_id: int, data: SignupCreate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    net = _get_owned_net(net_id, current_user, db)

    # Verify the schedule belongs to this net
    sched = db.query(NetSchedule).filter(
        NetSchedule.id == data.schedule_id,
        NetSchedule.net_id == net_id,
    ).first()
    if not sched:
        raise HTTPException(404, "Schedule not found for this net")

    # Verify the slot_date is actually a valid occurrence for this schedule
    if data.slot_date.weekday() != sched.day_of_week:
        raise HTTPException(400, f"That date is not a {DAYS[sched.day_of_week]}")

    if data.role in ("broadcaster", "both") and not net.has_broadcast:
        raise HTTPException(400, "This net does not have a broadcaster role enabled")

    # A 'both' signup occupies the date exclusively; net_control/broadcaster only conflict
    # with the same role or an existing 'both' signup.
    existing_roles = {
        r for (r,) in db.query(NetControlSignup.role).filter(
            NetControlSignup.schedule_id == data.schedule_id,
            NetControlSignup.slot_date == data.slot_date,
        ).all()
    }
    conflicting = (
        "both" in existing_roles
        or data.role == "both" and existing_roles
        or data.role in existing_roles
    )
    if conflicting:
        raise HTTPException(409, "That date/role is already claimed")

    # Determine who is being signed up
    if data.assigned_user_id:
        # Net owner assigning a registered operator
        if net.owner_id != current_user.id:
            raise HTTPException(403, "Only the net owner can assign other operators")
        assigned = db.query(User).filter(User.id == data.assigned_user_id, User.is_active == True).first()
        if not assigned:
            raise HTTPException(404, "Assigned user not found")
        signup_user_id = assigned.id
        signup_callsign = assigned.callsign
        signup_name = assigned.name
        signup_email = assigned.email
    else:
        # Self sign-up
        if not data.callsign:
            raise HTTPException(400, "callsign is required for self sign-up")
        signup_user_id = current_user.id
        signup_callsign = data.callsign
        signup_name = data.name
        signup_email = data.email

    signup = NetControlSignup(
        schedule_id=data.schedule_id,
        net_id=net_id,
        slot_date=data.slot_date,
        role=data.role,
        user_id=signup_user_id,
        callsign=signup_callsign,
        name=signup_name,
        email=signup_email,
        notes=data.notes,
    )
    db.add(signup)
    db.commit()
    db.refresh(signup)

    role_label = {
        "net_control": "Net Control",
        "broadcaster": net.broadcast_label or "Broadcaster",
        "both": f"Net Control & {net.broadcast_label or 'Broadcaster'}",
    }[data.role]

    # Send confirmation email with calendar attachment if we have an address
    _email_log.info(
        "Signup created: callsign=%s role=%s email=%r smtp_configured=%s",
        signup_callsign, data.role, signup_email, _smtp_configured(),
    )
    if signup_email:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        day_name = days[sched.day_of_week]
        assigned_by_admin = bool(data.assigned_user_id)
        action = "assigned you as" if assigned_by_admin else "confirmed your sign-up as"
        subject = f"[{net.name}] {role_label} – {signup.slot_date.strftime('%a %b %-d, %Y')}"
        body_html = f"""
<html><body style="font-family:sans-serif;color:#222;max-width:600px">
<h2 style="color:#1a6496">{net.name}</h2>
<p>Hi {signup_name or signup_callsign},</p>
<p>This email {action} <strong>{role_label}</strong> for the following session:</p>
<table style="border-collapse:collapse;margin:16px 0">
  <tr><td style="padding:6px 16px 6px 0;font-weight:bold">Date</td>
      <td style="padding:6px 0">{signup.slot_date.strftime('%A, %B %-d, %Y')}</td></tr>
  <tr><td style="padding:6px 16px 6px 0;font-weight:bold">Time</td>
      <td style="padding:6px 0">{sched.start_time} {sched.timezone}</td></tr>
  {"<tr><td style='padding:6px 16px 6px 0;font-weight:bold'>Frequency</td><td style='padding:6px 0'>" + net.frequency + "</td></tr>" if net.frequency else ""}
  {"<tr><td style='padding:6px 16px 6px 0;font-weight:bold'>Notes</td><td style='padding:6px 0'>" + signup.notes + "</td></tr>" if signup.notes else ""}
</table>
<p>A calendar event is attached — add it to your calendar to set a reminder.</p>
<p style="color:#666;font-size:12px">73 de Ham Net Tracker</p>
</body></html>"""
        body_text = (
            f"{net.name} – {role_label} Confirmation\n\n"
            f"Hi {signup_name or signup_callsign},\n\n"
            f"This email {action} {role_label} for:\n"
            f"  Date:      {signup.slot_date.strftime('%A, %B %-d, %Y')}\n"
            f"  Time:      {sched.start_time} {sched.timezone}\n"
            + (f"  Frequency: {net.frequency}\n" if net.frequency else "")
            + (f"  Notes:     {signup.notes}\n" if signup.notes else "")
            + "\nA calendar event (.ics) is attached.\n\n73 de Ham Net Tracker"
        )
        try:
            ics = _build_ics(net, sched, signup, role_label=role_label)
            send_email(
                to=[signup_email],
                subject=subject,
                body_html=body_html,
                body_text=body_text,
                ics_content=ics,
                ics_filename=f"netcontrol-{signup.slot_date}.ics",
            )
        except Exception as exc:
            _email_log.warning("Failed to send signup confirmation to %s: %s", signup_email, exc)

    return _signup_to_out(signup, current_user)


@app.delete("/signups/{signup_id}", status_code=204)
def delete_signup(signup_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    signup = db.query(NetControlSignup).filter(NetControlSignup.id == signup_id).first()
    if not signup:
        raise HTTPException(404, "Signup not found")
    # Net owner can delete any signup; operators can only delete their own
    net = db.query(Net).filter(Net.id == signup.net_id).first()
    if signup.user_id != current_user.id and (not net or net.owner_id != current_user.id):
        raise HTTPException(403, "Not authorised to remove this signup")
    db.delete(signup)
    db.commit()


@app.get("/nets/{net_id}/signups", response_model=list[SignupOut])
def list_signups(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    _get_owned_net(net_id, current_user, db)
    signups = (
        db.query(NetControlSignup)
        .filter(NetControlSignup.net_id == net_id)
        .order_by(NetControlSignup.slot_date)
        .all()
    )
    return [_signup_to_out(s, current_user) for s in signups]


# ---------------------------------------------------------------------------
# Net share management endpoints
# ---------------------------------------------------------------------------

@app.get("/nets/{net_id}/shares")
def get_net_shares(net_id: int, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Return the current sharing config for a net (owner or admin only)."""
    _get_owned_net(net_id, current_user, db)
    shares = db.query(NetShare).filter(NetShare.net_id == net_id).all()
    return {
        "share_with_all": any(s.user_id is None for s in shares),
        "user_ids": [s.user_id for s in shares if s.user_id is not None],
    }


@app.put("/nets/{net_id}/shares", status_code=204)
def update_net_shares(net_id: int, data: NetShareUpdate, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Replace the sharing config for a net (owner or admin only)."""
    _get_owned_net(net_id, current_user, db)
    # Wipe existing shares for this net
    db.query(NetShare).filter(NetShare.net_id == net_id).delete()
    if data.share_with_all:
        db.add(NetShare(net_id=net_id, user_id=None))
    else:
        for uid in data.user_ids:
            db.add(NetShare(net_id=net_id, user_id=uid))
    db.commit()


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _net_to_out(net: Net, user: User, db: Session) -> NetOut:
    """Build a NetOut with sharing metadata attached."""
    shares = db.query(NetShare).filter(NetShare.net_id == net.id).all()
    owner = db.query(User).filter(User.id == net.owner_id).first()
    out = NetOut.model_validate(net)
    out.is_owner = (net.owner_id == user.id or user.is_admin)
    out.shared_with_all = any(s.user_id is None for s in shares)
    out.shared_user_ids = [s.user_id for s in shares if s.user_id is not None]
    out.owner_callsign = owner.callsign if owner else None
    return out


def _get_owned_net(net_id: int, user: User, db: Session) -> Net:
    """Fetch a net; require owner or admin."""
    net = db.query(Net).filter(Net.id == net_id).first()
    if not net:
        raise HTTPException(404, "Net not found")
    if net.owner_id != user.id and not user.is_admin:
        raise HTTPException(403, "Not your net")
    return net


def _get_net_for_user(net_id: int, user: User, db: Session) -> Net:
    """Fetch a net; allow owner, admin, or user the net is shared with."""
    net = db.query(Net).filter(Net.id == net_id).first()
    if not net:
        raise HTTPException(404, "Net not found")
    if net.owner_id == user.id or user.is_admin:
        return net
    # Check shares: shared with all (user_id IS NULL) or shared with this user
    share = (
        db.query(NetShare)
        .filter(
            NetShare.net_id == net_id,
            or_(NetShare.user_id == user.id, NetShare.user_id == None),
        )
        .first()
    )
    if not share:
        raise HTTPException(403, "Access denied")
    return net


def _get_session_for_user(session_id: int, user: User, db: Session) -> NetSession:
    session = db.query(NetSession).filter(NetSession.id == session_id).first()
    if not session:
        raise HTTPException(404, "Session not found")
    _get_net_for_user(session.net_id, user, db)
    return session
