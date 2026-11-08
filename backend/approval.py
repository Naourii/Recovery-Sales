"""
approval.py
Human-in-the-loop approval workflow for generated recovery messages.
Input : customer dict + result dict from generate_recovery_message()
Output: approval request records (status: pending -> approved | rejected)

This module NEVER sends messages. It only records human decisions.
Storage is in-memory behind the ApprovalStore interface; a PostgreSQL
implementation can replace it without touching the functions below.
"""

import sys
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Callable

from lead_analysis import analyze_lead, load_customers
from message_generator import generate_recovery_message
from recovery_scoring import calculate_recovery_score
from strategy_agent import NO_CONTACT, _is_opted_out, choose_recovery_strategy

PENDING = "pending"
APPROVED = "approved"
REJECTED = "rejected"

ELIGIBLE_GENERATION_STATUSES = {"generated", "mock"}
MAX_MESSAGE_LENGTH = 5000
PAST_TENSE = {"approve": "approved", "reject": "rejected", "edit": "edited"}


# --- Errors (each has a stable .code so FastAPI can map them to HTTP codes later) ---
class ApprovalError(Exception):
    code = "approval_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidInputError(ApprovalError):
    code = "invalid_input"


class ApprovalNotFoundError(ApprovalError):
    code = "not_found"


class InvalidStatusError(ApprovalError):
    code = "invalid_status"


class OptedOutError(ApprovalError):
    code = "customer_opted_out"


class CustomerNotFoundError(ApprovalError):
    code = "customer_not_found"


class DuplicateRequestError(ApprovalError):
    code = "duplicate_request"


# --- Storage interface --------------------------------------------------------
class ApprovalStore(ABC):
    """Four methods are all a backend (in-memory, PostgreSQL) must implement."""

    @abstractmethod
    def add(self, record: dict) -> None:
        """Insert a record. Raise DuplicateRequestError if the customer already has a pending one."""

    @abstractmethod
    def get(self, message_id: str) -> dict | None: ...

    @abstractmethod 
    def list_by_status(self, status: str) -> list[dict]: ...

    @abstractmethod
    def update_if_pending(self, message_id: str, changes: dict) -> dict | None:
        """
        Atomically apply `changes` ONLY if the record exists and is still pending.
        Return the updated record, or None. This is the compare-and-set that prevents
        two reviewers from both approving the same message.
        """


class InMemoryApprovalStore(ApprovalStore):
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}
        self._lock = threading.Lock()

    def add(self, record: dict) -> None:
        with self._lock:
            if record["message_id"] in self._records:
                raise DuplicateRequestError(f"Message id {record['message_id']} already exists.")
            for existing in self._records.values():
                if existing["customer_id"] == record["customer_id"] and existing["status"] == PENDING:
                    raise DuplicateRequestError(
                        f"Customer {record['customer_id']} already has a pending approval request "
                        f"({existing['message_id']}). Resolve it before creating another."
                    )
            self._records[record["message_id"]] = dict(record)

    def get(self, message_id: str) -> dict | None:
        with self._lock:
            record = self._records.get(message_id)
            return dict(record) if record else None  # copy: callers can't mutate the store

    def list_by_status(self, status: str) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records.values() if r["status"] == status]

    def update_if_pending(self, message_id: str, changes: dict) -> dict | None:
        with self._lock:
            record = self._records.get(message_id)
            if record is None or record["status"] != PENDING:
                return None
            record.update(changes)
            return dict(record)


_default_store = InMemoryApprovalStore()


def _resolve_store(store: ApprovalStore | None) -> ApprovalStore:
    return _default_store if store is None else store


# --- Customer lookup (used for the opt-out re-check at approval time) ---------
def default_customer_lookup(customer_id) -> dict | None:
    """Reads data/customers.json. Replace with a PostgreSQL lookup later."""
    try:
        customers = load_customers()
    except SystemExit:
        raise ApprovalError("Customer data is unavailable; cannot verify opt-out status.")
    return next((c for c in customers if c.get("id") == customer_id), None)


# --- Helpers ------------------------------------------------------------------
def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _require_text(value, field: str, max_len: int | None = None) -> str:
    if not isinstance(value, str) or not value.strip():
        raise InvalidInputError(f"{field} must be a non-empty string.")
    value = value.strip()
    if max_len is not None and len(value) > max_len:
        raise InvalidInputError(f"{field} is too long ({len(value)} > {max_len} characters).")
    return value


def _status_error(record: dict, action: str) -> InvalidStatusError:
    mid, status = record["message_id"], record["status"]
    if action == "approve" and status == APPROVED:
        return InvalidStatusError(
            f"Message {mid} was already approved by {record['reviewer']} at {record['reviewed_at']}. "
            "Duplicate approvals are not allowed."
        )
    return InvalidStatusError(
        f"Cannot {action} message {mid}: status is '{status}'. "
        f"Only 'pending' messages can be {PAST_TENSE[action]}."
    )


def _get_pending_or_raise(store: ApprovalStore, message_id, action: str) -> dict:
    message_id = _require_text(message_id, "message_id")
    record = store.get(message_id)
    if record is None:
        raise ApprovalNotFoundError(f"No approval request with message_id '{message_id}'.")
    if record["status"] != PENDING:
        raise _status_error(record, action)
    return record


def _update_or_raise(store: ApprovalStore, message_id: str, changes: dict, action: str) -> dict:
    updated = store.update_if_pending(message_id, changes)
    if updated is not None:
        return updated
    # Lost a race or status changed since we checked: explain precisely why.
    current = store.get(message_id)
    if current is None:
        raise ApprovalNotFoundError(f"No approval request with message_id '{message_id}'.")
    raise _status_error(current, action)


# --- Public API ---------------------------------------------------------------
def create_approval_request(customer: dict, generation_result: dict, *, store: ApprovalStore | None = None) -> dict:
    """Create a PENDING request from a customer + generate_recovery_message() result."""
    store = _resolve_store(store)

    if not isinstance(customer, dict) or customer.get("id") is None:
        raise InvalidInputError("customer must be a dict with an 'id'.")
    if not isinstance(generation_result, dict):
        raise InvalidInputError("generation_result must be the dict returned by generate_recovery_message().")

    if _is_opted_out(customer, []):
        raise OptedOutError(f"Customer {customer['id']} has opted out. No approval request can be created.")

    gen_status = generation_result.get("generation_status")
    if gen_status not in ELIGIBLE_GENERATION_STATUSES:
        raise InvalidInputError(
            f"Cannot create an approval request: generation_status is {gen_status!r}, "
            f"expected one of {sorted(ELIGIBLE_GENERATION_STATUSES)}."
        )
    content = _require_text(generation_result.get("message"), "message", MAX_MESSAGE_LENGTH)
    if generation_result.get("customer_id") != customer["id"]:
        raise InvalidInputError("generation_result.customer_id does not match customer.id.")

    record = {
        "message_id": str(uuid.uuid4()),
        "customer_id": customer["id"],
        "message_content": content,
        "status": PENDING,
        "created_at": _now(),
        "reviewed_at": None,
        "reviewer": None,
        # extras kept for audit and the future dashboard
        "original_message": content,
        "strategy": generation_result.get("strategy"),
        "channel": generation_result.get("channel"),
        "edited_at": None,
        "edited_by": None,
        "review_note": None,
    }
    store.add(record)
    return dict(record)


def get_pending_approvals(*, store: ApprovalStore | None = None) -> list[dict]:
    """All pending requests, oldest first."""
    pending = _resolve_store(store).list_by_status(PENDING)
    return sorted(pending, key=lambda r: r["created_at"])


def get_approval(message_id: str, *, store: ApprovalStore | None = None) -> dict:
    store = _resolve_store(store)
    record = store.get(_require_text(message_id, "message_id"))
    if record is None:
        raise ApprovalNotFoundError(f"No approval request with message_id '{message_id}'.")
    return record


def edit_pending_message(message_id: str, new_content: str, editor: str | None = None,
                         *, store: ApprovalStore | None = None) -> dict:
    """Edit the text of a PENDING message. Status stays 'pending'."""
    store = _resolve_store(store)
    _get_pending_or_raise(store, message_id, "edit")
    content = _require_text(new_content, "new_content", MAX_MESSAGE_LENGTH)
    changes = {"message_content": content, "edited_at": _now(), "edited_by": editor}
    return _update_or_raise(store, message_id, changes, "edit")


def approve_message(message_id: str, reviewer: str, *,
                    customer_lookup: Callable[[int], dict | None] | None = None,
                    store: ApprovalStore | None = None) -> dict:
    """
    Mark a PENDING message approved. Re-checks the customer's CURRENT opt-out status first;
    an opted-out (or unknown) customer can never be approved. Does not send anything.
    """
    store = _resolve_store(store)
    lookup = customer_lookup or default_customer_lookup
    reviewer = _require_text(reviewer, "reviewer")

    record = _get_pending_or_raise(store, message_id, "approve")

    customer = lookup(record["customer_id"])
    if customer is None:
        raise CustomerNotFoundError(
            f"Customer {record['customer_id']} not found; cannot verify opt-out status, so approval is blocked."
        )
    if _is_opted_out(customer, []):
        raise OptedOutError(
            f"Customer {record['customer_id']} has opted out. Approval blocked. "
            "Reject this message instead."
        )

    changes = {"status": APPROVED, "reviewer": reviewer, "reviewed_at": _now()}
    return _update_or_raise(store, record["message_id"], changes, "approve")


def reject_message(message_id: str, reviewer: str, reason: str | None = None,
                   *, store: ApprovalStore | None = None) -> dict:
    """Mark a PENDING message rejected, with an optional reason."""
    store = _resolve_store(store)
    reviewer = _require_text(reviewer, "reviewer")
    record = _get_pending_or_raise(store, message_id, "reject")
    changes = {"status": REJECTED, "reviewer": reviewer, "reviewed_at": _now(), "review_note": reason}
    return _update_or_raise(store, record["message_id"], changes, "reject")


# --- Tests --------------------------------------------------------------------
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


def _build_drafts(n: int) -> tuple[list[dict], list[tuple[dict, dict]]]:
    customers = load_customers()
    drafts = []
    for c in customers:
        a = analyze_lead(c)
        s = calculate_recovery_score(c, a)
        st = choose_recovery_strategy(c, a, s)
        if st["strategy"] == NO_CONTACT:
            continue
        gen = generate_recovery_message(c, a, s, st, mode="mock")
        if gen["generation_status"] == "mock":
            drafts.append((c, gen))
        if len(drafts) == n:
            return customers, drafts
    sys.exit(f"Need {n} contactable customers for the tests, found {len(drafts)}.")


def run_tests() -> None:
    customers, drafts = _build_drafts(3)
    (cust_a, gen_a), (cust_b, gen_b), (cust_c, gen_c) = drafts
    store = InMemoryApprovalStore()

    def lookup(cid):  # current customer state, nobody opted out
        return next((c for c in customers if c["id"] == cid), None)

    print("TEST 1: create a pending message")
    req_a = create_approval_request(cust_a, gen_a, store=store)
    _ok("status is 'pending'", req_a["status"] == PENDING)
    _ok("reviewed_at and reviewer are empty", req_a["reviewed_at"] is None and req_a["reviewer"] is None)
    _ok("appears in get_pending_approvals()", [r["message_id"] for r in get_pending_approvals(store=store)] == [req_a["message_id"]])
    _expect(DuplicateRequestError, "second pending request for same customer is refused",
            create_approval_request, cust_a, gen_a, store=store)
    skipped = generate_recovery_message({**cust_a, "opted_out": True}, {}, {}, {"strategy": "Friendly follow-up"}, mode="mock")
    _expect(InvalidInputError, "skipped/failed generation results are refused",
            create_approval_request, cust_b, skipped, store=store)
    _expect(OptedOutError, "opted-out customer cannot get a request",
            create_approval_request, {**cust_b, "opted_out": True}, gen_b, store=store)

    print("\nTEST 2: edit a pending message")
    edited = edit_pending_message(req_a["message_id"], "Hi, just checking in. Happy to talk whenever suits you.", "alice", store=store)
    _ok("content changed", edited["message_content"].startswith("Hi, just checking in"))
    _ok("still pending, original preserved", edited["status"] == PENDING and edited["original_message"] == gen_a["message"])
    _expect(InvalidInputError, "empty edit is refused", edit_pending_message, req_a["message_id"], "   ", store=store)
    _expect(ApprovalNotFoundError, "unknown message id", edit_pending_message, "does-not-exist", "text", store=store)

    print("\nTEST 3: approve a message (uses the default customer lookup)")
    approved = approve_message(req_a["message_id"], "alice", store=store)
    _ok("status is 'approved'", approved["status"] == APPROVED)
    _ok("reviewer and reviewed_at set", approved["reviewer"] == "alice" and approved["reviewed_at"] is not None)
    _ok("no longer pending", get_pending_approvals(store=store) == [])
    _expect(InvalidStatusError, "approving it again is blocked (duplicate)", approve_message, req_a["message_id"], "bob", store=store)
    _expect(InvalidStatusError, "editing an approved message is blocked", edit_pending_message, req_a["message_id"], "new text", store=store)
    _expect(InvalidInputError, "reviewer is required", approve_message, req_a["message_id"], "", store=store)

    print("\nTEST 4: reject a message")
    req_b = create_approval_request(cust_b, gen_b, store=store)
    rejected = reject_message(req_b["message_id"], "bob", "Tone is too pushy", store=store)
    _ok("status is 'rejected'", rejected["status"] == REJECTED)
    _ok("reviewer, reviewed_at, reason set",
        rejected["reviewer"] == "bob" and rejected["reviewed_at"] and rejected["review_note"] == "Tone is too pushy")

    print("\nTEST 5: operations on an already rejected message")
    _expect(InvalidStatusError, "approve a rejected message", approve_message, req_b["message_id"], "alice", customer_lookup=lookup, store=store)
    _expect(InvalidStatusError, "reject a rejected message", reject_message, req_b["message_id"], "alice", store=store)
    _expect(InvalidStatusError, "edit a rejected message", edit_pending_message, req_b["message_id"], "text", store=store)

    print("\nTEST 6: approval cannot bypass opt-out")
    req_c = create_approval_request(cust_c, gen_c, store=store)
    opted_out_lookup = lambda cid: {**cust_c, "opted_out": True}  # customer opted out AFTER the draft was created
    _expect(OptedOutError, "approve blocked after customer opted out",
            approve_message, req_c["message_id"], "alice", customer_lookup=opted_out_lookup, store=store)
    _ok("message left untouched (still pending)", get_approval(req_c["message_id"], store=store)["status"] == PENDING)
    _expect(CustomerNotFoundError, "approve blocked when customer cannot be found",
            approve_message, req_c["message_id"], "alice", customer_lookup=lambda cid: None, store=store)
    _expect(OptedOutError, "approve blocked on unrecognized opted_out value (fails safe)",
            approve_message, req_c["message_id"], "alice", customer_lookup=lambda cid: {**cust_c, "opted_out": "garbage"}, store=store)
    reject_message(req_c["message_id"], "alice", "Customer opted out", store=store)
    _ok("opted-out draft can still be rejected", get_approval(req_c["message_id"], store=store)["status"] == REJECTED)

    print("\nTEST 7: returned records are copies")
    snapshot = get_approval(req_a["message_id"], store=store)
    snapshot["status"] = PENDING
    _ok("mutating a returned record does not change the store", get_approval(req_a["message_id"], store=store)["status"] == APPROVED)

    print("\nTEST 8: 10 reviewers approve the same message at once")
    req_c2 = create_approval_request(cust_c, gen_c, store=store)
    outcomes: list[str] = []

    def worker(i: int) -> None:
        try:
            approve_message(req_c2["message_id"], f"reviewer_{i}", customer_lookup=lookup, store=store)
            outcomes.append("approved")
        except InvalidStatusError:
            outcomes.append("blocked")

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _ok(f"exactly 1 approved, 9 blocked (got {outcomes.count('approved')} / {outcomes.count('blocked')})",
        outcomes.count("approved") == 1 and outcomes.count("blocked") == 9)

    print("\nAll approval tests passed.")


if __name__ == "__main__":
    run_tests()