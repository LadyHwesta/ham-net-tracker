"""
SQLAlchemy models for Ham Radio Net Tracker
"""

from datetime import datetime, timezone
from sqlalchemy import (
    Column, Integer, String, DateTime, Date, Time, ForeignKey, Text, Boolean, UniqueConstraint
)
from sqlalchemy.orm import relationship, declarative_base

Base = declarative_base()


def utcnow():
    return datetime.now(timezone.utc)


class User(Base):
    """Net control operators"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    callsign = Column(String(12), unique=True, nullable=False, index=True)
    name = Column(String(100), nullable=False)
    email = Column(String(255), unique=True, nullable=False, index=True)
    hashed_password = Column(String(255), nullable=False)
    is_active = Column(Boolean, default=False, nullable=False)  # False until admin approves
    is_admin = Column(Boolean, default=False, nullable=False)
    notify_new_registrations = Column(Boolean, default=False, nullable=False)  # email opt-in for new signups
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    nets = relationship("Net", back_populates="owner", cascade="all, delete-orphan")
    sessions = relationship("NetSession", back_populates="operator")

    def __repr__(self):
        return f"<User callsign={self.callsign}>"


class Net(Base):
    """A repeating amateur radio net"""
    __tablename__ = "nets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    frequency = Column(String(20), nullable=True)   # e.g. "146.520 MHz"
    description = Column(Text, nullable=True)
    is_ares = Column(Boolean, default=False, nullable=False)  # ARES/ACES net — enables evac zone tracking
    dmr_talkgroup = Column(String(20), nullable=True)  # Default DMR talk group for check-ins
    owner_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    owner = relationship("User", back_populates="nets")
    sessions = relationship("NetSession", back_populates="net", cascade="all, delete-orphan")
    schedules = relationship("NetSchedule", back_populates="net", cascade="all, delete-orphan")
    evac_zones = relationship("EvacZone", back_populates="net", cascade="all, delete-orphan")
    shares = relationship("NetShare", back_populates="net", cascade="all, delete-orphan")
    dmr_config = relationship("DmrConfig", back_populates="net", uselist=False, cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Net name={self.name}>"


class NetSession(Base):
    """A single activation / running of a net"""
    __tablename__ = "net_sessions"

    id = Column(Integer, primary_key=True, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    operator_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    name = Column(String(200), nullable=True)   # optional human-readable label
    notes = Column(Text, nullable=True)
    started_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    ended_at = Column(DateTime(timezone=True), nullable=True)

    net = relationship("Net", back_populates="sessions")
    operator = relationship("User", back_populates="sessions")
    checkins = relationship("Checkin", back_populates="session", cascade="all, delete-orphan")
    traffic_messages = relationship("TrafficMessage", back_populates="session", cascade="all, delete-orphan")

    @property
    def is_active(self):
        return self.ended_at is None

    def __repr__(self):
        return f"<NetSession id={self.id} net={self.net_id}>"


class Checkin(Base):
    """An individual station checking into a net session"""
    __tablename__ = "checkins"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("net_sessions.id", ondelete="CASCADE"), nullable=False)
    callsign = Column(String(12), nullable=False, index=True)
    name = Column(String(100), nullable=True)
    signal_report = Column(String(20), nullable=True)  # e.g. "59", "57"
    comments = Column(Text, nullable=True)
    has_traffic = Column(Boolean, default=False, nullable=False)
    evac_zone = Column(String(100), nullable=True)   # ARES/ACES evacuation zone
    dmr_talkgroup = Column(String(20), nullable=True)  # DMR talk group, e.g. "3100"
    dmr_region = Column(String(100), nullable=True)    # Region/state/area for DMR nets
    checked_in_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("NetSession", back_populates="checkins")

    __table_args__ = (
        # A callsign can only check in once per session
        UniqueConstraint("session_id", "callsign", name="uq_checkin_session_callsign"),
    )

    def __repr__(self):
        return f"<Checkin callsign={self.callsign} session={self.session_id}>"


class EvacZone(Base):
    """Tracks the most recent evacuation zone reported by each callsign for a given net."""
    __tablename__ = "evac_zones"

    id = Column(Integer, primary_key=True, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    callsign = Column(String(12), nullable=False, index=True)
    zone = Column(String(100), nullable=False)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    net = relationship("Net", back_populates="evac_zones")

    __table_args__ = (
        UniqueConstraint("net_id", "callsign", name="uq_evac_zone_net_callsign"),
    )

    def __repr__(self):
        return f"<EvacZone callsign={self.callsign} zone={self.zone}>"


class TrafficMessage(Base):
    """A formal or informal traffic message handled during a net session."""
    __tablename__ = "traffic_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("net_sessions.id", ondelete="CASCADE"), nullable=False)
    msg_number = Column(String(50), nullable=True)          # e.g. "NTS-001", "ICS-214-7"
    origin_callsign = Column(String(12), nullable=False)
    dest_info = Column(String(200), nullable=True)           # destination callsign or address
    msg_type = Column(String(20), nullable=False, default="formal")   # formal | informal | health_welfare
    status = Column(String(20), nullable=False, default="received")   # received | relayed | delivered | undeliverable
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    session = relationship("NetSession", back_populates="traffic_messages")

    def __repr__(self):
        return f"<TrafficMessage #{self.msg_number} from={self.origin_callsign}>"


class StationRemark(Base):
    """Persistent operator notes about a callsign, scoped to a net."""
    __tablename__ = "station_remarks"

    id = Column(Integer, primary_key=True, index=True)
    callsign = Column(String(12), nullable=False, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    remark = Column(Text, nullable=False)
    updated_by = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("callsign", "net_id", name="uq_station_remark_callsign_net"),
    )

    def __repr__(self):
        return f"<StationRemark callsign={self.callsign} net={self.net_id}>"


class NetShare(Base):
    """Grants a registered user access to a net they don't own.
    user_id=NULL means the net is shared with ALL registered users."""
    __tablename__ = "net_shares"

    id = Column(Integer, primary_key=True, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=True)  # NULL = share with all
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    net = relationship("Net", back_populates="shares")

    __table_args__ = (
        UniqueConstraint("net_id", "user_id", name="uq_net_share_net_user"),
    )

    def __repr__(self):
        target = f"user={self.user_id}" if self.user_id else "ALL"
        return f"<NetShare net={self.net_id} {target}>"


class NetSchedule(Base):
    """Weekly recurring schedule for a net (e.g. every Monday at 19:30)"""
    __tablename__ = "net_schedules"

    id = Column(Integer, primary_key=True, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    # 0=Monday … 6=Sunday  (matches Python datetime.weekday())
    day_of_week = Column(Integer, nullable=False)
    start_time = Column(String(5), nullable=False)   # "HH:MM" in local tz
    timezone = Column(String(60), nullable=False, default="UTC")
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    net = relationship("Net", back_populates="schedules")
    signups = relationship("NetControlSignup", back_populates="schedule", cascade="all, delete-orphan")

    def __repr__(self):
        days = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
        return f"<NetSchedule {days[self.day_of_week]} {self.start_time}>"


class SystemSetting(Base):
    """Key-value store for site-wide configuration such as branding."""
    __tablename__ = "system_settings"

    key = Column(String(100), primary_key=True)
    value = Column(Text, nullable=True)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    def __repr__(self):
        return f"<SystemSetting key={self.key}>"


class NetControlSignup(Base):
    """A logged-in operator claiming net control for a specific upcoming date"""
    __tablename__ = "net_control_signups"

    id = Column(Integer, primary_key=True, index=True)
    schedule_id = Column(Integer, ForeignKey("net_schedules.id", ondelete="CASCADE"), nullable=False)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False)
    slot_date = Column(Date, nullable=False)          # the specific date (YYYY-MM-DD)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    callsign = Column(String(12), nullable=False)
    name = Column(String(100), nullable=True)
    email = Column(String(255), nullable=True)
    notes = Column(Text, nullable=True)
    signed_up_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    schedule = relationship("NetSchedule", back_populates="signups")

    __table_args__ = (
        # Only one signup per schedule date
        UniqueConstraint("schedule_id", "slot_date", name="uq_signup_schedule_date"),
    )

    def __repr__(self):
        return f"<NetControlSignup {self.callsign} on {self.slot_date}>"


class ApiToken(Base):
    """Long-lived tokens for service accounts (e.g. DMR relay scripts)."""
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name = Column(String(100), nullable=False)             # human label, e.g. "DMR Relay - shack Pi"
    token_hash = Column(String(64), nullable=False, unique=True)  # SHA-256 hex of the raw token
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)
    last_used_at = Column(DateTime(timezone=True), nullable=True)

    user = relationship("User")

    def __repr__(self):
        return f"<ApiToken name={self.name} user={self.user_id}>"


class CallsignCache(Base):
    """Local cache of FCC/external callsign lookups to reduce external API dependency."""
    __tablename__ = "callsign_cache"

    callsign = Column(String(12), primary_key=True)
    status = Column(String(10), nullable=False)          # "found" | "not_found"
    name = Column(String(200), nullable=True)
    license_class = Column(String(10), nullable=True)
    state = Column(String(10), nullable=True)
    grid = Column(String(10), nullable=True)
    expires = Column(String(20), nullable=True)
    source = Column(String(50), nullable=True)
    cached_at = Column(DateTime(timezone=True), nullable=False, default=utcnow)

    def __repr__(self):
        return f"<CallsignCache {self.callsign} status={self.status}>"


class DmrConfig(Base):
    """Per-net DMR integration configuration (hotspot or network API)."""
    __tablename__ = "dmr_configs"

    id = Column(Integer, primary_key=True, index=True)
    net_id = Column(Integer, ForeignKey("nets.id", ondelete="CASCADE"), nullable=False, unique=True)
    # wpsd | pistar | brandmeister
    source_type = Column(String(20), nullable=False, default="wpsd")
    # WPSD/Pi-Star: full API URL, e.g. http://wpsd.local/api or http://host/api/local/lastheard
    hotspot_url = Column(Text, nullable=True)
    # BrandMeister: talk group number to monitor
    talkgroup_id = Column(Integer, nullable=True)
    # Callsign to exclude from heard list (usually NCS operator)
    filter_callsign = Column(String(12), nullable=True)
    # True = browser fetches hotspot directly (for local-network hotspots)
    # False = backend proxies the request (for public/accessible URLs)
    direct_mode = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow, nullable=False)

    net = relationship("Net", back_populates="dmr_config")

    def __repr__(self):
        return f"<DmrConfig net={self.net_id} type={self.source_type}>"
