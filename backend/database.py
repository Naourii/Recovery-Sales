"""
database.py
PostgreSQL persistence for AI Sales Recovery (SQLAlchemy 2.x + Alembic).

Contents
  1. Configuration + session management (env vars only, no credentials in code)
  2. Models: Lead, RecoveryAnalysis, RecoveryMessage, MessageApproval,
             CommunicationLog, CRMIntegrationRecord
  3. Lead / CRM / pipeline helpers (reuse the existing agents' dict formats)
  4. PostgresApprovalStore and PostgresSendLog: drop-in replacements for the in-memory
     stores in approval.py and communication_agent.py (pass them via store=...)
  5. CLI + tests:  python database.py check | init | import | test
"""

import re
import sys
import threading
import uuid
import os
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from typing import Iterator, Optional

from sqlalchemy import (
    Boolean, CheckConstraint, Date, DateTime, Float, ForeignKey, Index, Integer, MetaData,
    Numeric, String, Text, UniqueConstraint, create_engine, delete, func, inspect, select,
    text, update,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import URL, Engine, make_url
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.sql import expression

from approval import PENDING, ApprovalStore, DuplicateRequestError
from approval import InvalidInputError as ApprovalInvalidInput
from communication_agent import FAILED, SendLogStore
from communication_agent import InvalidInputError as CommInvalidInput
from strategy_agent import _is_opted_out

# =============================================================================
# 1. Configuration and session management
# =============================================================================
class DatabaseConfigError(Exception):
    pass


def get_database_url() -> URL:
    """DATABASE_URL wins; otherwise build from POSTGRES_* variables."""
    raw = os.environ.get("DATABASE_URL", "").strip()
    if raw:
        url = make_url(raw)
        if url.drivername in ("postgres", "postgresql"):
            url = url.set(drivername="postgresql+psycopg")  # use psycopg 3
        return url

    missing = [n for n in ("POSTGRES_DB", "POSTGRES_USER", "POSTGRES_PASSWORD") if not os.environ.get(n)]
    if missing:
        raise DatabaseConfigError(
            f"Set DATABASE_URL, or these environment variables: {', '.join(missing)} "
            "(optional: POSTGRES_HOST, POSTGRES_PORT)."
        )
    try:
        port = int(os.environ.get("POSTGRES_PORT", "5432"))
    except ValueError:
        raise DatabaseConfigError("POSTGRES_PORT must be an integer.")
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["POSTGRES_USER"],
        password=os.environ["POSTGRES_PASSWORD"],  # URL.create escapes special characters
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=port,
        database=os.environ["POSTGRES_DB"],
    )


def _safe_url() -> str:
    try:
        return get_database_url().render_as_string(hide_password=True)
    except Exception:
        return "(not configured)"


_engine: Optional[Engine] = None
_session_factory: Optional[sessionmaker] = None
_init_lock = threading.Lock()


def get_engine() -> Engine:
    global _engine, _session_factory
    with _init_lock:
        if _engine is None:
            _engine = create_engine(get_database_url(), pool_pre_ping=True)
            _session_factory = sessionmaker(bind=_engine, expire_on_commit=False)
        return _engine


@contextmanager
def session_scope() -> Iterator[Session]:
    """One transaction: commit if the block succeeds, roll back everything if it raises."""
    get_engine()
    session = _session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_session() -> Iterator[Session]:
    """FastAPI dependency for later: Depends(get_session)."""
    with session_scope() as session:
        yield session


def dispose_engine() -> None:
    global _engine, _session_factory
    with _init_lock:
        if _engine is not None:
            _engine.dispose()
        _engine, _session_factory = None, None


def check_connection() -> list[str]:
    """Verify the connection. Returns the names of tables that do not exist yet."""
    engine = get_engine()
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))
    return sorted(set(Base.metadata.tables) - set(inspect(engine).get_table_names()))


def init_db() -> None:
    """Create/upgrade the schema by running Alembic migrations (single source of truth)."""
    from alembic import command
    from alembic.config import Config

    here = Path(__file__).resolve().parent
    if not (here / "alembic.ini").exists():
        raise DatabaseConfigError("alembic.ini not found. Run: alembic init alembic")
    cfg = Config(str(here / "alembic.ini"))
    cfg.set_main_option("script_location", str(here / "alembic"))
    command.upgrade(cfg, "head")


# =============================================================================
# 2. Models
# =============================================================================
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class Lead(TimestampMixin, Base):
    """A lost-deal customer. `id` is the stable internal ID used everywhere in the app."""
    __tablename__ = "leads"
    __table_args__ = (
        CheckConstraint("deal_value >= 0", name="deal_value_nonneg"),
        CheckConstraint("emails_sent >= 0 AND emails_opened >= 0 AND meetings_held >= 0", name="counts_nonneg"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    email: Mapped[str] = mapped_column(String(320), index=True)
    company: Mapped[str] = mapped_column(String(200))
    industry: Mapped[Optional[str]] = mapped_column(String(100))
    role: Mapped[Optional[str]] = mapped_column(String(100))
    plan_interested: Mapped[Optional[str]] = mapped_column(String(50))
    deal_value: Mapped[Decimal] = mapped_column(Numeric(12, 2), server_default="0")
    stage_lost_at: Mapped[Optional[str]] = mapped_column(String(50))
    last_contact_date: Mapped[Optional[date]] = mapped_column(Date)
    emails_sent: Mapped[int] = mapped_column(Integer, server_default="0")
    emails_opened: Mapped[int] = mapped_column(Integer, server_default="0")
    meetings_held: Mapped[int] = mapped_column(Integer, server_default="0")
    crm_notes: Mapped[Optional[str]] = mapped_column(Text)
    opted_out: Mapped[bool] = mapped_column(Boolean, server_default=expression.false())
    opted_out_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    # Synthetic data only: ground truth for measuring agent accuracy. Never passed to agents.
    true_loss_reason: Mapped[Optional[str]] = mapped_column(String(50))

    crm_records: Mapped[list["CRMIntegrationRecord"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", passive_deletes=True)
    analyses: Mapped[list["RecoveryAnalysis"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", passive_deletes=True,
        order_by="RecoveryAnalysis.created_at")
    messages: Mapped[list["RecoveryMessage"]] = relationship(
        back_populates="lead", cascade="all, delete-orphan", passive_deletes=True)

    def to_agent_dict(self) -> dict:
        """Same shape as a record in data/customers.json, so the existing agents work unchanged."""
        if self.last_contact_date is not None:
            days_inactive = max(0, (date.today() - self.last_contact_date).days)
        else:
            days_inactive = 180  # unknown contact date: treat as cold
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "company": self.company,
            "industry": self.industry,
            "role": self.role,
            "plan_interested": self.plan_interested,
            "deal_value": float(self.deal_value or 0),
            "stage_lost_at": self.stage_lost_at,
            "last_contact_date": self.last_contact_date.isoformat() if self.last_contact_date else None,
            "days_inactive": days_inactive,
            "emails_sent": self.emails_sent or 0,
            "emails_opened": self.emails_opened or 0,
            "meetings_held": self.meetings_held or 0,
            "crm_notes": self.crm_notes or "",
            "opted_out": bool(self.opted_out),
        }


class CRMIntegrationRecord(TimestampMixin, Base):
    """Links an internal Lead to its external CRM identity. The unique constraint blocks duplicate imports."""
    __tablename__ = "crm_integration_records"
    __table_args__ = (
        UniqueConstraint("crm_source", "external_id", name="uq_crm_source_external_id"),
        UniqueConstraint("lead_id", "crm_source", name="uq_crm_lead_source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    crm_source: Mapped[str] = mapped_column(String(50))        # e.g. "synthetic", "hubspot"
    external_id: Mapped[str] = mapped_column(String(255))      # the CRM's own ID
    raw_payload: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))  # scrubbed of credentials
    sync_status: Mapped[str] = mapped_column(String(20), server_default="synced")
    last_synced_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))

    lead: Mapped["Lead"] = relationship(back_populates="crm_records")


class RecoveryAnalysis(TimestampMixin, Base):
    """One pipeline run for a lead: analyze_lead + calculate_recovery_score + choose_recovery_strategy."""
    __tablename__ = "recovery_analyses"
    __table_args__ = (
        CheckConstraint("recovery_score >= 0 AND recovery_score <= 100", name="score_range"),
        CheckConstraint("confidence >= 0 AND confidence <= 1", name="confidence_range"),
        CheckConstraint("priority IN ('High', 'Medium', 'Low')", name="priority_valid"),
        Index("ix_recovery_analyses_lead_created", "lead_id", "created_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    # analyze_lead()
    loss_reason: Mapped[str] = mapped_column(String(50))
    confidence: Mapped[float] = mapped_column(Float)
    evidence: Mapped[Optional[list]] = mapped_column(JSONB(none_as_null=True))
    email_open_rate: Mapped[Optional[float]] = mapped_column(Float)
    engagement_level: Mapped[Optional[str]] = mapped_column(String(20))
    urgency: Mapped[Optional[str]] = mapped_column(String(20))
    recommended_angle: Mapped[Optional[str]] = mapped_column(Text)
    summary: Mapped[Optional[str]] = mapped_column(Text)
    # calculate_recovery_score()
    recovery_score: Mapped[Decimal] = mapped_column(Numeric(5, 1))
    priority: Mapped[str] = mapped_column(String(10))
    score_breakdown: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))
    score_explanation: Mapped[Optional[str]] = mapped_column(Text)
    # choose_recovery_strategy()
    strategy: Mapped[str] = mapped_column(String(60))
    strategy_reasoning: Mapped[Optional[str]] = mapped_column(Text)
    recommended_channel: Mapped[Optional[str]] = mapped_column(String(20))
    strategy_priority: Mapped[Optional[str]] = mapped_column(String(10))
    next_action: Mapped[Optional[str]] = mapped_column(Text)
    strategy_warnings: Mapped[Optional[list]] = mapped_column(JSONB(none_as_null=True))

    lead: Mapped["Lead"] = relationship(back_populates="analyses")


class RecoveryMessage(TimestampMixin, Base):
    """A generated draft. `id` is the stable message_id used for approval and duplicate-send prevention."""
    __tablename__ = "recovery_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=lambda: str(uuid.uuid4()))
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    analysis_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recovery_analyses.id", ondelete="SET NULL"))
    strategy: Mapped[Optional[str]] = mapped_column(String(60))
    channel: Mapped[Optional[str]] = mapped_column(String(20))
    original_content: Mapped[str] = mapped_column(Text)  # exactly what the generator produced

    lead: Mapped["Lead"] = relationship(back_populates="messages")
    analysis: Mapped[Optional["RecoveryAnalysis"]] = relationship()
    approval: Mapped[Optional["MessageApproval"]] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan", passive_deletes=True)
    communication: Mapped[Optional["CommunicationLog"]] = relationship(
        back_populates="message", uselist=False, cascade="all, delete-orphan", passive_deletes=True)


class MessageApproval(TimestampMixin, Base):
    """Human review state for one message (1:1 with RecoveryMessage)."""
    __tablename__ = "message_approvals"
    __table_args__ = (
        CheckConstraint("status IN ('pending', 'approved', 'rejected')", name="status_valid"),
        CheckConstraint("status = 'pending' OR (reviewed_at IS NOT NULL AND reviewer IS NOT NULL)",
                        name="review_fields_set"),
        # At most one pending request per lead, enforced by the database itself.
        Index("uq_approval_one_pending_per_lead", "lead_id", unique=True,
              postgresql_where=text("status = 'pending'")),
    )

    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recovery_messages.id", ondelete="CASCADE"), primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"))
    status: Mapped[str] = mapped_column(String(20), server_default=PENDING)
    message_content: Mapped[str] = mapped_column(Text)  # current text (may be edited by a human)
    reviewer: Mapped[Optional[str]] = mapped_column(String(100))
    reviewed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    edited_by: Mapped[Optional[str]] = mapped_column(String(100))
    edited_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[Optional[str]] = mapped_column(Text)

    message: Mapped["RecoveryMessage"] = relationship(back_populates="approval")


class CommunicationLog(TimestampMixin, Base):
    """Send state per message. The primary key on message_id is the duplicate-send guard."""
    __tablename__ = "communication_logs"
    __table_args__ = (
        CheckConstraint("status IN ('sending', 'sent', 'failed')", name="status_valid"),
        CheckConstraint("(status = 'sent') = (sent_at IS NOT NULL)", name="sent_at_matches_status"),
    )

    message_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("recovery_messages.id", ondelete="CASCADE"), primary_key=True)
    lead_id: Mapped[int] = mapped_column(ForeignKey("leads.id", ondelete="CASCADE"), index=True)
    channel: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    provider: Mapped[Optional[str]] = mapped_column(String(50))
    recipient: Mapped[Optional[str]] = mapped_column(String(320))
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True))
    provider_response: Mapped[Optional[dict]] = mapped_column(JSONB(none_as_null=True))  # scrubbed
    error: Mapped[Optional[str]] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, server_default="1")

    message: Mapped["RecoveryMessage"] = relationship(back_populates="communication")


# =============================================================================
# 3. Lead / CRM / pipeline helpers
# =============================================================================
_SECRET_KEY = re.compile(r"(api[-_ ]?key|secret|token|password|passwd|authorization|credential|bearer)", re.I)


def _scrub(value):
    """Recursively redact credential-like keys so secrets never reach the database."""
    if isinstance(value, dict):
        return {k: ("[REDACTED]" if _SECRET_KEY.search(str(k)) else _scrub(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    return value


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _dt(value):
    """ISO string / datetime / None -> datetime / None."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _iso(value):
    return None if value is None else value.astimezone(timezone.utc).isoformat()


def _parse_date(value) -> Optional[date]:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return date.fromisoformat(value.strip()[:10])
        except ValueError:
            return None
    return None


_LEAD_FIELDS = ("name", "email", "company", "industry", "role", "plan_interested", "deal_value",
                "stage_lost_at", "emails_sent", "emails_opened", "meetings_held", "crm_notes")


def _lead_values(data: dict) -> dict:
    """Map a customer dict (customers.json format) to Lead columns. None values are skipped."""
    values = {k: data[k] for k in _LEAD_FIELDS if data.get(k) is not None}
    last = _parse_date(data.get("last_contact_date"))
    if last is None and data.get("days_inactive") is not None:
        try:
            last = date.today() - timedelta(days=int(data["days_inactive"]))
        except (TypeError, ValueError):
            pass
    if last is not None:
        values["last_contact_date"] = last
    if data.get("_true_loss_reason"):
        values["true_loss_reason"] = data["_true_loss_reason"]
    return values


def _find_crm_record(session: Session, source: str, external_id: str) -> Optional[CRMIntegrationRecord]:
    return session.scalar(select(CRMIntegrationRecord).where(
        CRMIntegrationRecord.crm_source == source, CRMIntegrationRecord.external_id == external_id))


def _apply_update(session: Session, record: CRMIntegrationRecord, data: dict, raw_payload) -> Lead:
    lead = record.lead
    for key, value in _lead_values(data).items():
        setattr(lead, key, value)
    # Opt-out is sticky: an import can set it, but never clears it.
    if "opted_out" in data and _is_opted_out(data, []) and not lead.opted_out:
        lead.opted_out, lead.opted_out_at = True, _utcnow()
    record.sync_status, record.last_synced_at = "synced", _utcnow()
    if raw_payload is not None:
        record.raw_payload = _scrub(raw_payload)
    session.flush()
    return lead


def upsert_lead_from_crm(session: Session, crm_source: str, external_id, data: dict,
                         raw_payload: Optional[dict] = None) -> tuple[Lead, bool]:
    """
    Create the lead if (crm_source, external_id) is new, otherwise update it.
    Returns (lead, created). Safe to call repeatedly and concurrently: the unique
    constraint decides, so duplicate imports are impossible.
    """
    source, ext = str(crm_source).strip(), str(external_id).strip()
    if not source or not ext:
        raise ValueError("crm_source and external_id are required.")

    existing = _find_crm_record(session, source, ext)
    if existing:
        return _apply_update(session, existing, data, raw_payload), False

    values = _lead_values(data)
    missing = [f for f in ("name", "email", "company") if not str(values.get(f) or "").strip()]
    if missing:
        raise ValueError(f"Lead data is missing required field(s): {', '.join(missing)}")

    opted_out = "opted_out" in data and _is_opted_out(data, [])
    try:
        with session.begin_nested():  # savepoint: a duplicate does not poison the outer transaction
            lead = Lead(**values, opted_out=opted_out, opted_out_at=_utcnow() if opted_out else None)
            session.add(lead)
            session.flush()
            session.add(CRMIntegrationRecord(
                lead_id=lead.id, crm_source=source, external_id=ext,
                raw_payload=_scrub(raw_payload) if raw_payload is not None else None,
                sync_status="synced", last_synced_at=_utcnow()))
            session.flush()
        return lead, True
    except IntegrityError:
        existing = _find_crm_record(session, source, ext)  # lost a race with a concurrent import
        if existing is None:
            raise
        return _apply_update(session, existing, data, raw_payload), False


def import_customers(session: Session, customers: list[dict], crm_source: str = "synthetic") -> dict:
    """Import customers.json records. All-or-nothing when called inside session_scope()."""
    created = updated = 0
    for c in customers:
        raw = {k: v for k, v in c.items() if not k.startswith("_")}
        _, was_created = upsert_lead_from_crm(session, crm_source, f"SYN-{c['id']}", c, raw_payload=raw)
        created += was_created
        updated += not was_created
    return {"created": created, "updated": updated}


def set_opted_out(session: Session, lead_id: int, opted_out: bool = True) -> None:
    lead = session.get(Lead, lead_id)
    if lead is None:
        raise LookupError(f"Lead {lead_id} not found.")
    lead.opted_out = opted_out
    lead.opted_out_at = _utcnow() if opted_out else None


def get_agent_customer(session: Session, lead_id: int) -> Optional[dict]:
    lead = session.get(Lead, lead_id)
    return lead.to_agent_dict() if lead else None


def db_customer_lookup(customer_id) -> Optional[dict]:
    """Pass as customer_lookup= to approve_message() so opt-out is checked against the database."""
    if not isinstance(customer_id, int):
        return None
    with session_scope() as session:
        return get_agent_customer(session, customer_id)


def save_pipeline_result(session: Session, lead_id: int, analysis: dict, score: dict,
                         strategy: dict) -> RecoveryAnalysis:
    """Persist the outputs of analyze_lead, calculate_recovery_score, choose_recovery_strategy."""
    if session.get(Lead, lead_id) is None:
        raise LookupError(f"Lead {lead_id} not found.")
    row = RecoveryAnalysis(
        lead_id=lead_id,
        loss_reason=analysis["loss_reason"], confidence=analysis["confidence"],
        evidence=analysis.get("evidence"), email_open_rate=analysis.get("email_open_rate"),
        engagement_level=analysis.get("engagement_level"), urgency=analysis.get("urgency"),
        recommended_angle=analysis.get("recommended_angle"), summary=analysis.get("summary"),
        recovery_score=score["recovery_score"], priority=score["priority"],
        score_breakdown=score.get("breakdown"), score_explanation=score.get("explanation"),
        strategy=strategy["strategy"], strategy_reasoning=strategy.get("reasoning"),
        recommended_channel=strategy.get("recommended_channel"), strategy_priority=strategy.get("priority"),
        next_action=strategy.get("next_action"), strategy_warnings=strategy.get("warnings"),
    )
    session.add(row)
    session.flush()
    return row


def get_latest_analysis(session: Session, lead_id: int) -> Optional[RecoveryAnalysis]:
    return session.scalar(select(RecoveryAnalysis).where(RecoveryAnalysis.lead_id == lead_id)
                          .order_by(RecoveryAnalysis.created_at.desc(), RecoveryAnalysis.id.desc()).limit(1))


# =============================================================================
# 4. Store adapters (implement the existing abstract interfaces)
# =============================================================================
def _constraint_name(exc: IntegrityError) -> str:
    return getattr(getattr(exc.orig, "diag", None), "constraint_name", None) or ""


_APPROVAL_COLUMNS = (
    MessageApproval.message_id, MessageApproval.lead_id, MessageApproval.message_content,
    MessageApproval.status, MessageApproval.created_at, MessageApproval.reviewed_at,
    MessageApproval.reviewer, MessageApproval.edited_at, MessageApproval.edited_by,
    MessageApproval.review_note, RecoveryMessage.original_content, RecoveryMessage.strategy,
    RecoveryMessage.channel,
)


def _approval_select():
    return select(*_APPROVAL_COLUMNS).join(RecoveryMessage, RecoveryMessage.id == MessageApproval.message_id)


def _approval_record(row) -> dict:
    """Row -> the dict format approval.py already uses."""
    return {
        "message_id": row.message_id, "customer_id": row.lead_id, "message_content": row.message_content,
        "status": row.status, "created_at": _iso(row.created_at), "reviewed_at": _iso(row.reviewed_at),
        "reviewer": row.reviewer, "original_message": row.original_content, "strategy": row.strategy,
        "channel": row.channel, "edited_at": _iso(row.edited_at), "edited_by": row.edited_by,
        "review_note": row.review_note,
    }


class PostgresApprovalStore(ApprovalStore):
    _CHANGEABLE = {"message_content", "status", "reviewer", "reviewed_at", "edited_at", "edited_by", "review_note"}

    def add(self, record: dict) -> None:
        try:
            with session_scope() as s:  # message + approval are written together or not at all
                s.add(RecoveryMessage(
                    id=record["message_id"], lead_id=record["customer_id"],
                    analysis_id=s.scalar(select(RecoveryAnalysis.id)
                                         .where(RecoveryAnalysis.lead_id == record["customer_id"])
                                         .order_by(RecoveryAnalysis.created_at.desc(), RecoveryAnalysis.id.desc())
                                         .limit(1)),  # latest analysis for this lead
                    strategy=record.get("strategy"), channel=record.get("channel"),
                    original_content=record.get("original_message") or record["message_content"]))
                s.flush()
                s.add(MessageApproval(
                    message_id=record["message_id"], lead_id=record["customer_id"], status=record["status"],
                    message_content=record["message_content"], created_at=_dt(record["created_at"])))
        except IntegrityError as exc:
            name = _constraint_name(exc)
            if name == "uq_approval_one_pending_per_lead":
                raise DuplicateRequestError(
                    f"Customer {record['customer_id']} already has a pending approval request. "
                    "Resolve it before creating another.") from exc
            if name.startswith("pk_"):
                raise DuplicateRequestError(f"Message id {record['message_id']} already exists.") from exc
            if name.startswith("fk_"):
                raise ApprovalInvalidInput(f"Lead {record['customer_id']} does not exist in the database.") from exc
            raise

    def get(self, message_id: str) -> Optional[dict]:
        with session_scope() as s:
            row = s.execute(_approval_select().where(MessageApproval.message_id == message_id)).first()
            return _approval_record(row) if row else None

    def list_by_status(self, status: str) -> list[dict]:
        with session_scope() as s:
            rows = s.execute(_approval_select().where(MessageApproval.status == status)).all()
            return [_approval_record(r) for r in rows]

    def update_if_pending(self, message_id: str, changes: dict) -> Optional[dict]:
        unknown = set(changes) - self._CHANGEABLE
        if unknown:
            raise ValueError(f"Unknown approval field(s): {sorted(unknown)}")
        values = {k: (_dt(v) if k in ("reviewed_at", "edited_at") else v) for k, v in changes.items()}
        with session_scope() as s:
            # Single atomic UPDATE ... WHERE status = 'pending': two reviewers can never both win.
            result = s.execute(
                update(MessageApproval)
                .where(MessageApproval.message_id == message_id, MessageApproval.status == PENDING)
                .values(**values).execution_options(synchronize_session=False))
            if result.rowcount == 0:
                return None
            row = s.execute(_approval_select().where(MessageApproval.message_id == message_id)).first()
            return _approval_record(row)


def _log_record(log: CommunicationLog) -> dict:
    """CommunicationLog -> the dict format communication_agent.py already uses."""
    return {
        "message_id": log.message_id, "customer_id": log.lead_id, "channel": log.channel,
        "status": log.status, "sent_at": _iso(log.sent_at), "provider_response": log.provider_response,
        "provider": log.provider, "recipient": log.recipient, "error": log.error,
        "started_at": _iso(log.started_at), "attempts": log.attempts,
    }


class PostgresSendLog(SendLogStore):
    def claim(self, record: dict) -> Optional[dict]:
        stmt = pg_insert(CommunicationLog).values(
            message_id=record["message_id"], lead_id=record["customer_id"], channel=record["channel"],
            status=record["status"], provider=record.get("provider"), recipient=record.get("recipient"),
            started_at=_dt(record.get("started_at")), attempts=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=[CommunicationLog.message_id],
            set_={"status": stmt.excluded.status, "attempts": CommunicationLog.attempts + 1, "error": None,
                  "started_at": stmt.excluded.started_at, "provider": stmt.excluded.provider,
                  "recipient": stmt.excluded.recipient, "updated_at": func.now()},
            where=(CommunicationLog.status == FAILED),  # only a failed send may be re-claimed
        ).returning(CommunicationLog.message_id)
        try:
            with session_scope() as s:
                if s.execute(stmt).first() is not None:
                    return None  # claimed
                existing = s.get(CommunicationLog, record["message_id"])
                if existing is None:
                    raise RuntimeError("Send log changed concurrently; retry.")
                return _log_record(existing)
        except IntegrityError as exc:
            if _constraint_name(exc).startswith("fk_"):
                raise CommInvalidInput(
                    f"Message {record['message_id']} is not stored in the database; "
                    "create the approval request through PostgresApprovalStore first.") from exc
            raise

    def finalize(self, message_id: str, changes: dict) -> dict:
        allowed = {"status", "error", "sent_at", "provider_response"}
        unknown = set(changes) - allowed
        if unknown:
            raise ValueError(f"Unknown send-log field(s): {sorted(unknown)}")
        values = {}
        for key, value in changes.items():
            values[key] = _dt(value) if key == "sent_at" else _scrub(value) if key == "provider_response" else value
        with session_scope() as s:
            result = s.execute(update(CommunicationLog).where(CommunicationLog.message_id == message_id)
                               .values(**values).execution_options(synchronize_session=False))
            if result.rowcount == 0:
                raise KeyError(message_id)
            return _log_record(s.get(CommunicationLog, message_id))

    def get(self, message_id: str) -> Optional[dict]:
        with session_scope() as s:
            log = s.get(CommunicationLog, message_id)
            return _log_record(log) if log else None

    def list_all(self) -> list[dict]:
        with session_scope() as s:
            return [_log_record(r) for r in s.scalars(select(CommunicationLog).order_by(CommunicationLog.started_at))]


# =============================================================================
# 5. CLI and tests
# =============================================================================
def _cmd_check() -> None:
    missing = check_connection()
    print(f"Connected: {_safe_url()}")
    if missing:
        print(f"Missing tables: {', '.join(missing)}\nRun: python database.py init")
    else:
        print(f"Schema OK: all {len(Base.metadata.tables)} tables present.")


def _cmd_init() -> None:
    init_db()
    print("Migrations applied.")
    _cmd_check()


def _cmd_import() -> None:
    from lead_analysis import load_customers
    customers = load_customers()
    with session_scope() as s:
        stats = import_customers(s, customers)
    print(f"Imported {len(customers)} synthetic customers: {stats['created']} created, {stats['updated']} already existed (updated).")


def _ok(label: str, condition: bool) -> None:
    assert condition, f"FAILED: {label}"
    print(f"  PASS {label}")


def _expect(exc_types, label: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_types as exc:
        message = getattr(exc, "message", None) or str(exc)
        print(f"  PASS {label}\n       -> {type(exc).__name__}: {message.splitlines()[0]}")
        return
    raise AssertionError(f"{label}: expected {exc_types} but no error was raised")


def _test_lead_data() -> dict:
    # Chosen so the real agents produce an email-channel strategy (price concern, High priority).
    return {
        "name": "Test Lead", "email": "test.lead@example.com", "company": "Test Co",
        "industry": "Technology", "role": "CTO", "plan_interested": "Professional",
        "deal_value": 10000, "stage_lost_at": "Proposal Sent",
        "last_contact_date": (date.today() - timedelta(days=20)).isoformat(),
        "emails_sent": 10, "emails_opened": 6, "meetings_held": 3,
        "crm_notes": "Asked for a 30% discount, we could not match it. Went quiet afterward.",
    }


def run_tests() -> None:
    from approval import (InvalidStatusError, approve_message, create_approval_request, edit_pending_message,
                          get_pending_approvals, reject_message)
    from approval import OptedOutError as ApprovalOptedOut
    from communication_agent import DuplicateSendError, MockCommunicationProvider, send_recovery_message
    from lead_analysis import analyze_lead
    from message_generator import generate_recovery_message
    from recovery_scoring import calculate_recovery_score
    from strategy_agent import choose_recovery_strategy

    missing = check_connection()
    if missing:
        sys.exit(f"Tables missing ({', '.join(missing)}). Run: python database.py init")

    external_id = f"TEST-{uuid.uuid4().hex[:8]}"
    lead_id = None
    message_ids: list[str] = []
    try:
        print("TEST 1: create and retrieve a lead")
        with session_scope() as s:
            lead, created = upsert_lead_from_crm(
                s, "test", external_id, _test_lead_data(),
                raw_payload={"name": "Test Lead", "api_key": "super-secret-value"})
            lead_id = lead.id
        _ok("lead created with an internal integer id", created and isinstance(lead_id, int))
        with session_scope() as s:
            fetched = s.get(Lead, lead_id)
            by_crm = _find_crm_record(s, "test", external_id)
            agent_dict = fetched.to_agent_dict()
            _ok("retrieved by internal id", fetched.name == "Test Lead" and fetched.company == "Test Co")
            _ok("retrieved by external CRM id", by_crm is not None and by_crm.lead_id == lead_id)
            _ok("agent dict has the customers.json shape (days_inactive computed = 20)",
                agent_dict["days_inactive"] == 20 and agent_dict["deal_value"] == 10000.0
                and agent_dict["opted_out"] is False)
            _ok("credential-like keys are redacted in raw_payload",
                by_crm.raw_payload == {"name": "Test Lead", "api_key": "[REDACTED]"})
        print(f"  lead id={lead_id}, external id={external_id}")

        print("\nTEST 2: duplicate import is prevented")
        with session_scope() as s:
            lead2, created2 = upsert_lead_from_crm(s, "test", external_id, {**_test_lead_data(), "company": "Test Co Renamed"})
            _ok("second import updates instead of inserting", (not created2) and lead2.id == lead_id)
        with session_scope() as s:
            count = s.scalar(select(func.count()).select_from(CRMIntegrationRecord)
                             .where(CRMIntegrationRecord.crm_source == "test", CRMIntegrationRecord.external_id == external_id))
            _ok("exactly one CRM record exists", count == 1)
            _ok("lead data was updated", s.get(Lead, lead_id).company == "Test Co Renamed")

            def _raw_duplicate():
                with s.begin_nested():
                    s.add(CRMIntegrationRecord(lead_id=lead_id, crm_source="test", external_id=external_id))
                    s.flush()
            _expect(IntegrityError, "database itself rejects a duplicate (crm_source, external_id)", _raw_duplicate)

            upsert_lead_from_crm(s, "test", external_id, {"opted_out": True})
            upsert_lead_from_crm(s, "test", external_id, {"opted_out": False})
            _ok("opt-out is sticky: re-import cannot clear it", s.get(Lead, lead_id).opted_out is True)
            set_opted_out(s, lead_id, False)

        print("\nTEST 3: persist recovery analysis, score and strategy (real agents)")
        with session_scope() as s:
            customer = get_agent_customer(s, lead_id)
        analysis = analyze_lead(customer)
        score = calculate_recovery_score(customer, analysis)
        strategy = choose_recovery_strategy(customer, analysis, score)
        with session_scope() as s:
            save_pipeline_result(s, lead_id, analysis, score, strategy)

            def _bad_score():
                with s.begin_nested():
                    s.add(RecoveryAnalysis(lead_id=lead_id, loss_reason="price_too_high", confidence=0.5,
                                           recovery_score=150, priority="High", strategy="Friendly follow-up"))
                    s.flush()
            _expect(IntegrityError, "database rejects a recovery score of 150", _bad_score)
        with session_scope() as s:
            latest = get_latest_analysis(s, lead_id)
            _ok("analysis round-trips", latest.loss_reason == analysis["loss_reason"] and latest.strategy == strategy["strategy"])
            _ok("score and explanation stored", float(latest.recovery_score) == score["recovery_score"] and bool(latest.score_explanation))
        print(f"  {strategy['strategy']} | {latest.loss_reason} | score {latest.recovery_score} [{latest.priority}] | channel {strategy['recommended_channel']}")

        if strategy["strategy"] == "No contact" or strategy["recommended_channel"] != "email":
            sys.exit("Test lead did not produce an email strategy; adjust _test_lead_data().")

        print("\nTEST 4: approval workflow persisted in PostgreSQL")
        approvals, sendlog = PostgresApprovalStore(), PostgresSendLog()
        gen = generate_recovery_message(customer, analysis, score, strategy, mode="mock")
        req = create_approval_request(customer, gen, store=approvals)
        message_ids.append(req["message_id"])
        mid = req["message_id"]
        _ok("created as 'pending'", req["status"] == "pending" and req["customer_id"] == lead_id)
        _ok("listed in get_pending_approvals()", mid in [r["message_id"] for r in get_pending_approvals(store=approvals)])
        _expect(DuplicateRequestError, "second pending request for the same lead is blocked (partial unique index)",
                create_approval_request, customer, gen, store=approvals)
        edited = edit_pending_message(mid, "Hi, just checking in. Happy to talk whenever suits you.", "alice", store=approvals)
        _ok("edit persisted, original preserved", edited["message_content"].startswith("Hi, just") and edited["original_message"] == gen["message"])
        approved = approve_message(mid, "alice", customer_lookup=db_customer_lookup, store=approvals)
        _ok("approved with reviewer and reviewed_at", approved["status"] == "approved" and approved["reviewer"] == "alice" and approved["reviewed_at"])
        _expect(InvalidStatusError, "approving again is blocked", approve_message, mid, "bob", customer_lookup=db_customer_lookup, store=approvals)

        print("\nTEST 5: communication log persisted in PostgreSQL (mock provider, nothing really sent)")
        provider = MockCommunicationProvider()
        sent = send_recovery_message(approved, db_customer_lookup(lead_id), provider=provider, store=sendlog)
        _ok("send recorded as 'sent'", sent["status"] == "sent" and sendlog.get(mid)["sent_at"] is not None)
        _ok("provider_response stored", sendlog.get(mid)["provider_response"]["simulated"] is True)
        _expect(DuplicateSendError, "duplicate send blocked by the database", send_recovery_message,
                approved, db_customer_lookup(lead_id), provider=provider, store=sendlog)
        _ok("provider delivered exactly once", len(provider.outbox) == 1)

        print("\nTEST 6: approval cannot bypass opt-out (checked against the database)")
        req2 = create_approval_request(customer, generate_recovery_message(customer, analysis, score, strategy, mode="mock"), store=approvals)
        message_ids.append(req2["message_id"])
        with session_scope() as s:
            set_opted_out(s, lead_id, True)
        _expect(ApprovalOptedOut, "approval blocked after the lead opted out", approve_message,
                req2["message_id"], "alice", customer_lookup=db_customer_lookup, store=approvals)
        _ok("message stays pending", approvals.get(req2["message_id"])["status"] == "pending")
        _ok("rejecting is still allowed", reject_message(req2["message_id"], "alice", "Customer opted out", store=approvals)["status"] == "rejected")

        print("\nAll database tests passed.")
    finally:
        if lead_id is not None:
            with session_scope() as s:
                s.execute(delete(Lead).where(Lead.id == lead_id))  # DB cascade removes everything linked
            with session_scope() as s:
                gone = all(s.get(RecoveryMessage, m) is None for m in message_ids)
            print(f"Test data removed (cascade delete verified: {gone}).")


def main() -> None:
    commands = {"check": _cmd_check, "init": _cmd_init, "import": _cmd_import, "test": run_tests}
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd not in commands:
        sys.exit("Usage: python database.py [check | init | import | test]")
    try:
        commands[cmd]()
    except DatabaseConfigError as exc:
        sys.exit(f"Configuration error: {exc}")
    except OperationalError as exc:
        detail = str(exc.orig).strip().splitlines()[0] if exc.orig else "unknown error"
        sys.exit(f"Cannot connect to PostgreSQL. Is it running, and are the credentials right?\n"
                 f"Target: {_safe_url()}\nDetails: {detail}")


if __name__ == "__main__":
    main()