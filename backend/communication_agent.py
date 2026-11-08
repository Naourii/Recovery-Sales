"""
communication_agent.py
Communication layer: sends APPROVED recovery messages through a pluggable provider.
Input : approval record (from approval.py), customer dict
Output: send record (message_id, customer_id, channel, status, sent_at, provider_response, ...)

Only MockCommunicationProvider exists today: it simulates sending and never contacts anyone.
A real provider = one new class implementing CommunicationProvider.
"""

import json
import re
import sys
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from approval import APPROVED
from strategy_agent import _is_opted_out

SENDING = "sending"
SENT = "sent"
FAILED = "failed"

SUPPORTED_CHANNELS = frozenset({"email"})
DEFAULT_SUBJECT = "Following up"
EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


# --- Errors (stable .code values so FastAPI can map them to HTTP codes later) ----
class CommunicationError(Exception):
    code = "communication_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidInputError(CommunicationError):
    code = "invalid_input"


class NotApprovedError(CommunicationError):
    code = "not_approved"


class OptedOutError(CommunicationError):
    code = "customer_opted_out"


class UnsupportedChannelError(CommunicationError):
    code = "unsupported_channel"


class InvalidRecipientError(CommunicationError):
    code = "invalid_recipient"


class DuplicateSendError(CommunicationError):
    code = "duplicate_send"


# --- Provider interface -----------------------------------------------------------
class ProviderError(Exception):
    """Raised by a provider when delivery fails."""


class CommunicationProvider(ABC):
    name: str = "base"
    supported_channels: frozenset = frozenset()

    @abstractmethod
    def send(self, *, channel: str, recipient: str, subject: str | None, body: str, message_id: str) -> dict:
        """
        Deliver one message. Return a dict describing the provider's response.
        Raise ProviderError on failure. `message_id` is the idempotency key: a real
        provider should pass it through so a retried request cannot deliver twice.
        """


class MockCommunicationProvider(CommunicationProvider):
    """Simulates sending. Stores messages in memory only; contacts no one."""

    name = "mock"
    supported_channels = frozenset({"email"})

    def __init__(self, fail_next: int = 0) -> None:
        self._outbox: list[dict] = []
        self._fail_next = fail_next  # simulate N provider failures (for testing error handling)
        self._lock = threading.Lock()

    @property
    def outbox(self) -> list[dict]:
        with self._lock:
            return [dict(m) for m in self._outbox]

    def send(self, *, channel: str, recipient: str, subject: str | None, body: str, message_id: str) -> dict:
        if channel not in self.supported_channels:
            raise ProviderError(f"Mock provider does not support channel {channel!r}.")
        with self._lock:
            if self._fail_next > 0:
                self._fail_next -= 1
                raise ProviderError("Simulated provider failure (mock).")
            self._outbox.append(
                {"message_id": message_id, "channel": channel, "recipient": recipient,
                 "subject": subject, "body": body}
            )
        return {
            "provider": self.name,
            "simulated": True,
            "accepted": True,
            "provider_message_id": f"mock-{uuid.uuid4().hex[:12]}",
            "idempotency_key": message_id,
            "note": "Simulated send. No email was delivered.",
        }


# --- Send log storage -------------------------------------------------------------
class SendLogStore(ABC):
    """Atomic claim-by-message_id is what prevents duplicate sends."""

    @abstractmethod
    def claim(self, record: dict) -> dict | None:
        """
        Atomically reserve record['message_id'] for sending.
        Return None if claimed. Return the existing record if the message is already
        'sending' or 'sent'. A previous 'failed' record may be re-claimed (retry).
        """

    @abstractmethod
    def finalize(self, message_id: str, changes: dict) -> dict: ...

    @abstractmethod
    def get(self, message_id: str) -> dict | None: ...

    @abstractmethod
    def list_all(self) -> list[dict]: ...


class InMemorySendLog(SendLogStore):
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def claim(self, record: dict) -> dict | None:
        with self._lock:
            existing = self._records.get(record["message_id"])
            if existing is not None and existing["status"] != FAILED:
                return dict(existing)
            new = dict(record)
            new["attempts"] = (existing["attempts"] if existing else 0) + 1
            self._records[new["message_id"]] = new
            return None

    def finalize(self, message_id: str, changes: dict) -> dict:
        with self._lock:
            record = self._records[message_id]
            record.update(changes)
            return dict(record)

    def get(self, message_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(message_id)
            return dict(record) if record else None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records.values()]


_default_provider = MockCommunicationProvider()
_default_log = InMemorySendLog()


# --- Helpers ----------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _split_subject(content: str) -> tuple[str, str]:
    """The Message Generator writes email drafts as 'Subject: ...' then the body."""
    lines = content.strip().splitlines()
    if lines and lines[0].lower().startswith("subject:"):
        subject = lines[0][len("subject:"):].strip() or DEFAULT_SUBJECT
        return subject, "\n".join(lines[1:]).strip()
    return DEFAULT_SUBJECT, content.strip()


# --- Public API -------------------------------------------------------------------
def send_recovery_message(approval_record: dict, customer: dict, *,
                          provider: CommunicationProvider | None = None,
                          store: SendLogStore | None = None) -> dict:
    """
    Send one approved message. Raises CommunicationError subclasses for invalid requests.
    If the provider itself fails, the failure is recorded and a record with
    status 'failed' is returned (a retry is then allowed).
    `customer` must be the CURRENT customer record so opt-out status is up to date.
    """
    provider = _default_provider if provider is None else provider
    store = _default_log if store is None else store

    # 1. Basic validation
    if not isinstance(approval_record, dict):
        raise InvalidInputError("approval_record must be a dict from approval.py.")
    message_id = approval_record.get("message_id")
    if not isinstance(message_id, str) or not message_id.strip():
        raise InvalidInputError("approval_record must contain a non-empty 'message_id'.")

    # 2. Status must be exactly "approved"
    status = approval_record.get("status")
    if status != APPROVED:
        raise NotApprovedError(
            f"Message {message_id} has status {status!r}; only messages with status 'approved' can be sent."
        )

    # 3. Customer must exist and match the approval record
    if not isinstance(customer, dict) or customer.get("id") is None:
        raise InvalidInputError("customer must be a dict with an 'id'.")
    if customer["id"] != approval_record.get("customer_id"):
        raise InvalidInputError(
            f"Customer {customer['id']} does not match the approval record's "
            f"customer {approval_record.get('customer_id')}."
        )

    # 4. Opt-out
    if _is_opted_out(customer, []):
        raise OptedOutError(f"Customer {customer['id']} has opted out. Message {message_id} was not sent.")

    # 5. Channel
    channel = approval_record.get("channel")
    if not isinstance(channel, str) or not channel.strip():
        raise InvalidInputError(f"Message {message_id} has no channel recorded.")
    channel = channel.strip().lower()
    if channel not in SUPPORTED_CHANNELS or channel not in provider.supported_channels:
        raise UnsupportedChannelError(
            f"Channel {channel!r} is not supported yet. Supported channels: {sorted(SUPPORTED_CHANNELS)}."
        )

    # 6. Recipient and content
    recipient = customer.get("email")
    if not isinstance(recipient, str) or not EMAIL_PATTERN.match(recipient.strip()):
        raise InvalidRecipientError(f"Customer {customer['id']} has no valid email address.")
    recipient = recipient.strip()

    content = approval_record.get("message_content")
    if not isinstance(content, str) or not content.strip():
        raise InvalidInputError(f"Message {message_id} has empty content.")
    subject, body = _split_subject(content)
    if not body:
        raise InvalidInputError(f"Message {message_id} has a subject but no body.")

    # 7. Atomic claim: the duplicate-send guard
    blocking = store.claim({
        "message_id": message_id,
        "customer_id": customer["id"],
        "channel": channel,
        "status": SENDING,
        "sent_at": None,
        "provider_response": None,
        "provider": provider.name,
        "recipient": recipient,
        "error": None,
        "started_at": _now(),
    })
    if blocking is not None:
        if blocking["status"] == SENT:
            raise DuplicateSendError(
                f"Message {message_id} was already sent at {blocking['sent_at']}. Duplicate sends are not allowed."
            )
        raise DuplicateSendError(f"Message {message_id} is already being sent. Duplicate sends are not allowed.")

    # 8. Deliver. The record must never be left stuck in 'sending'.
    try:
        response = provider.send(channel=channel, recipient=recipient, subject=subject,
                                 body=body, message_id=message_id)
        if not isinstance(response, dict):
            raise ProviderError("Provider returned an invalid response.")
        changes = {"status": SENT, "sent_at": _now(), "provider_response": response, "error": None}
    except ProviderError as exc:
        changes = {"status": FAILED, "error": str(exc)}
    except Exception as exc:  # a buggy provider must not crash the caller or leave 'sending'
        changes = {"status": FAILED, "error": f"Unexpected provider error: {type(exc).__name__}"}
    return store.finalize(message_id, changes)


def get_send_record(message_id: str, *, store: SendLogStore | None = None) -> dict | None:
    return (_default_log if store is None else store).get(message_id)


def list_send_records(*, store: SendLogStore | None = None) -> list[dict]:
    return (_default_log if store is None else store).list_all()


# --- Tests ------------------------------------------------------------------------
def _ok(label: str, condition: bool) -> None:
    assert condition, f"FAILED: {label}"
    print(f"  PASS {label}")


def _expect(exc_type, label: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        print(f"  PASS {label}\n       -> [{exc.code}] {exc.message}")
        return
    except Exception as exc:
        raise AssertionError(f"{label}: expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
    raise AssertionError(f"{label}: expected {exc_type.__name__} but no error was raised")


def _build_email_drafts(n: int):
    # Test-only imports, so the core module stays independent of the other agents.
    from lead_analysis import analyze_lead, load_customers
    from message_generator import generate_recovery_message
    from recovery_scoring import calculate_recovery_score
    from strategy_agent import NO_CONTACT, choose_recovery_strategy

    customers = load_customers()
    drafts = []
    for c in customers:
        a = analyze_lead(c)
        s = calculate_recovery_score(c, a)
        st = choose_recovery_strategy(c, a, s)
        if st["strategy"] == NO_CONTACT or st["recommended_channel"] != "email":
            continue
        gen = generate_recovery_message(c, a, s, st, mode="mock")
        if gen["generation_status"] == "mock":
            drafts.append((c, gen))
        if len(drafts) == n:
            return customers, drafts
    sys.exit(f"Need {n} customers with an email-channel draft, found {len(drafts)}.")


def run_tests() -> None:
    from approval import InMemoryApprovalStore, approve_message, create_approval_request, reject_message

    customers, drafts = _build_email_drafts(4)
    (ca, ga), (cb, gb), (cc, gc), (cd, gd) = drafts

    approvals = InMemoryApprovalStore()
    provider = MockCommunicationProvider()
    log = InMemorySendLog()
    lookup = lambda cid: next((c for c in customers if c["id"] == cid), None)
    send = lambda rec, cust: send_recovery_message(rec, cust, provider=provider, store=log)

    req_a = create_approval_request(ca, ga, store=approvals)
    req_b = create_approval_request(cb, gb, store=approvals)
    req_c = create_approval_request(cc, gc, store=approvals)
    req_d = create_approval_request(cd, gd, store=approvals)

    rec_a = approve_message(req_a["message_id"], "alice", customer_lookup=lookup, store=approvals)
    rec_c = reject_message(req_c["message_id"], "bob", "Not appropriate", store=approvals)
    rec_d = approve_message(req_d["message_id"], "alice", customer_lookup=lookup, store=approvals)

    print("TEST 1: approved message is sent (simulated)")
    res = send(rec_a, ca)
    print(json.dumps(res, indent=2))
    _ok("status is 'sent' with sent_at", res["status"] == SENT and res["sent_at"] is not None)
    _ok("ids and channel recorded", res["message_id"] == rec_a["message_id"] and res["customer_id"] == ca["id"] and res["channel"] == "email")
    _ok("provider_response says simulated", res["provider_response"]["simulated"] is True)
    out = provider.outbox
    _ok("exactly 1 message in the mock outbox", len(out) == 1)
    _ok("sent to the customer's email, subject split from body",
        out[0]["recipient"] == ca["email"] and bool(out[0]["subject"]) and not out[0]["body"].lower().startswith("subject:"))

    print("\nTEST 2: pending message is rejected")
    _expect(NotApprovedError, "pending message cannot be sent", send, req_b, cb)
    _expect(NotApprovedError, "status must match exactly ('Approved' is not 'approved')", send, {**rec_a, "status": "Approved"}, ca)

    print("\nTEST 3: rejected message is blocked")
    _expect(NotApprovedError, "rejected message cannot be sent", send, rec_c, cc)

    print("\nTEST 4: opted-out customer is blocked")
    _expect(OptedOutError, "approved message blocked after customer opted out", send, rec_d, {**cd, "opted_out": True})
    _expect(OptedOutError, "unrecognized opted_out value fails safe", send, rec_d, {**cd, "opted_out": "garbage"})
    _ok("nothing sent, nothing logged", len(provider.outbox) == 1 and log.get(rec_d["message_id"]) is None)

    print("\nTEST 5: duplicate send attempt")
    _expect(DuplicateSendError, "second send of the same message_id is blocked", send, rec_a, ca)
    _ok("outbox still has exactly 1 message", len(provider.outbox) == 1)

    print("\nTEST 6: other invalid requests")
    _expect(InvalidInputError, "customer does not match approval record", send, rec_d, ca)
    _expect(UnsupportedChannelError, "linkedin channel is not supported yet", send, {**rec_d, "channel": "linkedin"}, cd)
    _expect(InvalidRecipientError, "invalid email address", send, rec_d, {**cd, "email": "not-an-email"})
    _expect(InvalidInputError, "missing message_id", send, {**rec_d, "message_id": ""}, cd)
    _expect(InvalidInputError, "empty content", send, {**rec_d, "message_content": "   "}, cd)
    _ok("none of these were logged or sent", log.get(rec_d["message_id"]) is None and len(provider.outbox) == 1)

    print("\nTEST 7: provider failure is recorded, then retry succeeds")
    failing = MockCommunicationProvider(fail_next=1)
    res = send_recovery_message(rec_d, cd, provider=failing, store=log)
    _ok("status is 'failed' with an error and no sent_at", res["status"] == FAILED and res["error"] and res["sent_at"] is None)
    print(f"       -> error: {res['error']}")
    res = send_recovery_message(rec_d, cd, provider=failing, store=log)
    _ok("retry after failure succeeds (attempts == 2)", res["status"] == SENT and res["attempts"] == 2)
    _expect(DuplicateSendError, "no further sends after success", send_recovery_message, rec_d, cd, provider=failing, store=log)
    _ok("failing provider delivered exactly once", len(failing.outbox) == 1)

    print("\nTEST 8: 10 simultaneous sends of the same message")
    rec_b = approve_message(req_b["message_id"], "alice", customer_lookup=lookup, store=approvals)
    outcomes: list[str] = []

    def worker() -> None:
        try:
            send(rec_b, cb)
            outcomes.append("sent")
        except DuplicateSendError:
            outcomes.append("blocked")

    threads = [threading.Thread(target=worker) for _ in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _ok(f"exactly 1 sent, 9 blocked (got {outcomes.count('sent')} / {outcomes.count('blocked')})",
        outcomes.count("sent") == 1 and outcomes.count("blocked") == 9)
    _ok("mock outbox grew by exactly 1", len(provider.outbox) == 2)

    print("\nAll communication tests passed. No real messages were sent.")


if __name__ == "__main__":
    run_tests()