"""
main.py
REST API for AI Sales Recovery (FastAPI).

Every endpoint is a thin layer over the existing modules; no business logic is duplicated:
  lead_analysis, recovery_scoring, strategy_agent -> analysis pipeline
  message_generator                               -> draft messages
  approval (+ PostgresApprovalStore)              -> human review
  communication_agent (+ PostgresSendLog)         -> guarded sending
  database                                        -> persistence

Safety rules enforced here and in the underlying modules:
  - nothing is sent unless its stored approval status is exactly "approved"
  - opted-out customers are blocked at generation, approval and sending
  - duplicate sends are blocked by the database (message_id primary key)
  - communication is MOCK only; real delivery does not exist yet
  - "recovered revenue" is never estimated; simulated sends are labelled as such
"""

import hmac
import logging
import os
from contextlib import asynccontextmanager
from datetime import date, datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Depends, FastAPI, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError, SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from approval import (
    ApprovalError, approve_message, create_approval_request, edit_pending_message,
    get_approval, get_pending_approvals, reject_message,
)
from communication_agent import (
    CommunicationError, MockCommunicationProvider, get_send_record, send_recovery_message,
)
from database import (
    CommunicationLog, CRMIntegrationRecord, DatabaseConfigError, Lead, MessageApproval,
    PostgresApprovalStore, PostgresSendLog, RecoveryAnalysis, RecoveryMessage, check_connection,
    db_customer_lookup, dispose_engine, get_agent_customer, get_latest_analysis, import_customers,
    save_pipeline_result, session_scope,
)
from lead_analysis import analyze_lead, load_customers
from message_generator import generate_recovery_message
from recovery_scoring import calculate_recovery_score
from strategy_agent import choose_recovery_strategy

logger = logging.getLogger("sales_recovery.api")

# =============================================================================
# Configuration and shared singletons
# =============================================================================
SIMULATED_PROVIDERS = {"mock"}
CRM_STATUS_SIMULATED = "outreach_simulated"  # <= 20 chars (column limit)
CRM_STATUS_SENT = "outreach_sent"


def build_provider() -> MockCommunicationProvider:
    """Fail closed: only the mock provider exists, so real delivery cannot be enabled by accident."""
    name = os.environ.get("COMMUNICATION_PROVIDER", "mock").strip().lower()
    if name != "mock":
        raise RuntimeError(
            f"COMMUNICATION_PROVIDER={name!r} is not implemented. Only 'mock' exists, "
            "so the API refuses to start rather than risk real delivery."
        )
    return MockCommunicationProvider()


provider = build_provider()
approvals = PostgresApprovalStore()
sendlog = PostgresSendLog()


# =============================================================================
# Errors: one consistent envelope {"error": {"code", "message", "details"?}}
# =============================================================================
class ApiError(Exception):
    def __init__(self, status_code: int, code: str, message: str, details: Any = None):
        super().__init__(message)
        self.status_code, self.code, self.message, self.details = status_code, code, message, details


# Maps the .code of ApprovalError / CommunicationError subclasses to HTTP statuses.
ERROR_STATUS = {
    "invalid_input": 422, "not_found": 404, "customer_not_found": 404,
    "invalid_status": 409, "duplicate_request": 409, "not_approved": 409, "duplicate_send": 409,
    "customer_opted_out": 403, "unsupported_channel": 422, "invalid_recipient": 422,
}

# generate_recovery_message() failure statuses -> (HTTP status, error code)
GENERATION_FAILURES = {
    "failed_invalid_input": (422, "generation_invalid_input"),
    "failed_config": (503, "generator_misconfigured"),
    "failed_api": (502, "generator_upstream_error"),
    "failed_guardrail": (502, "generator_guardrail_rejected"),
}


def _error_response(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    body: dict[str, Any] = {"error": {"code": code, "message": message}}
    if details is not None:
        body["error"]["details"] = details
    return JSONResponse(status_code=status, content=jsonable_encoder(body))


# =============================================================================
# Pydantic models
# =============================================================================
class ErrorBody(BaseModel):
    code: str
    message: str
    details: Any | None = None


class ErrorResponse(BaseModel):
    error: ErrorBody


def _errs(*codes: int) -> dict:
    return {c: {"model": ErrorResponse} for c in codes}


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable", "not_configured"]
    schema_ready: bool
    missing_tables: list[str] = []
    communication_provider: str
    message_generator_mode: str
    checked_at: datetime


class CRMRecordOut(BaseModel):
    crm_source: str
    external_id: str
    sync_status: str
    last_synced_at: datetime | None = None


class AnalysisOut(BaseModel):
    id: int
    lead_id: int
    created_at: datetime | None = None
    loss_reason: str
    confidence: float
    evidence: list[str] = []
    email_open_rate: float | None = None
    engagement_level: str | None = None
    urgency: str | None = None
    summary: str | None = None
    recommended_angle: str | None = None
    recovery_score: float
    priority: str
    score_breakdown: dict[str, Any] | None = None
    score_explanation: str | None = None
    strategy: str
    strategy_reasoning: str | None = None
    recommended_channel: str | None = None
    strategy_priority: str | None = None
    next_action: str | None = None
    strategy_warnings: list[str] = []


class LeadSummary(BaseModel):
    id: int
    name: str
    email: str
    company: str
    industry: str | None = None
    role: str | None = None
    plan_interested: str | None = None
    deal_value: float
    stage_lost_at: str | None = None
    last_contact_date: date | None = None
    days_inactive: int
    opted_out: bool
    recovery_score: float | None = None
    priority: str | None = None
    strategy: str | None = None
    last_analyzed_at: datetime | None = None
    crm_notes: str | None = None  # Edit A


class LeadMessageRef(BaseModel):
    message_id: str
    strategy: str | None = None
    channel: str | None = None
    approval_status: str | None = None
    send_status: str | None = None
    created_at: datetime | None = None


class LeadDetail(LeadSummary):
    crm_records: list[CRMRecordOut] = []
    latest_analysis: AnalysisOut | None = None
    messages: list[LeadMessageRef] = []


class LeadListResponse(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[LeadSummary]


class MessageOut(BaseModel):
    message_id: str
    customer_id: int
    customer_name: str | None = None
    customer_company: str | None = None
    message_content: str
    original_message: str | None = None
    status: Literal["pending", "approved", "rejected"]
    strategy: str | None = None
    channel: str | None = None
    created_at: datetime
    reviewed_at: datetime | None = None
    reviewer: str | None = None
    edited_at: datetime | None = None
    edited_by: str | None = None
    review_note: str | None = None


class MessageListItem(MessageOut):  # Edit D
    send_status: str | None = None


class CommunicationOut(BaseModel):
    message_id: str
    customer_id: int
    channel: str
    status: str
    sent_at: datetime | None = None
    provider: str | None = None
    recipient: str | None = None
    provider_response: dict[str, Any] | None = None
    error: str | None = None
    attempts: int | None = None
    started_at: datetime | None = None


class MessageDetail(BaseModel):
    message: MessageOut
    communication: CommunicationOut | None = None


class GenerateMessageResponse(BaseModel):
    message: MessageOut
    generation_status: str
    analysis_id: int
    recovery_score: float
    priority: str
    strategy: str


class EditRequest(BaseModel):
    message_content: str = Field(min_length=1, max_length=5000)
    editor: str = Field(min_length=1, max_length=100)


class ApproveRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)


class RejectRequest(BaseModel):
    reviewer: str = Field(min_length=1, max_length=100)
    reason: str | None = Field(default=None, max_length=1000)


class SendResponse(BaseModel):
    communication: CommunicationOut
    delivery_mode: Literal["simulated", "real"]
    crm_update: dict[str, Any]


class CRMSyncRequest(BaseModel):
    source: Literal["synthetic"] = "synthetic"


class CRMSyncResponse(BaseModel):
    source: str
    records_in_source: int
    created: int
    updated: int
    synced_at: datetime
    note: str


class OverviewResponse(BaseModel):
    generated_at: datetime
    leads_total: int
    leads_analyzed: int
    leads_not_yet_analyzed: int
    leads_opted_out: int
    priority_distribution: dict[str, int]
    strategy_distribution: dict[str, int]
    lost_pipeline_value_total: float
    lost_pipeline_value_by_priority: dict[str, float]
    messages_total: int
    approvals: dict[str, int]
    sends: dict[str, int]


class StrategyPerformance(BaseModel):
    strategy: str
    messages: int
    approved: int
    rejected: int
    sent: int


class RecoveryPerformanceResponse(BaseModel):
    generated_at: datetime
    funnel: dict[str, int]
    approval_rate: float | None
    send_rate_of_approved: float | None
    pipeline_value_total: float
    pipeline_value_with_approved_messages: float
    pipeline_value_contacted_simulated: float
    pipeline_value_contacted_real: float
    actual_recovered_revenue: float | None
    recovered_revenue_status: str
    by_strategy: list[StrategyPerformance]
    notes: list[str]


# =============================================================================
# Helpers (mapping only: no business rules live here)
# =============================================================================
def _now() -> datetime:
    return datetime.now(timezone.utc)


def _get_customer_or_404(lead_id: int) -> dict:
    with session_scope() as s:
        customer = get_agent_customer(s, lead_id)
    if customer is None:
        raise ApiError(404, "lead_not_found", f"Lead {lead_id} not found.")
    return customer


def _run_agents(customer: dict) -> tuple[dict, dict, dict]:
    analysis = analyze_lead(customer)
    score = calculate_recovery_score(customer, analysis)
    strategy = choose_recovery_strategy(customer, analysis, score)
    return analysis, score, strategy


def _analysis_out(r: RecoveryAnalysis) -> AnalysisOut:
    return AnalysisOut(
        id=r.id, lead_id=r.lead_id, created_at=r.created_at, loss_reason=r.loss_reason,
        confidence=r.confidence, evidence=r.evidence or [], email_open_rate=r.email_open_rate,
        engagement_level=r.engagement_level, urgency=r.urgency, summary=r.summary,
        recommended_angle=r.recommended_angle, recovery_score=float(r.recovery_score), priority=r.priority,
        score_breakdown=r.score_breakdown, score_explanation=r.score_explanation, strategy=r.strategy,
        strategy_reasoning=r.strategy_reasoning, recommended_channel=r.recommended_channel,
        strategy_priority=r.strategy_priority, next_action=r.next_action,
        strategy_warnings=r.strategy_warnings or [],
    )


def _latest_analyses_for(session, lead_ids: list[int]) -> dict[int, RecoveryAnalysis]:
    if not lead_ids:
        return {}
    rows = session.scalars(
        select(RecoveryAnalysis).where(RecoveryAnalysis.lead_id.in_(lead_ids))
        .distinct(RecoveryAnalysis.lead_id)  # PostgreSQL DISTINCT ON: newest row per lead
        .order_by(RecoveryAnalysis.lead_id, RecoveryAnalysis.created_at.desc(), RecoveryAnalysis.id.desc())
    ).all()
    return {r.lead_id: r for r in rows}


def _latest_analyses_subquery():
    return (
        select(RecoveryAnalysis).distinct(RecoveryAnalysis.lead_id)
        .order_by(RecoveryAnalysis.lead_id, RecoveryAnalysis.created_at.desc(), RecoveryAnalysis.id.desc())
        .subquery()
    )


def _lead_fields(lead: Lead, analysis: RecoveryAnalysis | None) -> dict:
    d = lead.to_agent_dict()
    return dict(
        id=lead.id, name=lead.name, email=lead.email, company=lead.company, industry=lead.industry,
        role=lead.role, plan_interested=lead.plan_interested, deal_value=d["deal_value"],
        stage_lost_at=lead.stage_lost_at, last_contact_date=lead.last_contact_date,
        days_inactive=d["days_inactive"], opted_out=bool(lead.opted_out),
        recovery_score=float(analysis.recovery_score) if analysis else None,
        priority=analysis.priority if analysis else None,
        strategy=analysis.strategy if analysis else None,
        last_analyzed_at=analysis.created_at if analysis else None,
        crm_notes=lead.crm_notes,  # Edit B
    )


def _enrich(records: list[dict]) -> list[MessageOut]:
    """Approval records -> MessageOut, adding customer name/company with ONE query."""
    ids = {r["customer_id"] for r in records}
    info: dict[int, tuple[str, str]] = {}
    if ids:
        with session_scope() as s:
            for row in s.execute(select(Lead.id, Lead.name, Lead.company).where(Lead.id.in_(ids))):
                info[row.id] = (row.name, row.company)
    skip = {"customer_name", "customer_company"}
    out = []
    for r in records:
        data = {k: r.get(k) for k in MessageOut.model_fields if k not in skip}
        name, company = info.get(r["customer_id"], (None, None))
        out.append(MessageOut(**data, customer_name=name, customer_company=company))
    return out


def _communication_out(record: dict) -> CommunicationOut:
    return CommunicationOut(**{k: record.get(k) for k in CommunicationOut.model_fields})


# =============================================================================
# CRM adapter: the only place CRM behavior lives. Wire crm_integration.py in here.
# =============================================================================
def record_outreach_in_crm(lead_id: int, simulated: bool) -> dict:
    """
    Records the outreach status on the lead's CRM records IN POSTGRESQL ONLY.
    It never raises: the message has already been sent, so a CRM problem must not look like a send failure.
    """
    status = CRM_STATUS_SIMULATED if simulated else CRM_STATUS_SENT
    try:
        with session_scope() as s:
            records = s.scalars(select(CRMIntegrationRecord).where(CRMIntegrationRecord.lead_id == lead_id)).all()
            for r in records:
                r.sync_status = status
            return {"status": "recorded_locally", "crm_status": status,
                    "records_updated": len(records), "pushed_to_external_crm": False}
    except Exception:
        logger.exception("CRM status update failed for lead %s", lead_id)
        return {"status": "failed", "crm_status": status, "records_updated": 0, "pushed_to_external_crm": False}


# =============================================================================
# App, CORS, auth, error handlers
# =============================================================================
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not os.environ.get("BACKEND_API_KEY", "").strip():
        logger.warning("BACKEND_API_KEY is not set: the API is UNAUTHENTICATED. Set it before exposing this server.")
    logger.info("Communication provider: %s (simulated; nothing is delivered)", provider.name)
    yield
    dispose_engine()


app = FastAPI(
    title="AI Sales Recovery API",
    version="0.1.0",
    description=(
        "Recover lost sales with a human-in-the-loop AI workflow.\n\n"
        "**Workflow:** `POST /crm/sync` -> `POST /leads/{id}/analyze` -> `POST /leads/{id}/generate-message` "
        "-> review (`PUT`/approve/reject) -> `POST /messages/{id}/send`.\n\n"
        "Communication is **simulated** (mock provider). Nothing is delivered to real customers, "
        "and recovered revenue is never estimated."
    ),
    lifespan=lifespan,
    openapi_tags=[
        {"name": "System", "description": "Health check."},
        {"name": "Leads", "description": "Read leads, run the analysis pipeline, draft messages."},
        {"name": "Messages", "description": "Human review and guarded sending."},
        {"name": "Analytics", "description": "Funnel and pipeline metrics."},
        {"name": "CRM", "description": "Lead import / CRM status."},
    ],
)

origins = [o.strip() for o in os.environ.get(
    "CORS_ORIGINS", "http://localhost:3000,http://localhost:5173").split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware, allow_origins=origins, allow_credentials=False,
    allow_methods=["GET", "POST", "PUT", "OPTIONS"], allow_headers=["Content-Type", "X-API-Key"],
)


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    """Optional shared-secret auth: enforced only when BACKEND_API_KEY is set."""
    expected = os.environ.get("BACKEND_API_KEY", "").strip()
    if not expected:
        return
    if not x_api_key or not hmac.compare_digest(x_api_key.encode(), expected.encode()):
        raise ApiError(401, "unauthorized", "Missing or invalid X-API-Key header.")


@app.exception_handler(ApiError)
async def _api_error(_: Request, exc: ApiError):
    return _error_response(exc.status_code, exc.code, exc.message, exc.details)


@app.exception_handler(ApprovalError)
async def _approval_error(_: Request, exc: ApprovalError):
    return _error_response(ERROR_STATUS.get(exc.code, 400), exc.code, exc.message)


@app.exception_handler(CommunicationError)
async def _communication_error(_: Request, exc: CommunicationError):
    return _error_response(ERROR_STATUS.get(exc.code, 400), exc.code, exc.message)


@app.exception_handler(RequestValidationError)
async def _validation_error(_: Request, exc: RequestValidationError):
    return _error_response(422, "validation_error", "Request validation failed.", exc.errors())


@app.exception_handler(StarletteHTTPException)
async def _http_error(_: Request, exc: StarletteHTTPException):
    return _error_response(exc.status_code, f"http_{exc.status_code}", str(exc.detail))


@app.exception_handler(DatabaseConfigError)
async def _db_config_error(_: Request, exc: DatabaseConfigError):
    return _error_response(503, "database_not_configured", str(exc))


@app.exception_handler(OperationalError)
async def _db_unavailable(_: Request, exc: OperationalError):
    logger.error("Database unavailable: %s", exc.orig)
    return _error_response(503, "database_unavailable", "The database is unavailable.")


@app.exception_handler(SQLAlchemyError)
async def _db_error(_: Request, exc: SQLAlchemyError):
    logger.exception("Database error")
    return _error_response(500, "database_error",
                           "A database error occurred. If this is a new setup, run: python database.py init")


@app.exception_handler(Exception)
async def _unhandled(_: Request, exc: Exception):
    logger.exception("Unhandled error")
    return _error_response(500, "internal_error", "An unexpected error occurred.")


auth = [Depends(require_api_key)]
leads_router = APIRouter(prefix="/leads", tags=["Leads"], dependencies=auth)
messages_router = APIRouter(prefix="/messages", tags=["Messages"], dependencies=auth)
analytics_router = APIRouter(prefix="/analytics", tags=["Analytics"], dependencies=auth)
crm_router = APIRouter(prefix="/crm", tags=["CRM"], dependencies=auth)


# =============================================================================
# System
# =============================================================================
@app.get("/health", tags=["System"], response_model=HealthResponse, responses={503: {"model": HealthResponse}},
         summary="Liveness and dependency check")
def health():
    db, missing = "ok", []
    try:
        missing = check_connection()
    except DatabaseConfigError:
        db = "not_configured"
    except Exception:
        db = "unavailable"
    healthy = db == "ok" and not missing
    body = HealthResponse(
        status="ok" if healthy else "degraded", database=db, schema_ready=db == "ok" and not missing,
        missing_tables=missing, communication_provider=provider.name,
        message_generator_mode=os.environ.get("MESSAGE_GENERATOR_MODE", "mock").strip().lower(),
        checked_at=_now(),
    )
    return JSONResponse(status_code=200 if healthy else 503, content=jsonable_encoder(body))


# =============================================================================
# Leads
# =============================================================================
@leads_router.get("", response_model=LeadListResponse, summary="List leads (paginated)")
def list_leads(limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0),
               opted_out: bool | None = Query(None, description="Filter by opt-out status")):
    with session_scope() as s:
        query, count_query = select(Lead), select(func.count()).select_from(Lead)
        if opted_out is not None:
            query = query.where(Lead.opted_out == opted_out)
            count_query = count_query.where(Lead.opted_out == opted_out)
        total = s.scalar(count_query) or 0
        leads = s.scalars(query.order_by(Lead.id).limit(limit).offset(offset)).all()
        latest = _latest_analyses_for(s, [lead.id for lead in leads])
        items = [LeadSummary(**_lead_fields(lead, latest.get(lead.id))) for lead in leads]
    return LeadListResponse(total=total, limit=limit, offset=offset, items=items)


@leads_router.get("/{lead_id}", response_model=LeadDetail, responses=_errs(404), summary="Lead detail")
def get_lead(lead_id: int):
    with session_scope() as s:
        lead = s.get(Lead, lead_id)
        if lead is None:
            raise ApiError(404, "lead_not_found", f"Lead {lead_id} not found.")
        analysis = get_latest_analysis(s, lead_id)
        messages = [
            LeadMessageRef(
                message_id=m.id, strategy=m.strategy, channel=m.channel,
                approval_status=m.approval.status if m.approval else None,
                send_status=m.communication.status if m.communication else None, created_at=m.created_at)
            for m in sorted(lead.messages, key=lambda m: m.created_at, reverse=True)
        ]
        crm_records = [CRMRecordOut(crm_source=r.crm_source, external_id=r.external_id,
                                    sync_status=r.sync_status, last_synced_at=r.last_synced_at)
                       for r in lead.crm_records]
        # Edit C: crm_notes now comes from _lead_fields(), so it is not passed again here.
        return LeadDetail(**_lead_fields(lead, analysis), crm_records=crm_records,
                          latest_analysis=_analysis_out(analysis) if analysis else None, messages=messages)


@leads_router.post("/{lead_id}/analyze", response_model=AnalysisOut, responses=_errs(404),
                   summary="Analyze, score and choose a strategy (saved to the database)")
def analyze(lead_id: int):
    customer = _get_customer_or_404(lead_id)
    analysis, score, strategy = _run_agents(customer)
    with session_scope() as s:
        row = save_pipeline_result(s, lead_id, analysis, score, strategy)
        s.refresh(row)
        return _analysis_out(row)


@leads_router.post("/{lead_id}/generate-message", status_code=201, response_model=GenerateMessageResponse,
                   responses=_errs(403, 404, 409, 422, 502, 503),
                   summary="Re-run the pipeline, draft a message and create a PENDING approval request")
def generate_message(lead_id: int):
    customer = _get_customer_or_404(lead_id)
    analysis, score, strategy = _run_agents(customer)
    with session_scope() as s:
        analysis_id = save_pipeline_result(s, lead_id, analysis, score, strategy).id

    # Fail early (before any LLM call) if a draft is already waiting for review.
    with session_scope() as s:
        pending_id = s.scalar(select(MessageApproval.message_id).where(
            MessageApproval.lead_id == lead_id, MessageApproval.status == "pending"))
    if pending_id:
        raise ApiError(409, "duplicate_request",
                       f"Lead {lead_id} already has a pending message. Approve or reject it first.",
                       {"message_id": pending_id})

    gen = generate_recovery_message(customer, analysis, score, strategy)  # mode comes from MESSAGE_GENERATOR_MODE
    status = gen["generation_status"]
    if status == "skipped_opted_out":
        raise ApiError(403, "customer_opted_out", f"Lead {lead_id} has opted out. No message was generated.")
    if status == "skipped_no_contact":
        raise ApiError(409, "no_contact_strategy",
                       "The strategy for this lead is 'No contact', so no message was generated.",
                       {"reasoning": strategy.get("reasoning"), "next_action": strategy.get("next_action")})
    if status in GENERATION_FAILURES:
        http_status, code = GENERATION_FAILURES[status]
        logger.error("Message generation failed for lead %s: %s (%s)", lead_id, status, gen.get("error"))
        public = "The inference provider request failed. See server logs." if status == "failed_api" else gen.get("error")
        raise ApiError(http_status, code, public or "Message generation failed.")

    record = create_approval_request(customer, gen, store=approvals)  # always starts as "pending"
    return GenerateMessageResponse(
        message=_enrich([record])[0], generation_status=status, analysis_id=analysis_id,
        recovery_score=score["recovery_score"], priority=score["priority"], strategy=strategy["strategy"])


# =============================================================================
# Messages (the list route and /pending are declared BEFORE /{message_id})
# =============================================================================
@messages_router.get("", response_model=list[MessageListItem],  # Edit E
                     summary="List messages by approval status (newest first)")
def list_messages(status: Literal["pending", "approved", "rejected"] = Query("pending"),
                  limit: int = Query(100, ge=1, le=200)):
    records = sorted(approvals.list_by_status(status), key=lambda r: r["created_at"], reverse=True)[:limit]
    items = []
    for message in _enrich(records):
        comm = get_send_record(message.message_id, store=sendlog)
        items.append(MessageListItem(**message.model_dump(), send_status=comm["status"] if comm else None))
    return items


@messages_router.get("/pending", response_model=list[MessageOut], summary="Messages awaiting review (oldest first)")
def pending_messages():
    return _enrich(get_pending_approvals(store=approvals))


@messages_router.get("/{message_id}", response_model=MessageDetail, responses=_errs(404),
                     summary="One message with its send record, if any")
def message_detail(message_id: str):
    record = get_approval(message_id, store=approvals)
    comm = get_send_record(message_id, store=sendlog)
    return MessageDetail(message=_enrich([record])[0], communication=_communication_out(comm) if comm else None)


@messages_router.put("/{message_id}", response_model=MessageOut, responses=_errs(404, 409, 422),
                     summary="Edit a PENDING message")
def edit_message(message_id: str, body: EditRequest):
    record = edit_pending_message(message_id, body.message_content, body.editor, store=approvals)
    return _enrich([record])[0]


@messages_router.post("/{message_id}/approve", response_model=MessageOut, responses=_errs(403, 404, 409, 422),
                      summary="Approve a PENDING message (does NOT send it)")
def approve(message_id: str, body: ApproveRequest):
    record = approve_message(message_id, body.reviewer, customer_lookup=db_customer_lookup, store=approvals)
    return _enrich([record])[0]


@messages_router.post("/{message_id}/reject", response_model=MessageOut, responses=_errs(404, 409, 422),
                      summary="Reject a PENDING message")
def reject(message_id: str, body: RejectRequest):
    record = reject_message(message_id, body.reviewer, body.reason, store=approvals)
    return _enrich([record])[0]


@messages_router.post("/{message_id}/send", response_model=SendResponse,
                      responses=_errs(403, 404, 409, 422, 502),
                      summary="Send an APPROVED message through the (mock) provider")
def send(message_id: str):
    # The record is loaded from the database, never from the request, so approval status cannot be forged.
    record = get_approval(message_id, store=approvals)
    customer = db_customer_lookup(record["customer_id"])  # current opt-out status
    if customer is None:
        raise ApiError(404, "customer_not_found", f"Customer {record['customer_id']} not found.")

    result = send_recovery_message(record, customer, provider=provider, store=sendlog)
    if result["status"] != "sent":
        logger.error("Send failed for message %s: %s", message_id, result.get("error"))
        raise ApiError(502, "provider_failure",
                       "The provider failed. The message was not delivered and may be retried.",
                       {"message_id": message_id, "attempts": result.get("attempts")})

    simulated = provider.name in SIMULATED_PROVIDERS
    return SendResponse(
        communication=_communication_out(result),
        delivery_mode="simulated" if simulated else "real",
        crm_update=record_outreach_in_crm(result["customer_id"], simulated),
    )


# =============================================================================
# Analytics
# =============================================================================
def _approval_counts(s) -> dict[str, int]:
    counts = {"pending": 0, "approved": 0, "rejected": 0}
    for status, n in s.execute(select(MessageApproval.status, func.count()).group_by(MessageApproval.status)):
        counts[status] = n
    return counts


def _send_counts(s) -> dict[str, int]:
    counts = {"sent_simulated": 0, "sent_real": 0, "failed": 0, "in_progress": 0}
    rows = s.execute(select(CommunicationLog.status, CommunicationLog.provider, func.count())
                     .group_by(CommunicationLog.status, CommunicationLog.provider))
    for status, prov, n in rows:
        if status == "sent":
            counts["sent_simulated" if prov in SIMULATED_PROVIDERS else "sent_real"] += n
        elif status == "failed":
            counts["failed"] += n
        else:
            counts["in_progress"] += n
    return counts


def _value_of(s, lead_ids_query) -> float:
    return float(s.scalar(select(func.coalesce(func.sum(Lead.deal_value), 0)).where(Lead.id.in_(lead_ids_query))) or 0)


@analytics_router.get("/overview", response_model=OverviewResponse, summary="Leads, priorities, strategies, messages")
def analytics_overview():
    with session_scope() as s:
        total = s.scalar(select(func.count()).select_from(Lead)) or 0
        opted_out = s.scalar(select(func.count()).select_from(Lead).where(Lead.opted_out.is_(True))) or 0
        analyzed = s.scalar(select(func.count(func.distinct(RecoveryAnalysis.lead_id)))) or 0
        value_total = float(s.scalar(select(func.coalesce(func.sum(Lead.deal_value), 0))) or 0)

        latest = _latest_analyses_subquery()
        priorities = {"High": 0, "Medium": 0, "Low": 0}
        values = {"High": 0.0, "Medium": 0.0, "Low": 0.0}
        for prio, n, val in s.execute(
                select(latest.c.priority, func.count(), func.coalesce(func.sum(Lead.deal_value), 0))
                .select_from(latest).join(Lead, Lead.id == latest.c.lead_id).group_by(latest.c.priority)):
            priorities[prio], values[prio] = n, float(val)
        strategies = {name: n for name, n in s.execute(select(latest.c.strategy, func.count()).group_by(latest.c.strategy))}

        return OverviewResponse(
            generated_at=_now(), leads_total=total, leads_analyzed=analyzed,
            leads_not_yet_analyzed=max(total - analyzed, 0), leads_opted_out=opted_out,
            priority_distribution=priorities, strategy_distribution=strategies,
            lost_pipeline_value_total=value_total, lost_pipeline_value_by_priority=values,
            messages_total=s.scalar(select(func.count()).select_from(RecoveryMessage)) or 0,
            approvals=_approval_counts(s), sends=_send_counts(s),
        )


@analytics_router.get("/recovery-performance", response_model=RecoveryPerformanceResponse,
                      summary="Outreach funnel. Recovered revenue is NOT estimated.")
def analytics_recovery_performance():
    with session_scope() as s:
        approvals_c, sends_c = _approval_counts(s), _send_counts(s)
        total_messages = s.scalar(select(func.count()).select_from(RecoveryMessage)) or 0
        sent_total = sends_c["sent_simulated"] + sends_c["sent_real"]
        decided = approvals_c["approved"] + approvals_c["rejected"]

        sent_ok = (CommunicationLog.status == "sent")
        simulated_leads = select(CommunicationLog.lead_id).where(
            sent_ok, CommunicationLog.provider.in_(list(SIMULATED_PROVIDERS)))
        real_leads = select(CommunicationLog.lead_id).where(
            sent_ok, CommunicationLog.provider.not_in(list(SIMULATED_PROVIDERS)))
        approved_leads = select(MessageApproval.lead_id).where(MessageApproval.status == "approved")

        by_strategy = [
            StrategyPerformance(strategy=name or "unknown", messages=m, approved=a, rejected=r, sent=sent)
            for name, m, a, r, sent in s.execute(
                select(RecoveryMessage.strategy, func.count(),
                       func.count().filter(MessageApproval.status == "approved"),
                       func.count().filter(MessageApproval.status == "rejected"),
                       func.count().filter(CommunicationLog.status == "sent"))
                .select_from(RecoveryMessage)
                .join(MessageApproval, MessageApproval.message_id == RecoveryMessage.id)
                .outerjoin(CommunicationLog, CommunicationLog.message_id == RecoveryMessage.id)
                .group_by(RecoveryMessage.strategy))
        ]

        return RecoveryPerformanceResponse(
            generated_at=_now(),
            funnel={
                "leads_total": s.scalar(select(func.count()).select_from(Lead)) or 0,
                "leads_analyzed": s.scalar(select(func.count(func.distinct(RecoveryAnalysis.lead_id)))) or 0,
                "messages_generated": total_messages,
                "messages_pending": approvals_c["pending"],
                "messages_approved": approvals_c["approved"],
                "messages_rejected": approvals_c["rejected"],
                "messages_sent_simulated": sends_c["sent_simulated"],
                "messages_sent_real": sends_c["sent_real"],
                "send_failures": sends_c["failed"],
            },
            approval_rate=round(approvals_c["approved"] / decided, 3) if decided else None,
            send_rate_of_approved=round(sent_total / approvals_c["approved"], 3) if approvals_c["approved"] else None,
            pipeline_value_total=float(s.scalar(select(func.coalesce(func.sum(Lead.deal_value), 0))) or 0),
            pipeline_value_with_approved_messages=_value_of(s, approved_leads),
            pipeline_value_contacted_simulated=_value_of(s, simulated_leads),
            pipeline_value_contacted_real=_value_of(s, real_leads),
            actual_recovered_revenue=None,
            recovered_revenue_status="not_tracked",
            by_strategy=by_strategy,
            notes=[
                "Pipeline values are the sizes of deals that were lost. They are NOT recovered revenue.",
                "'simulated' values come from mock sends: no customer was contacted.",
                "actual_recovered_revenue is null because no deal outcomes (replies, won deals) are tracked yet.",
            ],
        )


# =============================================================================
# CRM
# =============================================================================
@crm_router.post("/sync", response_model=CRMSyncResponse, responses=_errs(404, 422),
                 summary="Import leads (idempotent: re-running never creates duplicates)")
def crm_sync(body: CRMSyncRequest | None = None):
    source = body.source if body else "synthetic"
    try:
        customers = load_customers()  # data/customers.json, relative to where uvicorn is started
    except SystemExit:
        raise ApiError(404, "data_file_not_found", "data/customers.json not found. Run: python generate_data.py")
    try:
        with session_scope() as s:  # all-or-nothing
            stats = import_customers(s, customers, crm_source=source)
    except ValueError as exc:
        raise ApiError(422, "invalid_crm_data", str(exc))
    return CRMSyncResponse(
        source=source, records_in_source=len(customers), created=stats["created"], updated=stats["updated"],
        synced_at=_now(),
        note="Imported from data/customers.json into PostgreSQL. No external CRM was contacted.",
    )


app.include_router(leads_router)
app.include_router(messages_router)
app.include_router(analytics_router)
app.include_router(crm_router)