"""
crm_integration.py
CRM integration layer: connects with CRM providers (initially MockCRMProvider, extensible to HubSpot).
Handles fetching, syncing, updating lead statuses, and adding recovery notes while maintaining
stable internal customer IDs, external CRM IDs, and preventing duplicates.
"""

import re
import sys
import threading
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

# --- Errors (stable .code values) ---
class CRMError(Exception):
    code = "crm_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class InvalidInputError(CRMError):
    code = "invalid_input"


class LeadNotFoundError(CRMError):
    code = "lead_not_found"


class ProviderError(CRMError):
    code = "provider_error"


# --- Provider Interface ---
class CRMProvider(ABC):
    name: str = "base"

    @abstractmethod
    def fetch_leads(self) -> list[dict]:
        """Fetch raw lead records from the external CRM."""
        pass

    @abstractmethod
    def get_lead(self, external_id: str) -> dict | None:
        """Fetch a single lead by its external CRM ID."""
        pass

    @abstractmethod
    def update_lead_status(self, external_id: str, status: str) -> dict:
        """Update lead status in the external CRM."""
        pass

    @abstractmethod
    def add_recovery_note(self, external_id: str, note: str) -> dict:
        """Add a recovery note to the lead in the external CRM."""
        pass


class MockCRMProvider(CRMProvider):
    """Simulates a CRM provider (e.g., HubSpot) in memory."""

    name = "mock_crm"

    def __init__(self, initial_leads: list[dict] | None = None, fail_next: int = 0) -> None:
        self._leads: dict[str, dict] = {}
        self._fail_next = fail_next
        self._lock = threading.Lock()
        
        # Default synthetic leads matching existing customer format
        defaults = initial_leads or [
            {
                "crm_id": "hubspot-101",
                "name": "Alice Smith",
                "email": "alice@example.com",
                "status": "inactive",
                "total_spent": 1200.0,
                "last_order_date": "2025-11-01",
                "notes": []
            },
            {
                "crm_id": "hubspot-102",
                "name": "Bob Jones",
                "email": "bob@example.com",
                "status": "active",
                "total_spent": 450.0,
                "last_order_date": "2026-04-15",
                "notes": []
            },
            {
                "crm_id": "hubspot-103",
                "name": "Charlie Brown",
                "email": "charlie@example.com",
                "status": "inactive",
                "total_spent": 2500.0,
                "last_order_date": "2025-08-20",
                "notes": []
            }
        ]
        for lead in defaults:
            ext_id = lead.get("crm_id") or f"mock-{uuid.uuid4().hex[:8]}"
            self._leads[ext_id] = {**lead, "crm_id": ext_id}

    def fetch_leads(self) -> list[dict]:
        with self._lock:
            if self._fail_next > 0:
                self._fail_next -= 1
                raise ProviderError("Simulated CRM provider failure.")
            return [dict(l) for l in self._leads.values()]

    def get_lead(self, external_id: str) -> dict | None:
        with self._lock:
            lead = self._leads.get(external_id)
            return dict(lead) if lead else None

    def update_lead_status(self, external_id: str, status: str) -> dict:
        with self._lock:
            if external_id not in self._leads:
                raise LeadNotFoundError(f"Lead with external ID {external_id!r} not found in CRM.")
            self._leads[external_id]["status"] = status
            self._leads[external_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            return dict(self._leads[external_id])

    def add_recovery_note(self, external_id: str, note: str) -> dict:
        with self._lock:
            if external_id not in self._leads:
                raise LeadNotFoundError(f"Lead with external ID {external_id!r} not found in CRM.")
            if "notes" not in self._leads[external_id]:
                self._leads[external_id]["notes"] = []
            timestamped_note = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "note": note
            }
            self._leads[external_id]["notes"].append(timestamped_note)
            self._leads[external_id]["updated_at"] = datetime.now(timezone.utc).isoformat()
            return dict(self._leads[external_id])


# --- Local CRM Store & Synchronization Management ---
class LocalCRMStore:
    """In-memory store managing local customer records synchronized from CRM providers."""
    def __init__(self) -> None:
        self._records: dict[str, dict] = {}  # keyed by internal customer 'id'
        self._email_index: dict[str, str] = {}  # email -> internal 'id'
        self._crm_index: dict[str, str] = {}  # external 'crm_id' -> internal 'id'
        self._lock = threading.Lock()

    def get_by_id(self, customer_id: str) -> dict | None:
        with self._lock:
            rec = self._records.get(customer_id)
            return dict(rec) if rec else None

    def get_by_email(self, email: str) -> dict | None:
        with self._lock:
            cid = self._email_index.get(email.lower().strip())
            return dict(self._records[cid]) if cid else None

    def get_by_crm_id(self, crm_id: str) -> dict | None:
        with self._lock:
            cid = self._crm_index.get(crm_id)
            return dict(self._records[cid]) if cid else None

    def list_all(self) -> list[dict]:
        with self._lock:
            return [dict(r) for r in self._records.values()]

    def upsert(self, customer_record: dict) -> dict:
        with self._lock:
            cid = customer_record.get("id")
            email = customer_record.get("email")
            crm_id = customer_record.get("crm_id")

            if not cid:
                raise InvalidInputError("Customer record must contain an 'id'.")

            # Check existing by ID, CRM ID, or Email to prevent duplicates
            existing_id = cid
            if cid not in self._records:
                if crm_id and crm_id in self._crm_index:
                    existing_id = self._crm_index[crm_id]
                elif email and email.lower().strip() in self._email_index:
                    existing_id = self._email_index[email.lower().strip()]

            now = datetime.now(timezone.utc).isoformat()
            existing = self._records.get(existing_id)

            if existing:
                # Preserve existing internal ID and merge fields without overwriting valid data with blanks
                merged = dict(existing)
                for k, v in customer_record.items():
                    if v is not None and v != "":
                        merged[k] = v
                merged["updated_at"] = now
                self._records[existing_id] = merged
                if email:
                    self._email_index[email.lower().strip()] = existing_id
                if crm_id:
                    self._crm_index[crm_id] = existing_id
                return dict(merged)
            else:
                new_rec = dict(customer_record)
                new_rec["id"] = existing_id
                new_rec["created_at"] = now
                new_rec["updated_at"] = now
                self._records[existing_id] = new_rec
                if email:
                    self._email_index[email.lower().strip()] = existing_id
                if crm_id:
                    self._crm_index[crm_id] = existing_id
                return dict(new_rec)


_default_crm_provider = MockCRMProvider()
_default_crm_store = LocalCRMStore()


# --- Public API Functions ---
def fetch_leads(provider: CRMProvider | None = None) -> list[dict]:
    """Fetch raw lead records from the configured CRM provider."""
    provider = provider or _default_crm_provider
    return provider.fetch_leads()


def get_lead(identifier: str, *, store: LocalCRMStore | None = None, provider: CRMProvider | None = None) -> dict:
    """
    Retrieve a lead by internal customer ID, external CRM ID, or email address.
    """
    store = store or _default_crm_store
    provider = provider or _default_crm_provider

    # Try local store first
    rec = store.get_by_id(identifier) or store.get_by_crm_id(identifier) or store.get_by_email(identifier)
    if rec:
        return rec

    # If not found locally, check provider by external CRM ID
    ext_lead = provider.get_lead(identifier)
    if ext_lead:
        return sync_lead_record(ext_lead, store=store)

    raise LeadNotFoundError(f"Lead with identifier {identifier!r} not found.")


def update_lead_status(identifier: str, status: str, *, store: LocalCRMStore | None = None, provider: CRMProvider | None = None) -> dict:
    """
    Update a lead's status both locally and in the CRM provider.
    """
    store = store or _default_crm_store
    provider = provider or _default_crm_provider

    rec = store.get_by_id(identifier) or store.get_by_crm_id(identifier) or store.get_by_email(identifier)
    if not rec:
        ext_lead = provider.get_lead(identifier)
        if ext_lead:
            rec = sync_lead_record(ext_lead, store=store)
        else:
            raise LeadNotFoundError(f"Lead with identifier {identifier!r} not found.")

    crm_id = rec.get("crm_id")
    if not crm_id:
        raise InvalidInputError(f"Lead {identifier!r} has no associated external crm_id.")

    updated_ext = provider.update_lead_status(crm_id, status)
    
    rec["status"] = status
    rec["updated_at"] = updated_ext.get("updated_at", datetime.now(timezone.utc).isoformat())
    return store.upsert(rec)


def add_recovery_note(identifier: str, note: str, *, store: LocalCRMStore | None = None, provider: CRMProvider | None = None) -> dict:
    """
    Add a recovery note to a lead both locally and in the CRM provider.
    """
    store = store or _default_crm_store
    provider = provider or _default_crm_provider

    rec = store.get_by_id(identifier) or store.get_by_crm_id(identifier) or store.get_by_email(identifier)
    if not rec:
        ext_lead = provider.get_lead(identifier)
        if ext_lead:
            rec = sync_lead_record(ext_lead, store=store)
        else:
            raise LeadNotFoundError(f"Lead with identifier {identifier!r} not found.")

    crm_id = rec.get("crm_id")
    if not crm_id:
        raise InvalidInputError(f"Lead {identifier!r} has no associated external crm_id.")

    updated_ext = provider.add_recovery_note(crm_id, note)
    
    if "notes" not in rec:
        rec["notes"] = []
    rec["notes"] = updated_ext.get("notes", rec["notes"])
    rec["updated_at"] = updated_ext.get("updated_at", datetime.now(timezone.utc).isoformat())
    return store.upsert(rec)


def sync_lead_record(ext_lead: dict, *, store: LocalCRMStore | None = None) -> dict:
    """Helper to convert and upsert an external CRM lead into the local store format."""
    store = store or _default_crm_store
    crm_id = ext_lead.get("crm_id")
    if not crm_id:
        raise InvalidInputError("External lead missing 'crm_id'.")

    email = ext_lead.get("email")
    # Generate stable internal ID
    internal_id = f"cust-{uuid.uuid5(uuid.NAMESPACE_DNS, email or crm_id).hex[:8]}"

    customer_record = {
        "id": internal_id,
        "crm_id": crm_id,
        "name": ext_lead.get("name", "Unknown"),
        "email": email,
        "status": ext_lead.get("status", "active"),
        "total_spent": ext_lead.get("total_spent", 0.0),
        "last_order_date": ext_lead.get("last_order_date"),
        "notes": ext_lead.get("notes", []),
        "opted_out": ext_lead.get("opted_out", False)
    }
    return store.upsert(customer_record)


def sync_leads(provider: CRMProvider | None = None, store: LocalCRMStore | None = None) -> dict:
    """
    Fetch all leads from the provider and synchronize them into the local store.
    Prevents duplicates, preserves existing info, tracks external CRM ID separately,
    and adds synchronization timestamps.
    """
    provider = provider or _default_crm_provider
    store = store or _default_crm_store

    raw_leads = provider.fetch_leads()
    synced_records = []
    sync_time = datetime.now(timezone.utc).isoformat()

    for ext_lead in raw_leads:
        rec = sync_lead_record(ext_lead, store=store)
        rec["synced_at"] = sync_time
        store.upsert(rec)
        synced_records.append(rec)

    return {
        "sync_status": "success",
        "synced_count": len(synced_records),
        "synced_at": sync_time,
        "records": synced_records
    }


# --- Testing Suite ---
def _ok(label: str, condition: bool) -> None:
    assert condition, f"FAILED: {label}"
    print(f"  PASS {label}")


def _expect(exc_type, label: str, fn, *args, **kwargs) -> None:
    try:
        fn(*args, **kwargs)
    except exc_type as exc:
        print(f"  PASS {label}\n        -> [{exc.code}] {exc.message}")
        return
    except Exception as exc:
        raise AssertionError(f"{label}: expected {exc_type.__name__}, got {type(exc).__name__}: {exc}")
    raise AssertionError(f"{label}: expected {exc_type.__name__} but no error was raised")


def run_tests() -> None:
    print("TESTING crm_integration.py")
    
    crm_provider = MockCRMProvider()
    crm_store = LocalCRMStore()

    print("\n1. Initial sync of synthetic leads")
    sync_result = sync_leads(provider=crm_provider, store=crm_store)
    _ok("sync successful", sync_result["sync_status"] == "success" and sync_result["synced_count"] == 3)
    _ok("synced_at timestamp present", bool(sync_result["synced_at"]))

    print("\n2. Retrieve a lead (by internal ID, CRM ID, and email)")
    all_leads = crm_store.list_all()
    sample_lead = all_leads[0]
    internal_id = sample_lead["id"]
    crm_id = sample_lead["crm_id"]
    email = sample_lead["email"]

    retrieved_by_id = get_lead(internal_id, store=crm_store, provider=crm_provider)
    retrieved_by_crm = get_lead(crm_id, store=crm_store, provider=crm_provider)
    retrieved_by_email = get_lead(email, store=crm_store, provider=crm_provider)

    _ok("retrieve by internal ID matches", retrieved_by_id["id"] == internal_id)
    _ok("retrieve by crm_id matches", retrieved_by_crm["crm_id"] == crm_id)
    _ok("retrieve by email matches", retrieved_by_email["email"] == email)
    _ok("external crm_id tracked separately from internal id", retrieved_by_id["id"] != retrieved_by_id["crm_id"])

    print("\n3. Update lead status")
    updated = update_lead_status(internal_id, "contacted", store=crm_store, provider=crm_provider)
    _ok("status updated to 'contacted'", updated["status"] == "contacted")
    _ok("updated_at timestamp updated", bool(updated["updated_at"]))

    print("\n4. Add a recovery note")
    noted = add_recovery_note(internal_id, "Sent win-back email with 20% discount.", store=crm_store, provider=crm_provider)
    _ok("note added successfully", len(noted["notes"]) == 1)
    _ok("note text matches", noted["notes"][0]["note"] == "Sent win-back email with 20% discount.")
    _ok("note timestamp present", bool(noted["notes"][0]["timestamp"]))

    print("\n5. Sync the same leads again without creating duplicates")
    initial_count = len(crm_store.list_all())
    resync_result = sync_leads(provider=crm_provider, store=crm_store)
    final_count = len(crm_store.list_all())
    _ok("no duplicate records created upon re-sync", initial_count == final_count)
    
    re_retrieved = get_lead(internal_id, store=crm_store, provider=crm_provider)
    _ok("status 'contacted' preserved after re-sync", re_retrieved["status"] == "contacted")
    _ok("recovery notes preserved after re-sync", len(re_retrieved["notes"]) == 1)

    print("\n6. Error handling for missing leads")
    _expect(LeadNotFoundError, "getting non-existent lead raises LeadNotFoundError", get_lead, "non-existent-id", store=crm_store, provider=crm_provider)
    _expect(LeadNotFoundError, "updating status for non-existent lead raises LeadNotFoundError", update_lead_status, "non-existent-id", "active", store=crm_store, provider=crm_provider)

    print("\nAll CRM integration tests passed successfully.")


if __name__ == "__main__":
    run_tests()