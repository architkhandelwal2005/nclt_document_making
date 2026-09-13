"""SQLite persistence for the Casefile practice-management application.

The module deliberately uses Python's standard-library sqlite3 driver.  The
application is local-first, so this avoids a database service while retaining
foreign keys, transactions, migrations, audit history, and a clean future
migration path to PostgreSQL.
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional
import json
import shutil
import sqlite3
import uuid


SCHEMA_VERSION = 16

FULL_CASE_ACCESS_ROLES = {"admin", "administrator", "professional"}

# API module names are explicitly mapped to tables and writable columns.  This
# allow-list prevents clients from selecting arbitrary SQL tables or audit
# columns while keeping the operational modules consistent.
MODULE_FIELDS: Dict[str, tuple[str, ...]] = {
    "tasks": ("title", "description", "category", "assignee_id", "reviewer_id", "priority", "status", "start_date", "due_date", "completed_at", "recurrence", "source_type", "source_id", "checklist_json"),
    "deadlines": ("rule_id", "title", "provision", "trigger_date", "calculated_date", "due_date", "override_reason", "status", "responsible_user_id", "completed_at", "evidence_document_id"),
    "hearings": ("tribunal", "bench", "hearing_at", "purpose", "counsel", "venue_or_link", "status", "next_hearing_at", "notes"),
    "applications": ("application_type", "number", "filing_date", "parties", "relief_sought", "status", "defects", "disposal_date"),
    "orders": ("hearing_id", "application_id", "order_date", "summary", "document_id"),
    "order-directions": ("order_id", "direction", "due_date", "responsible_user_id", "task_id", "status"),
    "claims": (
        "claimant_contact_id", "claim_number", "creditor_name", "contact_person", "email", "phone", "address",
        "creditor_category", "related_party_status", "form_type", "received_date", "received_via", "sender_email",
        "sender_name", "source_email_id", "email_subject", "source_notes", "currency", "principal_claimed",
        "interest_claimed", "other_amount_claimed", "claimed_amount", "calculated_component_total",
        "principal_admitted", "interest_admitted", "other_amount_admitted", "admitted_amount", "amount_not_admitted",
        "security_details", "secured_status", "security_value", "charge_details", "date_debt_incurred", "due_date",
        "default_date", "interest_rate", "interest_basis", "status", "deficiency_notes", "verification_notes",
        "issues_identified", "documents_checked", "decision_date", "decision_by", "decision_reason", "revision",
        "parent_claim_id", "idempotency_key", "override_reason", "claim_submission_date", "claim_as_on_date",
        "claim_reference", "creditor_identifier", "authorized_representative",
        "authorized_representative_designation", "nature_of_debt", "basis_of_claim", "facility_type",
        "original_facility_amount", "sanction_letter_reference", "sanction_date", "agreement_date", "bank_details",
        "supporting_evidence_json", "reconciliation_json",
        "acknowledgement_status", "acknowledged_at", "acknowledged_by", "classification_status",
        "classification_confirmed_at", "classification_confirmed_by", "scrutiny_status", "verification_status",
        "verification_started_at", "verification_completed_at", "verification_completed_by", "late_flag",
        "claim_deadline_date", "days_after_deadline", "late_review_status", "late_review_reason",
        "late_reviewed_by", "late_reviewed_at", "principal_claimed_paise", "interest_claimed_paise",
        "other_claimed_paise", "total_claimed_paise", "principal_admitted_paise",
        "interest_admitted_paise", "other_admitted_paise", "total_admitted_paise",
        "amount_not_admitted_paise",
    ),
    "claim-documents": ("claim_id", "name", "required", "received", "document_id", "notes"),
    "coc-members": ("claim_id", "contact_id", "admitted_debt", "voting_share", "valid_from", "valid_to", "authorized_representative"),
    "coc-meetings": ("meeting_number", "meeting_type", "coc_constitution_id", "meeting_at", "scheduled_start_at", "scheduled_end_at", "actual_start_at", "actual_end_at", "mode", "venue_or_link", "notice_date", "notice_place", "notice_due_date", "meeting_due_date", "voting_start", "voting_end", "status", "agenda_json", "notice_snapshot_json", "minutes", "quorum_threshold", "chair_name", "membership_review_required", "signed_at"),
    "coc-agenda-items": ("meeting_id", "position", "section", "title", "notes", "discussion", "minutes_text", "minutes_disposition", "decision", "proposed_resolution", "resolution_text", "voting_required", "status"),
    "coc-attendance": ("meeting_id", "member_id", "meeting_member_snapshot_id", "participant_name", "organization", "capacity", "participant_role", "email", "present", "attendance_mode", "authorization_status", "authorization_document_id", "voting_entitled", "joined_at", "left_at", "voting_share_snapshot", "voting_share_units", "notes"),
    "coc-votes": ("meeting_id", "member_id", "meeting_member_snapshot_id", "resolution_id", "agenda_key", "vote", "voting_share", "voting_share_units", "method", "source", "remarks", "cast_at"),
    "documents": ("template_id", "name", "category", "status", "version", "parent_document_id", "storage_path", "mime_type", "source_type", "linked_type", "linked_id", "confidentiality_classification", "metadata_json"),
    "communications": ("channel", "direction", "occurred_at", "sender", "recipients", "subject", "summary", "delivery_status", "proof_document_id", "linked_type", "linked_id", "follow_up_task_id"),
    "assets": ("category", "description", "ownership", "location", "book_value", "security_interest", "possession", "insurance", "encumbrance", "status"),
    "financial-records": ("record_type", "name", "amount", "as_of_date", "details_json"),
    "valuations": ("asset_id", "valuer_contact_id", "asset_class", "appointment_date", "inspection_date", "report_date", "fair_value", "liquidation_value", "status", "confidential", "notes"),
    "expenses": ("category", "vendor_contact_id", "invoice_number", "expense_date", "amount", "tax_amount", "approval_status", "payment_status", "cirp_cost_eligible", "proof_document_id"),
    "contributions": ("coc_member_id", "called_amount", "called_amount_paise", "due_date", "paid_amount", "paid_date", "payer_contact_id", "payer_name", "purpose", "direction_date", "status", "source_type", "source_id", "notes"),
    "document-requirements": ("name", "category", "status", "source_document_id", "source_intake_id", "notes"),
    "payment-evidence": ("instrument_type", "instrument_number", "instrument_date", "amount_paise", "bank_name", "branch_name", "presented_date", "presented_through", "return_date", "return_reason", "source_document_id", "source_intake_id", "notes"),
}

MODULE_TABLES = {name: name.replace("-", "_") for name in MODULE_FIELDS}


class ClosingConnection(sqlite3.Connection):
    """Make ``with connection`` close the Windows file handle as well as commit."""

    def __exit__(self, exc_type, exc_value, traceback):
        try:
            return super().__exit__(exc_type, exc_value, traceback)
        finally:
            self.close()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def new_id() -> str:
    return str(uuid.uuid4())


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _from_json(value: Optional[str], fallback: Any) -> Any:
    if not value:
        return fallback
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return fallback


class CasefileDatabase:
    """Small repository layer with one connection per operation."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.initialize()

    def connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=30, factory=ClosingConnection)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 30000")
        return connection

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self.connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def initialize(self) -> None:
        with self.transaction() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    version INTEGER PRIMARY KEY,
                    applied_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS profiles (
                    user_id TEXT PRIMARY KEY,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    name TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'staff',
                    password_hash TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS cases (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    internal_reference TEXT NOT NULL DEFAULT '',
                    cin TEXT NOT NULL DEFAULT '',
                    registered_address TEXT NOT NULL DEFAULT '',
                    registered_email TEXT NOT NULL DEFAULT '',
                    industry TEXT NOT NULL DEFAULT '',
                    nclt_bench TEXT NOT NULL DEFAULT '',
                    petition_number TEXT NOT NULL DEFAULT '',
                    applicant_name TEXT NOT NULL DEFAULT '',
                    applicant_category TEXT NOT NULL DEFAULT '',
                    process_type TEXT NOT NULL DEFAULT 'CIRP',
                    order_date TEXT,
                    commencement_date TEXT,
                    status TEXT NOT NULL DEFAULT 'active',
                    current_stage TEXT NOT NULL DEFAULT '',
                    closure_date TEXT,
                    outcome TEXT NOT NULL DEFAULT '',
                    assigned_user_id TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    risk_level TEXT NOT NULL DEFAULT 'normal',
                    legacy_values_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_cases_status ON cases(status, archived_at);

                CREATE TABLE IF NOT EXISTS case_user_assignments (
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                    assigned_by TEXT NOT NULL,
                    assigned_at TEXT NOT NULL,
                    PRIMARY KEY(case_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS ix_case_assignments_user ON case_user_assignments(user_id, case_id);

                CREATE TABLE IF NOT EXISTS contacts (
                    id TEXT PRIMARY KEY,
                    kind TEXT NOT NULL DEFAULT 'person',
                    name TEXT NOT NULL,
                    organization TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    phone TEXT NOT NULL DEFAULT '',
                    address TEXT NOT NULL DEFAULT '',
                    identifiers_json TEXT NOT NULL DEFAULT '{}',
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS case_contacts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    contact_id TEXT NOT NULL REFERENCES contacts(id),
                    role TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    communication_preference TEXT NOT NULL DEFAULT '',
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_case_contacts_case ON case_contacts(case_id, archived_at);

                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    category TEXT NOT NULL DEFAULT '',
                    assignee_id TEXT NOT NULL DEFAULT '',
                    reviewer_id TEXT NOT NULL DEFAULT '',
                    priority TEXT NOT NULL DEFAULT 'normal',
                    status TEXT NOT NULL DEFAULT 'open',
                    start_date TEXT,
                    due_date TEXT,
                    completed_at TEXT,
                    recurrence TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'manual',
                    source_id TEXT NOT NULL DEFAULT '',
                    checklist_json TEXT NOT NULL DEFAULT '[]',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_tasks_case_due ON tasks(case_id, due_date, status, archived_at);

                CREATE TABLE IF NOT EXISTS task_dependencies (
                    task_id TEXT NOT NULL REFERENCES tasks(id),
                    depends_on_task_id TEXT NOT NULL REFERENCES tasks(id),
                    PRIMARY KEY(task_id, depends_on_task_id),
                    CHECK(task_id <> depends_on_task_id)
                );

                CREATE TABLE IF NOT EXISTS compliance_rules (
                    id TEXT PRIMARY KEY,
                    process_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    provision TEXT NOT NULL DEFAULT '',
                    trigger_event TEXT NOT NULL DEFAULT 'commencement_date',
                    offset_days INTEGER NOT NULL DEFAULT 0,
                    day_method TEXT NOT NULL DEFAULT 'calendar',
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    is_conditional INTEGER NOT NULL DEFAULT 0,
                    evidence_required TEXT NOT NULL DEFAULT '',
                    source_reference TEXT NOT NULL DEFAULT '',
                    version INTEGER NOT NULL DEFAULT 1,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS deadlines (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    rule_id TEXT REFERENCES compliance_rules(id),
                    title TEXT NOT NULL,
                    provision TEXT NOT NULL DEFAULT '',
                    trigger_date TEXT,
                    calculated_date TEXT,
                    due_date TEXT,
                    override_reason TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'open',
                    responsible_user_id TEXT NOT NULL DEFAULT '',
                    completed_at TEXT,
                    evidence_document_id TEXT,
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_deadlines_case_due ON deadlines(case_id, due_date, status, archived_at);

                CREATE TABLE IF NOT EXISTS hearings (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    tribunal TEXT NOT NULL DEFAULT 'NCLT',
                    bench TEXT NOT NULL DEFAULT '',
                    hearing_at TEXT NOT NULL,
                    purpose TEXT NOT NULL DEFAULT '',
                    counsel TEXT NOT NULL DEFAULT '',
                    venue_or_link TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'scheduled',
                    next_hearing_at TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS applications (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    application_type TEXT NOT NULL,
                    number TEXT NOT NULL DEFAULT '',
                    filing_date TEXT,
                    parties TEXT NOT NULL DEFAULT '',
                    relief_sought TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    defects TEXT NOT NULL DEFAULT '',
                    disposal_date TEXT,
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS orders (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    hearing_id TEXT REFERENCES hearings(id),
                    application_id TEXT REFERENCES applications(id),
                    order_date TEXT NOT NULL,
                    summary TEXT NOT NULL DEFAULT '',
                    document_id TEXT,
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS order_directions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    order_id TEXT NOT NULL REFERENCES orders(id),
                    direction TEXT NOT NULL,
                    due_date TEXT,
                    responsible_user_id TEXT NOT NULL DEFAULT '',
                    task_id TEXT REFERENCES tasks(id),
                    status TEXT NOT NULL DEFAULT 'open',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claims (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claimant_contact_id TEXT REFERENCES contacts(id),
                    creditor_category TEXT NOT NULL,
                    form_type TEXT NOT NULL DEFAULT '',
                    received_date TEXT,
                    claimed_amount REAL NOT NULL DEFAULT 0,
                    admitted_amount REAL NOT NULL DEFAULT 0,
                    security_details TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'received',
                    deficiency_notes TEXT NOT NULL DEFAULT '',
                    decision_reason TEXT NOT NULL DEFAULT '',
                    revision INTEGER NOT NULL DEFAULT 1,
                    parent_claim_id TEXT REFERENCES claims(id),
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claims_case_status ON claims(case_id, status, archived_at);

                CREATE TABLE IF NOT EXISTS claim_documents (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    name TEXT NOT NULL,
                    required INTEGER NOT NULL DEFAULT 0,
                    received INTEGER NOT NULL DEFAULT 0,
                    document_id TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_checklist_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    label TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'MISSING',
                    document_id TEXT REFERENCES documents(id),
                    notes TEXT NOT NULL DEFAULT '',
                    position INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_queries (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    query_date TEXT NOT NULL,
                    subject TEXT NOT NULL,
                    query_text TEXT NOT NULL,
                    information_requested TEXT NOT NULL DEFAULT '',
                    sent_to TEXT NOT NULL DEFAULT '',
                    response_due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_query_responses (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    query_id TEXT NOT NULL REFERENCES claim_queries(id),
                    response_received_date TEXT NOT NULL,
                    response_notes TEXT NOT NULL DEFAULT '',
                    source_reference TEXT NOT NULL DEFAULT '',
                    document_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_decisions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    claim_revision INTEGER NOT NULL,
                    decision_date TEXT NOT NULL,
                    decision_by TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    principal_admitted REAL NOT NULL DEFAULT 0,
                    interest_admitted REAL NOT NULL DEFAULT 0,
                    other_amount_admitted REAL NOT NULL DEFAULT 0,
                    admitted_amount REAL NOT NULL DEFAULT 0,
                    amount_not_admitted REAL NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    override_reason TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS claim_revisions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    revision INTEGER NOT NULL,
                    snapshot_json TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    document_ids_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(claim_id, revision)
                );

                CREATE INDEX IF NOT EXISTS ix_claim_checklist_claim ON claim_checklist_items(case_id, claim_id, position);
                CREATE INDEX IF NOT EXISTS ix_claim_queries_claim ON claim_queries(case_id, claim_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_claim_responses_query ON claim_query_responses(case_id, claim_id, query_id);
                CREATE INDEX IF NOT EXISTS ix_claim_decisions_claim ON claim_decisions(case_id, claim_id, created_at);
                CREATE INDEX IF NOT EXISTS ix_claim_revisions_claim ON claim_revisions(case_id, claim_id, revision);

                CREATE TABLE IF NOT EXISTS coc_members (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT REFERENCES claims(id),
                    contact_id TEXT REFERENCES contacts(id),
                    admitted_debt REAL NOT NULL DEFAULT 0,
                    voting_share REAL NOT NULL DEFAULT 0,
                    valid_from TEXT NOT NULL,
                    valid_to TEXT,
                    authorized_representative TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS coc_meetings (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_number INTEGER NOT NULL,
                    meeting_at TEXT NOT NULL,
                    mode TEXT NOT NULL DEFAULT '',
                    venue_or_link TEXT NOT NULL DEFAULT '',
                    notice_date TEXT,
                    voting_start TEXT,
                    voting_end TEXT,
                    status TEXT NOT NULL DEFAULT 'planned',
                    agenda_json TEXT NOT NULL DEFAULT '[]',
                    notice_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    minutes TEXT NOT NULL DEFAULT '',
                    actual_start_at TEXT,
                    actual_end_at TEXT,
                    notice_place TEXT NOT NULL DEFAULT '',
                    quorum_threshold REAL NOT NULL DEFAULT 33,
                    chair_name TEXT NOT NULL DEFAULT '',
                    signed_at TEXT,
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS coc_agenda_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id),
                    position INTEGER NOT NULL,
                    section TEXT NOT NULL DEFAULT 'discussion',
                    title TEXT NOT NULL,
                    notes TEXT NOT NULL DEFAULT '',
                    discussion TEXT NOT NULL DEFAULT '',
                    decision TEXT NOT NULL DEFAULT '',
                    proposed_resolution TEXT NOT NULL DEFAULT '',
                    resolution_text TEXT NOT NULL DEFAULT '',
                    voting_required INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'draft',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, position)
                );
                CREATE INDEX IF NOT EXISTS ix_coc_agenda_meeting ON coc_agenda_items(meeting_id, position, archived_at);

                CREATE TABLE IF NOT EXISTS coc_attendance (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id),
                    member_id TEXT REFERENCES coc_members(id),
                    participant_name TEXT NOT NULL,
                    organization TEXT NOT NULL DEFAULT '',
                    capacity TEXT NOT NULL DEFAULT '',
                    email TEXT NOT NULL DEFAULT '',
                    present INTEGER NOT NULL DEFAULT 1,
                    attendance_mode TEXT NOT NULL DEFAULT '',
                    joined_at TEXT,
                    left_at TEXT,
                    voting_share_snapshot REAL NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_coc_attendance_meeting ON coc_attendance(meeting_id, present, archived_at);

                CREATE TABLE IF NOT EXISTS coc_votes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id),
                    member_id TEXT NOT NULL REFERENCES coc_members(id),
                    agenda_key TEXT NOT NULL,
                    vote TEXT NOT NULL,
                    voting_share REAL NOT NULL,
                    cast_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, member_id, agenda_key)
                );

                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    template_id TEXT,
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'draft',
                    version INTEGER NOT NULL DEFAULT 1,
                    parent_document_id TEXT REFERENCES documents(id),
                    storage_path TEXT NOT NULL DEFAULT '',
                    mime_type TEXT NOT NULL DEFAULT '',
                    source_type TEXT NOT NULL DEFAULT 'upload',
                    linked_type TEXT NOT NULL DEFAULT '',
                    linked_id TEXT NOT NULL DEFAULT '',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_documents_case ON documents(case_id, category, archived_at);

                CREATE TABLE IF NOT EXISTS communications (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    channel TEXT NOT NULL,
                    direction TEXT NOT NULL DEFAULT 'outgoing',
                    occurred_at TEXT NOT NULL,
                    sender TEXT NOT NULL DEFAULT '',
                    recipients TEXT NOT NULL DEFAULT '',
                    subject TEXT NOT NULL DEFAULT '',
                    summary TEXT NOT NULL DEFAULT '',
                    delivery_status TEXT NOT NULL DEFAULT '',
                    proof_document_id TEXT REFERENCES documents(id),
                    linked_type TEXT NOT NULL DEFAULT '',
                    linked_id TEXT NOT NULL DEFAULT '',
                    follow_up_task_id TEXT REFERENCES tasks(id),
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS assets (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    category TEXT NOT NULL,
                    description TEXT NOT NULL,
                    ownership TEXT NOT NULL DEFAULT '',
                    location TEXT NOT NULL DEFAULT '',
                    book_value REAL NOT NULL DEFAULT 0,
                    security_interest TEXT NOT NULL DEFAULT '',
                    possession TEXT NOT NULL DEFAULT '',
                    insurance TEXT NOT NULL DEFAULT '',
                    encumbrance TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'identified',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS financial_records (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_type TEXT NOT NULL,
                    name TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    as_of_date TEXT,
                    details_json TEXT NOT NULL DEFAULT '{}',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS valuations (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    asset_id TEXT REFERENCES assets(id),
                    valuer_contact_id TEXT REFERENCES contacts(id),
                    asset_class TEXT NOT NULL,
                    appointment_date TEXT,
                    inspection_date TEXT,
                    report_date TEXT,
                    fair_value REAL,
                    liquidation_value REAL,
                    status TEXT NOT NULL DEFAULT 'appointed',
                    confidential INTEGER NOT NULL DEFAULT 1,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS expenses (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    category TEXT NOT NULL,
                    vendor_contact_id TEXT REFERENCES contacts(id),
                    invoice_number TEXT NOT NULL DEFAULT '',
                    expense_date TEXT NOT NULL,
                    amount REAL NOT NULL DEFAULT 0,
                    tax_amount REAL NOT NULL DEFAULT 0,
                    approval_status TEXT NOT NULL DEFAULT 'pending',
                    payment_status TEXT NOT NULL DEFAULT 'unpaid',
                    cirp_cost_eligible INTEGER NOT NULL DEFAULT 0,
                    proof_document_id TEXT REFERENCES documents(id),
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS contributions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    coc_member_id TEXT REFERENCES coc_members(id),
                    called_amount REAL NOT NULL DEFAULT 0,
                    due_date TEXT,
                    paid_amount REAL NOT NULL DEFAULT 0,
                    paid_date TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS document_requirements (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    name TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT 'Referenced document',
                    status TEXT NOT NULL DEFAULT 'REFERENCED_NOT_RECEIVED',
                    source_document_id TEXT REFERENCES documents(id),
                    source_intake_id TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_document_requirements_case ON document_requirements(case_id, archived_at);

                CREATE TABLE IF NOT EXISTS payment_evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    instrument_type TEXT NOT NULL DEFAULT 'Cheque',
                    instrument_number TEXT NOT NULL DEFAULT '',
                    instrument_date TEXT,
                    amount_paise INTEGER NOT NULL DEFAULT 0,
                    bank_name TEXT NOT NULL DEFAULT '',
                    branch_name TEXT NOT NULL DEFAULT '',
                    presented_date TEXT,
                    presented_through TEXT NOT NULL DEFAULT '',
                    return_date TEXT,
                    return_reason TEXT NOT NULL DEFAULT '',
                    source_document_id TEXT REFERENCES documents(id),
                    source_intake_id TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_payment_evidence_case ON payment_evidence(case_id, archived_at);

                CREATE TABLE IF NOT EXISTS admission_order_intakes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    document_id TEXT REFERENCES documents(id),
                    status TEXT NOT NULL DEFAULT 'review',
                    original_filename TEXT NOT NULL,
                    storage_path TEXT NOT NULL,
                    mime_type TEXT NOT NULL DEFAULT 'application/pdf',
                    page_count INTEGER NOT NULL DEFAULT 0,
                    extracted_json TEXT NOT NULL DEFAULT '{}',
                    mca_json TEXT NOT NULL DEFAULT '{}',
                    review_json TEXT NOT NULL DEFAULT '{}',
                    duplicate_candidates_json TEXT NOT NULL DEFAULT '[]',
                    uploaded_by TEXT NOT NULL,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_admission_intakes_case ON admission_order_intakes(case_id, status);

                CREATE TABLE IF NOT EXISTS public_announcements (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL UNIQUE REFERENCES cases(id),
                    status TEXT NOT NULL DEFAULT 'NOT_STARTED',
                    draft_data_json TEXT NOT NULL DEFAULT '{}',
                    provenance_json TEXT NOT NULL DEFAULT '{}',
                    draft_document_id TEXT REFERENCES documents(id),
                    draft_generated_at TEXT,
                    finalised_by TEXT,
                    finalised_at TEXT,
                    sent_by TEXT,
                    sent_at TEXT,
                    published_document_id TEXT REFERENCES documents(id),
                    extraction_method TEXT NOT NULL DEFAULT '',
                    extraction_json TEXT NOT NULL DEFAULT '{}',
                    published_review_json TEXT NOT NULL DEFAULT '{}',
                    conflicts_json TEXT NOT NULL DEFAULT '[]',
                    uploaded_by TEXT,
                    uploaded_at TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_public_announcements_case_status ON public_announcements(case_id, status);

                CREATE TABLE IF NOT EXISTS nclt_fetch_runs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    case_number TEXT NOT NULL,
                    case_year INTEGER NOT NULL,
                    case_type TEXT NOT NULL,
                    case_type_label TEXT NOT NULL,
                    bench TEXT NOT NULL,
                    case_title TEXT NOT NULL DEFAULT '',
                    applicant TEXT NOT NULL DEFAULT '',
                    respondent TEXT NOT NULL DEFAULT '',
                    portal_case_identifier TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    result_json TEXT NOT NULL DEFAULT '{}',
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    debug_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    completed_at TEXT
                );
                CREATE INDEX IF NOT EXISTS ix_nclt_fetch_runs_case_time ON nclt_fetch_runs(case_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_nclt_fetch_runs_actor_time ON nclt_fetch_runs(created_by, created_at DESC);

                CREATE TABLE IF NOT EXISTS nclt_fetch_records (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES nclt_fetch_runs(id),
                    case_id TEXT REFERENCES cases(id),
                    case_number TEXT NOT NULL,
                    case_year INTEGER NOT NULL,
                    case_type TEXT NOT NULL,
                    bench TEXT NOT NULL,
                    case_title TEXT NOT NULL DEFAULT '',
                    proceeding_date TEXT,
                    purpose TEXT NOT NULL DEFAULT '',
                    next_date TEXT,
                    proceeding_status TEXT NOT NULL DEFAULT '',
                    order_type TEXT NOT NULL DEFAULT '',
                    source_url TEXT NOT NULL DEFAULT '',
                    source_identifier TEXT NOT NULL,
                    file_path TEXT NOT NULL DEFAULT '',
                    file_hash TEXT NOT NULL DEFAULT '',
                    file_size INTEGER NOT NULL DEFAULT 0,
                    document_id TEXT REFERENCES documents(id),
                    status TEXT NOT NULL,
                    first_seen_at TEXT NOT NULL,
                    last_checked_at TEXT NOT NULL,
                    downloaded_at TEXT,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_nclt_fetch_source
                    ON nclt_fetch_records(bench, case_type, case_number, case_year, source_identifier);
                CREATE INDEX IF NOT EXISTS ix_nclt_fetch_records_case_date
                    ON nclt_fetch_records(case_id, proceeding_date DESC);
                CREATE INDEX IF NOT EXISTS ix_nclt_fetch_records_hash ON nclt_fetch_records(file_hash);

                CREATE TABLE IF NOT EXISTS ai_jobs (
                    id TEXT PRIMARY KEY,
                    intake_id TEXT REFERENCES admission_order_intakes(id),
                    case_id TEXT REFERENCES cases(id),
                    document_id TEXT REFERENCES documents(id),
                    document_hash TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    model TEXT NOT NULL,
                    prompt_version TEXT NOT NULL,
                    schema_version TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_tokens INTEGER NOT NULL DEFAULT 0,
                    output_tokens INTEGER NOT NULL DEFAULT 0,
                    api_calls INTEGER NOT NULL DEFAULT 0,
                    latency_ms INTEGER NOT NULL DEFAULT 0,
                    actual_cost REAL,
                    estimated_list_cost REAL,
                    estimated_cost REAL,
                    started_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_code TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT '',
                    parsed_result_json TEXT NOT NULL DEFAULT '{}',
                    validation_json TEXT NOT NULL DEFAULT '{}',
                    comparison_json TEXT NOT NULL DEFAULT '{}',
                    review_json TEXT NOT NULL DEFAULT '{}',
                    cache_hit_of TEXT REFERENCES ai_jobs(id),
                    created_by TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_ai_jobs_intake_time ON ai_jobs(intake_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS ix_ai_jobs_case_time ON ai_jobs(case_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS ix_ai_jobs_cache ON ai_jobs(
                    document_hash, task_type, provider, model, prompt_version, schema_version, status
                );

                CREATE TABLE IF NOT EXISTS claim_bundles (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    name TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    source_hash TEXT NOT NULL DEFAULT '',
                    review_json TEXT NOT NULL DEFAULT '{}',
                    inventory_json TEXT NOT NULL DEFAULT '[]',
                    reconciliation_json TEXT NOT NULL DEFAULT '{}',
                    query_suggestions_json TEXT NOT NULL DEFAULT '[]',
                    confirmed_at TEXT,
                    confirmed_by TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_bundles_claim_time
                    ON claim_bundles(case_id, claim_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS claim_bundle_documents (
                    id TEXT PRIMARY KEY,
                    bundle_id TEXT NOT NULL REFERENCES claim_bundles(id),
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    document_hash TEXT NOT NULL,
                    page_count INTEGER NOT NULL DEFAULT 0,
                    extraction_status TEXT NOT NULL DEFAULT 'PENDING',
                    position INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    UNIQUE(bundle_id, document_id)
                );

                CREATE TABLE IF NOT EXISTS claim_bundle_segments (
                    id TEXT PRIMARY KEY,
                    bundle_id TEXT NOT NULL REFERENCES claim_bundles(id),
                    document_id TEXT NOT NULL REFERENCES documents(id),
                    start_page INTEGER NOT NULL,
                    end_page INTEGER NOT NULL,
                    document_type TEXT NOT NULL,
                    classification_status TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    title_text TEXT NOT NULL DEFAULT '',
                    evidence_text TEXT NOT NULL DEFAULT '',
                    user_document_type TEXT,
                    corrected_by TEXT,
                    corrected_at TEXT,
                    ai_job_id TEXT REFERENCES ai_jobs(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_bundle_segments
                    ON claim_bundle_segments(bundle_id, document_id, start_page);

                CREATE TABLE IF NOT EXISTS claim_evidence_facts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    bundle_id TEXT NOT NULL REFERENCES claim_bundles(id),
                    layer TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    field_name TEXT NOT NULL,
                    value_json TEXT NOT NULL,
                    document_id TEXT,
                    bundle_file TEXT NOT NULL DEFAULT '',
                    document_type TEXT NOT NULL,
                    page INTEGER,
                    source_text TEXT NOT NULL DEFAULT '',
                    basis TEXT NOT NULL,
                    confidence TEXT NOT NULL,
                    review_status TEXT NOT NULL,
                    reviewed_value_json TEXT,
                    reviewed_by TEXT,
                    reviewed_at TEXT,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_evidence_facts
                    ON claim_evidence_facts(claim_id, bundle_id, layer, field_name);

                CREATE TABLE IF NOT EXISTS claim_conflicts (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    bundle_id TEXT NOT NULL REFERENCES claim_bundles(id),
                    conflict_type TEXT NOT NULL,
                    topic TEXT NOT NULL,
                    source_a_json TEXT NOT NULL,
                    source_b_json TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    user_resolution TEXT NOT NULL DEFAULT '',
                    resolved_by TEXT,
                    resolved_at TEXT,
                    notes TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_conflicts_open
                    ON claim_conflicts(claim_id, bundle_id, status);

                CREATE TABLE IF NOT EXISTS workflow_phases (
                    id TEXT PRIMARY KEY,
                    workflow_type TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    code TEXT NOT NULL,
                    name TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workflow_version, code)
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_phases_version_sequence
                    ON workflow_phases(workflow_version, sequence);

                CREATE TABLE IF NOT EXISTS workflow_step_definitions (
                    id TEXT PRIMARY KEY,
                    workflow_type TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    step_code TEXT NOT NULL,
                    phase_id TEXT NOT NULL REFERENCES workflow_phases(id),
                    sequence INTEGER NOT NULL,
                    day_trigger_text TEXT NOT NULL DEFAULT '',
                    legal_reference TEXT NOT NULL DEFAULT '',
                    area TEXT NOT NULL DEFAULT '',
                    activity TEXT NOT NULL,
                    staff_action TEXT NOT NULL DEFAULT '',
                    standard_output TEXT NOT NULL DEFAULT '',
                    automation_type TEXT NOT NULL,
                    approval_role TEXT NOT NULL DEFAULT '',
                    depends_on_description TEXT NOT NULL DEFAULT '',
                    requires_coc_approval INTEGER NOT NULL DEFAULT 0,
                    requires_nclt_filing INTEGER NOT NULL DEFAULT 0,
                    requires_ibbi_filing INTEGER NOT NULL DEFAULT 0,
                    evidence_requirement_text TEXT NOT NULL DEFAULT '',
                    trigger_event_type TEXT NOT NULL,
                    creates_task INTEGER NOT NULL DEFAULT 1,
                    task_title TEXT NOT NULL DEFAULT '',
                    default_priority TEXT NOT NULL DEFAULT 'normal',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workflow_version, step_code, rule_version)
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_definitions_trigger
                    ON workflow_step_definitions(workflow_version, trigger_event_type, is_active);
                CREATE INDEX IF NOT EXISTS ix_workflow_definitions_phase
                    ON workflow_step_definitions(phase_id, sequence);

                CREATE TABLE IF NOT EXISTS workflow_dependencies (
                    id TEXT PRIMARY KEY,
                    parent_step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    child_step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    dependency_type TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(parent_step_definition_id, child_step_definition_id, dependency_type),
                    CHECK(parent_step_definition_id <> child_step_definition_id)
                );

                CREATE TABLE IF NOT EXISTS deadline_rules (
                    id TEXT PRIMARY KEY,
                    step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    anchor_event_type TEXT NOT NULL,
                    offset_days INTEGER,
                    offset_direction TEXT NOT NULL DEFAULT 'AFTER',
                    calendar_basis TEXT NOT NULL DEFAULT 'CALENDAR_DAYS',
                    rule_text TEXT NOT NULL DEFAULT '',
                    legal_reference TEXT NOT NULL DEFAULT '',
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(step_definition_id, rule_version)
                );

                CREATE TABLE IF NOT EXISTS workflow_template_links (
                    id TEXT PRIMARY KEY,
                    step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    template_id TEXT NOT NULL,
                    template_role TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(step_definition_id, template_id, template_role)
                );

                CREATE TABLE IF NOT EXISTS workflow_evidence_requirements (
                    id TEXT PRIMARY KEY,
                    step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    evidence_type TEXT NOT NULL,
                    mandatory INTEGER NOT NULL DEFAULT 0,
                    description TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(step_definition_id, evidence_type)
                );

                CREATE TABLE IF NOT EXISTS case_workflows (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    workflow_type TEXT NOT NULL,
                    workflow_version TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    started_at TEXT NOT NULL,
                    closed_at TEXT,
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id, workflow_type)
                );
                CREATE INDEX IF NOT EXISTS ix_case_workflows_case
                    ON case_workflows(case_id, status);

                CREATE TABLE IF NOT EXISTS case_events (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    event_type TEXT NOT NULL,
                    event_date TEXT NOT NULL,
                    source_type TEXT NOT NULL DEFAULT 'manual',
                    source_id TEXT NOT NULL DEFAULT '',
                    source_document_id TEXT REFERENCES documents(id),
                    status TEXT NOT NULL DEFAULT 'PENDING_REVIEW',
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT NOT NULL,
                    processed_at TEXT,
                    created_by TEXT NOT NULL,
                    confirmed_by TEXT,
                    confirmed_at TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, idempotency_key)
                );
                CREATE INDEX IF NOT EXISTS ix_case_events_case_date
                    ON case_events(case_id, event_date, created_at);
                CREATE INDEX IF NOT EXISTS ix_case_events_type_status
                    ON case_events(case_id, event_type, status);

                CREATE TABLE IF NOT EXISTS case_workflow_steps (
                    id TEXT PRIMARY KEY,
                    case_workflow_id TEXT NOT NULL REFERENCES case_workflows(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    step_definition_id TEXT NOT NULL REFERENCES workflow_step_definitions(id),
                    status TEXT NOT NULL DEFAULT 'NOT_TRIGGERED',
                    trigger_event_id TEXT REFERENCES case_events(id),
                    trigger_date TEXT,
                    statutory_due_date TEXT,
                    internal_due_date TEXT,
                    owner_user_id TEXT REFERENCES users(id),
                    checker_user_id TEXT REFERENCES users(id),
                    approval_status TEXT NOT NULL DEFAULT 'NOT_REQUIRED',
                    approved_by TEXT REFERENCES users(id),
                    approved_at TEXT,
                    priority TEXT NOT NULL DEFAULT 'normal',
                    started_at TEXT,
                    completed_at TEXT,
                    remarks TEXT NOT NULL DEFAULT '',
                    evidence_override_reason TEXT NOT NULL DEFAULT '',
                    evidence_override_by TEXT REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_workflow_id, step_definition_id)
                );
                CREATE INDEX IF NOT EXISTS ix_case_workflow_steps_case_status
                    ON case_workflow_steps(case_id, status, statutory_due_date);

                CREATE TABLE IF NOT EXISTS case_deadlines (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    case_workflow_step_id TEXT NOT NULL REFERENCES case_workflow_steps(id) ON DELETE CASCADE,
                    deadline_rule_id TEXT NOT NULL REFERENCES deadline_rules(id),
                    anchor_event_id TEXT REFERENCES case_events(id),
                    anchor_date TEXT,
                    calculated_due_date TEXT,
                    override_due_date TEXT,
                    override_reason TEXT NOT NULL DEFAULT '',
                    override_by TEXT REFERENCES users(id),
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    rule_version INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_workflow_step_id, deadline_rule_id)
                );
                CREATE INDEX IF NOT EXISTS ix_case_deadlines_case_due
                    ON case_deadlines(case_id, calculated_due_date, override_due_date, status);

                CREATE TABLE IF NOT EXISTS workflow_step_evidence (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id) ON DELETE CASCADE,
                    case_workflow_step_id TEXT NOT NULL REFERENCES case_workflow_steps(id) ON DELETE CASCADE,
                    evidence_requirement_id TEXT REFERENCES workflow_evidence_requirements(id),
                    evidence_type TEXT NOT NULL,
                    document_id TEXT REFERENCES documents(id),
                    event_id TEXT REFERENCES case_events(id),
                    source_type TEXT NOT NULL DEFAULT 'document',
                    source_id TEXT NOT NULL DEFAULT '',
                    override_reason TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_workflow_evidence_step
                    ON workflow_step_evidence(case_workflow_step_id, evidence_type);

                CREATE TABLE IF NOT EXISTS activity_events (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    event_type TEXT NOT NULL,
                    title TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    entity_type TEXT NOT NULL DEFAULT '',
                    entity_id TEXT NOT NULL DEFAULT '',
                    actor_id TEXT NOT NULL,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_activity_case_time ON activity_events(case_id, occurred_at DESC);

                CREATE TABLE IF NOT EXISTS audit_logs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    actor_id TEXT NOT NULL,
                    action TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    entity_id TEXT NOT NULL,
                    before_json TEXT,
                    after_json TEXT,
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_audit_case_time ON audit_logs(case_id, occurred_at DESC);
                """
            )
            connection.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (SCHEMA_VERSION, utc_now()),
            )
            self._ensure_column(connection, "contributions", "called_amount_paise", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "contributions", "payer_contact_id", "TEXT REFERENCES contacts(id)")
            self._ensure_column(connection, "contributions", "payer_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "contributions", "purpose", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "contributions", "direction_date", "TEXT")
            self._ensure_column(connection, "contributions", "status", "TEXT NOT NULL DEFAULT 'awaiting payment'")
            self._ensure_column(connection, "contributions", "source_type", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "contributions", "source_id", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_meetings", "notice_snapshot_json", "TEXT NOT NULL DEFAULT '{}'")
            self._ensure_column(connection, "coc_meetings", "actual_start_at", "TEXT")
            self._ensure_column(connection, "coc_meetings", "actual_end_at", "TEXT")
            self._ensure_column(connection, "coc_meetings", "notice_place", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_meetings", "quorum_threshold", "REAL NOT NULL DEFAULT 33")
            self._ensure_column(connection, "coc_meetings", "chair_name", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_meetings", "meeting_type", "TEXT NOT NULL DEFAULT 'SUBSEQUENT_COC'")
            self._ensure_column(connection, "coc_meetings", "coc_constitution_id", "TEXT REFERENCES coc_constitutions(id)")
            self._ensure_column(connection, "coc_meetings", "scheduled_start_at", "TEXT")
            self._ensure_column(connection, "coc_meetings", "scheduled_end_at", "TEXT")
            self._ensure_column(connection, "coc_meetings", "notice_due_date", "TEXT")
            self._ensure_column(connection, "coc_meetings", "meeting_due_date", "TEXT")
            self._ensure_column(connection, "coc_meetings", "membership_review_required", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "coc_agenda_items", "minutes_text", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_agenda_items", "minutes_disposition", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_attendance", "meeting_member_snapshot_id", "TEXT")
            self._ensure_column(connection, "coc_attendance", "participant_role", "TEXT NOT NULL DEFAULT 'OTHER'")
            self._ensure_column(connection, "coc_attendance", "authorization_status", "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'")
            self._ensure_column(connection, "coc_attendance", "authorization_document_id", "TEXT REFERENCES documents(id)")
            self._ensure_column(connection, "coc_attendance", "voting_entitled", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "coc_attendance", "voting_share_units", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "coc_votes", "meeting_member_snapshot_id", "TEXT")
            self._ensure_column(connection, "coc_votes", "resolution_id", "TEXT")
            self._ensure_column(connection, "coc_votes", "voting_share_units", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "coc_votes", "method", "TEXT NOT NULL DEFAULT 'MEETING'")
            self._ensure_column(connection, "coc_votes", "source", "TEXT NOT NULL DEFAULT 'MANUAL'")
            self._ensure_column(connection, "coc_votes", "remarks", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "ai_jobs", "api_calls", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "ai_jobs", "latency_ms", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "ai_jobs", "actual_cost", "REAL")
            self._ensure_column(connection, "ai_jobs", "estimated_list_cost", "REAL")
            self._ensure_column(connection, "ai_jobs", "claim_id", "TEXT REFERENCES claims(id)")
            self._ensure_column(connection, "ai_jobs", "bundle_id", "TEXT REFERENCES claim_bundles(id)")
            self._ensure_column(connection, "ai_jobs", "page_start", "INTEGER")
            self._ensure_column(connection, "ai_jobs", "page_end", "INTEGER")
            self._ensure_column(connection, "ai_jobs", "parent_job_id", "TEXT REFERENCES ai_jobs(id)")
            self._ensure_column(connection, "ai_jobs", "input_fingerprint", "TEXT NOT NULL DEFAULT ''")
            connection.execute("CREATE INDEX IF NOT EXISTS ix_ai_jobs_granular_cache ON ai_jobs(input_fingerprint, status)")
            self._ensure_column(connection, "coc_meetings", "signed_at", "TEXT")
            self._ensure_column(connection, "tasks", "workflow_step_id", "TEXT REFERENCES case_workflow_steps(id)")
            self._ensure_column(connection, "documents", "workflow_step_id", "TEXT REFERENCES case_workflow_steps(id)")
            self._ensure_column(connection, "documents", "event_id", "TEXT REFERENCES case_events(id)")
            self._ensure_column(connection, "documents", "confidentiality_classification", "TEXT NOT NULL DEFAULT 'NORMAL'")
            # These Phase-4 extensions target Phase-3 tables which are created
            # later in this initialization script on a pristine database.
            if connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='coc_cost_statements'").fetchone():
                self._ensure_column(connection, "coc_cost_statements", "expense_period_label", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(connection, "coc_cost_statements", "cost_kind", "TEXT NOT NULL DEFAULT 'ESTIMATED'")
                self._ensure_column(connection, "coc_cost_statements", "approval_status", "TEXT NOT NULL DEFAULT 'DRAFT'")
                self._ensure_column(connection, "coc_cost_statements", "approved_meeting_id", "TEXT REFERENCES coc_meetings(id)")
                self._ensure_column(connection, "coc_cost_statements", "approved_resolution_id", "TEXT REFERENCES coc_resolutions(id)")
                self._ensure_column(connection, "coc_cost_statement_rows", "expense_period_label", "TEXT NOT NULL DEFAULT ''")
                self._ensure_column(connection, "coc_cost_statement_rows", "expense_type", "TEXT NOT NULL DEFAULT 'ACTUAL'")
                self._ensure_column(connection, "coc_cost_statement_rows", "vendor_contact_id", "TEXT REFERENCES contacts(id)")
                self._ensure_column(connection, "coc_cost_statement_rows", "approval_status", "TEXT NOT NULL DEFAULT 'DRAFT'")
                self._ensure_column(connection, "coc_cost_statement_rows", "payment_date", "TEXT")
                self._ensure_column(connection, "coc_cost_statement_rows", "payment_reference", "TEXT NOT NULL DEFAULT ''")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_tasks_workflow_step ON tasks(workflow_step_id) WHERE workflow_step_id IS NOT NULL")
            connection.execute("CREATE INDEX IF NOT EXISTS ix_documents_workflow_event ON documents(workflow_step_id, event_id)")
            claim_columns = {
                "claim_number": "TEXT NOT NULL DEFAULT ''", "creditor_name": "TEXT NOT NULL DEFAULT ''",
                "contact_person": "TEXT NOT NULL DEFAULT ''", "email": "TEXT NOT NULL DEFAULT ''",
                "phone": "TEXT NOT NULL DEFAULT ''", "address": "TEXT NOT NULL DEFAULT ''",
                "related_party_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'", "received_via": "TEXT NOT NULL DEFAULT 'Email'",
                "sender_email": "TEXT NOT NULL DEFAULT ''", "sender_name": "TEXT NOT NULL DEFAULT ''",
                "source_email_id": "TEXT", "email_subject": "TEXT NOT NULL DEFAULT ''", "source_notes": "TEXT NOT NULL DEFAULT ''",
                "currency": "TEXT NOT NULL DEFAULT 'INR'", "principal_claimed": "REAL", "interest_claimed": "REAL",
                "other_amount_claimed": "REAL", "calculated_component_total": "REAL",
                "principal_admitted": "REAL NOT NULL DEFAULT 0", "interest_admitted": "REAL NOT NULL DEFAULT 0",
                "other_amount_admitted": "REAL NOT NULL DEFAULT 0", "amount_not_admitted": "REAL NOT NULL DEFAULT 0",
                "secured_status": "TEXT NOT NULL DEFAULT 'UNKNOWN'", "security_value": "REAL", "charge_details": "TEXT NOT NULL DEFAULT ''",
                "date_debt_incurred": "TEXT", "due_date": "TEXT", "default_date": "TEXT", "interest_rate": "REAL",
                "interest_basis": "TEXT NOT NULL DEFAULT ''", "verification_notes": "TEXT NOT NULL DEFAULT ''",
                "issues_identified": "TEXT NOT NULL DEFAULT ''", "documents_checked": "TEXT NOT NULL DEFAULT ''",
                "decision_date": "TEXT", "decision_by": "TEXT NOT NULL DEFAULT ''", "idempotency_key": "TEXT",
                "override_reason": "TEXT NOT NULL DEFAULT ''",
                "claim_submission_date": "TEXT", "claim_as_on_date": "TEXT", "claim_reference": "TEXT NOT NULL DEFAULT ''",
                "creditor_identifier": "TEXT NOT NULL DEFAULT ''", "authorized_representative": "TEXT NOT NULL DEFAULT ''",
                "authorized_representative_designation": "TEXT NOT NULL DEFAULT ''", "nature_of_debt": "TEXT NOT NULL DEFAULT ''",
                "basis_of_claim": "TEXT NOT NULL DEFAULT ''", "facility_type": "TEXT NOT NULL DEFAULT ''",
                "original_facility_amount": "REAL", "sanction_letter_reference": "TEXT NOT NULL DEFAULT ''",
                "sanction_date": "TEXT", "agreement_date": "TEXT", "bank_details": "TEXT NOT NULL DEFAULT ''",
                "supporting_evidence_json": "TEXT NOT NULL DEFAULT '{}'", "reconciliation_json": "TEXT NOT NULL DEFAULT '{}'",
            }
            for column, declaration in claim_columns.items():
                self._ensure_column(connection, "claims", column, declaration)
            phase_two_claim_columns = {
                "acknowledgement_status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "acknowledged_at": "TEXT",
                "acknowledged_by": "TEXT",
                "classification_status": "TEXT NOT NULL DEFAULT 'PENDING'",
                "classification_confirmed_at": "TEXT",
                "classification_confirmed_by": "TEXT",
                "scrutiny_status": "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
                "verification_status": "TEXT NOT NULL DEFAULT 'NOT_STARTED'",
                "verification_started_at": "TEXT",
                "verification_completed_at": "TEXT",
                "verification_completed_by": "TEXT",
                "late_flag": "INTEGER NOT NULL DEFAULT 0",
                "claim_deadline_date": "TEXT",
                "days_after_deadline": "INTEGER NOT NULL DEFAULT 0",
                "late_review_status": "TEXT NOT NULL DEFAULT 'NOT_APPLICABLE'",
                "late_review_reason": "TEXT NOT NULL DEFAULT ''",
                "late_reviewed_by": "TEXT",
                "late_reviewed_at": "TEXT",
                "principal_claimed_paise": "INTEGER",
                "interest_claimed_paise": "INTEGER",
                "other_claimed_paise": "INTEGER",
                "total_claimed_paise": "INTEGER NOT NULL DEFAULT 0",
                "principal_admitted_paise": "INTEGER NOT NULL DEFAULT 0",
                "interest_admitted_paise": "INTEGER NOT NULL DEFAULT 0",
                "other_admitted_paise": "INTEGER NOT NULL DEFAULT 0",
                "total_admitted_paise": "INTEGER NOT NULL DEFAULT 0",
                "amount_not_admitted_paise": "INTEGER NOT NULL DEFAULT 0",
            }
            for column, declaration in phase_two_claim_columns.items():
                self._ensure_column(connection, "claims", column, declaration)
            for column in (
                "principal_admitted_paise", "interest_admitted_paise", "other_admitted_paise",
                "total_admitted_paise", "amount_not_admitted_paise",
            ):
                self._ensure_column(connection, "claim_decisions", column, "INTEGER NOT NULL DEFAULT 0")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS claim_related_party_reviews (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    claim_revision INTEGER NOT NULL,
                    status TEXT NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence_document_id TEXT REFERENCES documents(id),
                    determined_by TEXT NOT NULL,
                    determined_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_related_party_reviews
                    ON claim_related_party_reviews(case_id, claim_id, determined_at DESC);

                CREATE TABLE IF NOT EXISTS claim_security_reviews (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    claim_revision INTEGER NOT NULL,
                    status_claimed TEXT NOT NULL DEFAULT 'UNKNOWN',
                    status_verified TEXT NOT NULL DEFAULT 'UNKNOWN',
                    verification_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    review_notes TEXT NOT NULL DEFAULT '',
                    evidence_document_id TEXT REFERENCES documents(id),
                    reviewed_by TEXT NOT NULL,
                    reviewed_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_claim_security_reviews
                    ON claim_security_reviews(case_id, claim_id, reviewed_at DESC);

                CREATE TABLE IF NOT EXISTS claim_decision_communications (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    decision_id TEXT NOT NULL REFERENCES claim_decisions(id),
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    communication_id TEXT REFERENCES communications(id),
                    service_proof_document_id TEXT REFERENCES documents(id),
                    prepared_by TEXT NOT NULL,
                    approved_by TEXT,
                    sent_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id, decision_id)
                );

                CREATE TABLE IF NOT EXISTS list_of_creditors_snapshots (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    version_number INTEGER NOT NULL,
                    as_on_date TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'FORMAL',
                    total_claimed_paise INTEGER NOT NULL,
                    total_admitted_paise INTEGER NOT NULL,
                    total_not_admitted_paise INTEGER NOT NULL,
                    row_count INTEGER NOT NULL,
                    source_fingerprint TEXT NOT NULL,
                    document_id TEXT REFERENCES documents(id),
                    filing_status TEXT NOT NULL DEFAULT 'NOT_RECORDED',
                    filing_date TEXT,
                    filing_mechanism TEXT NOT NULL DEFAULT '',
                    filing_evidence_document_id TEXT REFERENCES documents(id),
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, version_number),
                    UNIQUE(case_id, source_fingerprint)
                );
                CREATE INDEX IF NOT EXISTS ix_loc_snapshots_case_version
                    ON list_of_creditors_snapshots(case_id, version_number DESC);

                CREATE TABLE IF NOT EXISTS list_of_creditors_snapshot_rows (
                    id TEXT PRIMARY KEY,
                    snapshot_id TEXT NOT NULL REFERENCES list_of_creditors_snapshots(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    position INTEGER NOT NULL,
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    claim_number TEXT NOT NULL,
                    claim_revision INTEGER NOT NULL,
                    decision_id TEXT,
                    creditor_name TEXT NOT NULL,
                    creditor_category TEXT NOT NULL,
                    form_type TEXT NOT NULL,
                    received_date TEXT,
                    claimed_paise INTEGER NOT NULL,
                    admitted_paise INTEGER NOT NULL,
                    not_admitted_paise INTEGER NOT NULL,
                    security_status TEXT NOT NULL,
                    related_party_status TEXT NOT NULL,
                    decision_status TEXT NOT NULL,
                    decision_date TEXT,
                    UNIQUE(snapshot_id, claim_id)
                );

                CREATE TABLE IF NOT EXISTS coc_eligibility_reviews (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    claim_revision INTEGER NOT NULL,
                    decision_id TEXT NOT NULL REFERENCES claim_decisions(id),
                    related_party_status TEXT NOT NULL,
                    eligibility_status TEXT NOT NULL,
                    eligible_debt_paise INTEGER NOT NULL DEFAULT 0,
                    reason TEXT NOT NULL DEFAULT '',
                    evidence_document_id TEXT REFERENCES documents(id),
                    decided_by TEXT NOT NULL,
                    decided_at TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_coc_eligibility_case
                    ON coc_eligibility_reviews(case_id, eligibility_status, decided_at DESC);

                CREATE TABLE IF NOT EXISTS coc_voting_calculations (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    calculation_version INTEGER NOT NULL,
                    constitution_id TEXT,
                    total_eligible_debt_paise INTEGER NOT NULL,
                    display_precision INTEGER NOT NULL DEFAULT 4,
                    source_fingerprint TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'CALCULATED',
                    calculated_by TEXT NOT NULL,
                    calculated_at TEXT NOT NULL,
                    UNIQUE(case_id, calculation_version),
                    UNIQUE(case_id, source_fingerprint)
                );
                CREATE TABLE IF NOT EXISTS coc_voting_calculation_rows (
                    id TEXT PRIMARY KEY,
                    calculation_id TEXT NOT NULL REFERENCES coc_voting_calculations(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    decision_id TEXT NOT NULL REFERENCES claim_decisions(id),
                    eligibility_review_id TEXT NOT NULL REFERENCES coc_eligibility_reviews(id),
                    creditor_name TEXT NOT NULL,
                    admitted_debt_paise INTEGER NOT NULL,
                    raw_percentage TEXT NOT NULL,
                    display_percentage TEXT NOT NULL,
                    UNIQUE(calculation_id, claim_id)
                );

                CREATE TABLE IF NOT EXISTS coc_constitutions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    constitution_version INTEGER NOT NULL,
                    constitution_date TEXT NOT NULL,
                    constitution_type TEXT NOT NULL DEFAULT 'INITIAL',
                    status TEXT NOT NULL DEFAULT 'CONFIRMED',
                    parent_constitution_id TEXT REFERENCES coc_constitutions(id),
                    loc_snapshot_id TEXT NOT NULL REFERENCES list_of_creditors_snapshots(id),
                    voting_calculation_id TEXT NOT NULL REFERENCES coc_voting_calculations(id),
                    confirmed_by TEXT NOT NULL,
                    confirmed_at TEXT NOT NULL,
                    review_required INTEGER NOT NULL DEFAULT 0,
                    report_status TEXT NOT NULL DEFAULT 'NOT_GENERATED',
                    report_document_id TEXT REFERENCES documents(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(case_id, constitution_version)
                );
                CREATE INDEX IF NOT EXISTS ix_coc_constitutions_case_version
                    ON coc_constitutions(case_id, constitution_version DESC);

                CREATE TABLE IF NOT EXISTS coc_constitution_members (
                    id TEXT PRIMARY KEY,
                    constitution_id TEXT NOT NULL REFERENCES coc_constitutions(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    decision_id TEXT NOT NULL REFERENCES claim_decisions(id),
                    eligibility_review_id TEXT NOT NULL REFERENCES coc_eligibility_reviews(id),
                    creditor_name TEXT NOT NULL,
                    admitted_debt_paise INTEGER NOT NULL,
                    related_party_status TEXT NOT NULL,
                    eligibility_status TEXT NOT NULL,
                    raw_voting_percentage TEXT NOT NULL,
                    display_voting_percentage TEXT NOT NULL,
                    UNIQUE(constitution_id, claim_id)
                );

                CREATE TABLE IF NOT EXISTS creditor_classes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    class_name TEXT NOT NULL,
                    class_type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    ar_required INTEGER NOT NULL DEFAULT 0,
                    notes TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id, class_name)
                );
                CREATE TABLE IF NOT EXISTS creditor_class_claims (
                    class_id TEXT NOT NULL REFERENCES creditor_classes(id) ON DELETE CASCADE,
                    claim_id TEXT NOT NULL REFERENCES claims(id),
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    PRIMARY KEY(class_id, claim_id)
                );
                CREATE TABLE IF NOT EXISTS authorised_representative_processes (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    class_id TEXT NOT NULL REFERENCES creditor_classes(id),
                    requirement_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    candidate_name TEXT NOT NULL DEFAULT '',
                    selected_ar TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'NOT_STARTED',
                    filing_requirement TEXT NOT NULL DEFAULT '',
                    evidence_document_id TEXT REFERENCES documents(id),
                    created_by TEXT NOT NULL,
                    updated_by TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(case_id, class_id)
                );

                -- Phase 3 extends the retained CoC meeting tables with versioned,
                -- historical records.  Generic legacy CoC rows remain readable;
                -- these tables are the authoritative lifecycle records for new work.
                CREATE TABLE IF NOT EXISTS coc_agenda_versions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    agenda_kind TEXT NOT NULL DEFAULT 'STANDARD',
                    parent_agenda_version_id TEXT REFERENCES coc_agenda_versions(id),
                    frozen_at TEXT,
                    frozen_by TEXT REFERENCES users(id),
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, version_number)
                );
                CREATE TABLE IF NOT EXISTS coc_agenda_version_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    agenda_version_id TEXT NOT NULL REFERENCES coc_agenda_versions(id) ON DELETE CASCADE,
                    agenda_number TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    agenda_type TEXT NOT NULL DEFAULT 'FOR_DISCUSSION',
                    title TEXT NOT NULL,
                    agenda_note TEXT NOT NULL DEFAULT '',
                    proposed_resolution_text TEXT NOT NULL DEFAULT '',
                    requires_resolution INTEGER NOT NULL DEFAULT 0,
                    requires_voting INTEGER NOT NULL DEFAULT 0,
                    supporting_document_ids_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(agenda_version_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS ix_coc_agenda_versions_meeting
                    ON coc_agenda_versions(case_id, meeting_id, version_number DESC);
                CREATE INDEX IF NOT EXISTS ix_coc_agenda_version_items
                    ON coc_agenda_version_items(case_id, meeting_id, agenda_version_id, sequence);

                CREATE TABLE IF NOT EXISTS coc_meeting_member_snapshots (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    coc_constitution_id TEXT NOT NULL REFERENCES coc_constitutions(id),
                    operational_member_id TEXT REFERENCES coc_members(id),
                    claim_id TEXT REFERENCES claims(id),
                    creditor_name TEXT NOT NULL,
                    representative_name TEXT NOT NULL DEFAULT '',
                    recipient_email TEXT NOT NULL DEFAULT '',
                    recipient_address TEXT NOT NULL DEFAULT '',
                    recipient_category TEXT NOT NULL DEFAULT 'COC_MEMBER',
                    admitted_debt_paise INTEGER NOT NULL DEFAULT 0,
                    voting_share_units INTEGER NOT NULL DEFAULT 0,
                    voting_share_text TEXT NOT NULL DEFAULT '0.0000',
                    participation_rights INTEGER NOT NULL DEFAULT 1,
                    voting_rights INTEGER NOT NULL DEFAULT 1,
                    frozen_at TEXT NOT NULL,
                    frozen_by TEXT NOT NULL REFERENCES users(id),
                    UNIQUE(meeting_id, claim_id)
                );
                CREATE INDEX IF NOT EXISTS ix_coc_meeting_snapshot_meeting
                    ON coc_meeting_member_snapshots(case_id, meeting_id, recipient_category);

                CREATE TABLE IF NOT EXISTS coc_notice_versions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    coc_constitution_id TEXT NOT NULL REFERENCES coc_constitutions(id),
                    agenda_version_id TEXT NOT NULL REFERENCES coc_agenda_versions(id),
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    document_id TEXT REFERENCES documents(id),
                    approved_by TEXT REFERENCES users(id),
                    approved_at TEXT,
                    issued_by TEXT REFERENCES users(id),
                    issued_at TEXT,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, version_number)
                );
                CREATE TABLE IF NOT EXISTS coc_notice_dispatches (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    notice_version_id TEXT NOT NULL REFERENCES coc_notice_versions(id) ON DELETE CASCADE,
                    meeting_member_snapshot_id TEXT REFERENCES coc_meeting_member_snapshots(id),
                    recipient_name TEXT NOT NULL,
                    recipient_address TEXT NOT NULL DEFAULT '',
                    dispatch_method TEXT NOT NULL DEFAULT '',
                    dispatch_datetime TEXT,
                    status TEXT NOT NULL DEFAULT 'NOT_SENT',
                    service_proof_document_id TEXT REFERENCES documents(id),
                    remarks TEXT NOT NULL DEFAULT '',
                    recorded_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_coc_notice_dispatch_meeting
                    ON coc_notice_dispatches(case_id, meeting_id, notice_version_id);

                CREATE TABLE IF NOT EXISTS coc_quorum_rules (
                    id TEXT PRIMARY KEY,
                    rule_code TEXT NOT NULL,
                    process_type TEXT NOT NULL DEFAULT 'CIRP',
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    minimum_voting_share_units INTEGER NOT NULL,
                    rule_reference TEXT NOT NULL DEFAULT '',
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(rule_code, rule_version)
                );
                CREATE TABLE IF NOT EXISTS coc_meeting_quorum_records (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    quorum_rule_id TEXT REFERENCES coc_quorum_rules(id),
                    required_voting_share_units INTEGER NOT NULL DEFAULT 0,
                    present_voting_share_units INTEGER NOT NULL DEFAULT 0,
                    quorum_met INTEGER,
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    constitution_id TEXT NOT NULL REFERENCES coc_constitutions(id),
                    calculated_at TEXT NOT NULL,
                    calculated_by TEXT NOT NULL REFERENCES users(id),
                    UNIQUE(meeting_id, calculated_at)
                );

                CREATE TABLE IF NOT EXISTS coc_approval_rules (
                    id TEXT PRIMARY KEY,
                    rule_code TEXT NOT NULL,
                    process_type TEXT NOT NULL DEFAULT 'CIRP',
                    effective_from TEXT NOT NULL,
                    effective_to TEXT,
                    minimum_for_share_units INTEGER NOT NULL,
                    rule_reference TEXT NOT NULL DEFAULT '',
                    rule_version INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(rule_code, rule_version)
                );
                CREATE TABLE IF NOT EXISTS coc_resolutions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    agenda_item_id TEXT NOT NULL REFERENCES coc_agenda_version_items(id),
                    resolution_number TEXT NOT NULL,
                    title TEXT NOT NULL,
                    proposed_resolution_text TEXT NOT NULL DEFAULT '',
                    final_resolution_text TEXT NOT NULL DEFAULT '',
                    resolution_category TEXT NOT NULL DEFAULT 'OTHER_APPLICABLE_DECISION',
                    voting_required INTEGER NOT NULL DEFAULT 0,
                    approval_rule_id TEXT REFERENCES coc_approval_rules(id),
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, resolution_number)
                );
                CREATE TABLE IF NOT EXISTS coc_voting_sessions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    opened_at TEXT,
                    scheduled_close_at TEXT,
                    actual_close_at TEXT,
                    communication_document_id TEXT REFERENCES documents(id),
                    dispatch_status TEXT NOT NULL DEFAULT 'NOT_SENT',
                    eligible_voter_snapshot_json TEXT NOT NULL DEFAULT '[]',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_voting_session_resolutions (
                    voting_session_id TEXT NOT NULL REFERENCES coc_voting_sessions(id) ON DELETE CASCADE,
                    resolution_id TEXT NOT NULL REFERENCES coc_resolutions(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    PRIMARY KEY(voting_session_id, resolution_id)
                );
                CREATE TABLE IF NOT EXISTS coc_vote_corrections (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    vote_id TEXT NOT NULL REFERENCES coc_votes(id),
                    old_vote TEXT NOT NULL,
                    new_vote TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    corrected_by TEXT NOT NULL REFERENCES users(id),
                    corrected_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_voting_results (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    voting_session_id TEXT NOT NULL REFERENCES coc_voting_sessions(id),
                    resolution_id TEXT NOT NULL REFERENCES coc_resolutions(id),
                    approval_rule_id TEXT REFERENCES coc_approval_rules(id),
                    required_for_share_units INTEGER NOT NULL DEFAULT 0,
                    for_share_units INTEGER NOT NULL DEFAULT 0,
                    against_share_units INTEGER NOT NULL DEFAULT 0,
                    abstain_share_units INTEGER NOT NULL DEFAULT 0,
                    not_voted_share_units INTEGER NOT NULL DEFAULT 0,
                    result TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED',
                    status TEXT NOT NULL DEFAULT 'CALCULATED',
                    calculated_by TEXT NOT NULL REFERENCES users(id),
                    calculated_at TEXT NOT NULL,
                    finalized_by TEXT REFERENCES users(id),
                    finalized_at TEXT,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    UNIQUE(voting_session_id, resolution_id)
                );

                CREATE TABLE IF NOT EXISTS coc_minutes_entries (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    agenda_item_id TEXT NOT NULL REFERENCES coc_agenda_version_items(id),
                    minutes_text TEXT NOT NULL DEFAULT '',
                    disposition TEXT NOT NULL DEFAULT '',
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id, agenda_item_id)
                );
                CREATE TABLE IF NOT EXISTS coc_minutes_versions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    version_number INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    document_id TEXT REFERENCES documents(id),
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    generated_by TEXT NOT NULL REFERENCES users(id),
                    generated_at TEXT NOT NULL,
                    finalized_by TEXT REFERENCES users(id),
                    finalized_at TEXT,
                    parent_minutes_version_id TEXT REFERENCES coc_minutes_versions(id),
                    UNIQUE(meeting_id, version_number)
                );
                CREATE TABLE IF NOT EXISTS coc_minutes_circulations (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    minutes_version_id TEXT NOT NULL REFERENCES coc_minutes_versions(id) ON DELETE CASCADE,
                    recipient_snapshot_json TEXT NOT NULL DEFAULT '[]',
                    circulation_datetime TEXT,
                    method TEXT NOT NULL DEFAULT '',
                    status TEXT NOT NULL DEFAULT 'NOT_SENT',
                    service_proof_document_id TEXT REFERENCES documents(id),
                    remarks TEXT NOT NULL DEFAULT '',
                    recorded_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS coc_cost_statements (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    total_paise INTEGER NOT NULL DEFAULT 0,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_cost_statement_rows (
                    id TEXT PRIMARY KEY,
                    cost_statement_id TEXT NOT NULL REFERENCES coc_cost_statements(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    category TEXT NOT NULL,
                    vendor_name TEXT NOT NULL DEFAULT '',
                    description TEXT NOT NULL DEFAULT '',
                    period_date TEXT,
                    amount_paise INTEGER NOT NULL DEFAULT 0,
                    gst_paise INTEGER NOT NULL DEFAULT 0,
                    paid_status TEXT NOT NULL DEFAULT 'UNPAID',
                    approval_required INTEGER NOT NULL DEFAULT 0,
                    supporting_document_id TEXT REFERENCES documents(id),
                    created_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_operations_updates (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    payload_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(meeting_id)
                );
                CREATE TABLE IF NOT EXISTS coc_professional_proposals (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    agenda_item_id TEXT REFERENCES coc_agenda_version_items(id),
                    service_category TEXT NOT NULL,
                    professional_name TEXT NOT NULL DEFAULT '',
                    scope TEXT NOT NULL DEFAULT '',
                    proposed_fee_paise INTEGER NOT NULL DEFAULT 0,
                    tax_paise INTEGER NOT NULL DEFAULT 0,
                    appointment_status TEXT NOT NULL DEFAULT 'PROPOSED',
                    approval_type TEXT NOT NULL DEFAULT '',
                    supporting_document_id TEXT REFERENCES documents(id),
                    resolution_id TEXT REFERENCES coc_resolutions(id),
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_action_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    agenda_item_id TEXT REFERENCES coc_agenda_version_items(id),
                    resolution_id TEXT REFERENCES coc_resolutions(id),
                    task_id TEXT REFERENCES tasks(id),
                    action_text TEXT NOT NULL,
                    owner TEXT NOT NULL DEFAULT '',
                    due_date TEXT,
                    status TEXT NOT NULL DEFAULT 'OPEN',
                    completion_evidence_document_id TEXT REFERENCES documents(id),
                    remarks TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS coc_meeting_adjournments (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    meeting_id TEXT NOT NULL REFERENCES coc_meetings(id) ON DELETE CASCADE,
                    adjourned_at TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    next_scheduled_start_at TEXT,
                    recorded_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_coc_action_items_case
                    ON coc_action_items(case_id, meeting_id, status);

                /* CIRP-057--076: typed operational control registers.
                   Domain-specific validation lives in phase4_core; this shared
                   ledger keeps source, evidence, version and audit semantics
                   uniform without turning the application into an ERP. */
                CREATE TABLE IF NOT EXISTS phase4_records (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    domain TEXT NOT NULL,
                    record_type TEXT NOT NULL,
                    record_key TEXT NOT NULL DEFAULT '',
                    parent_record_id TEXT REFERENCES phase4_records(id),
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    period_from TEXT,
                    period_to TEXT,
                    effective_date TEXT,
                    amount_paise INTEGER NOT NULL DEFAULT 0,
                    tax_paise INTEGER NOT NULL DEFAULT 0,
                    document_id TEXT REFERENCES documents(id),
                    confidentiality_level TEXT NOT NULL DEFAULT 'NORMAL',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    version_number INTEGER NOT NULL DEFAULT 1,
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    archived_at TEXT,
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_phase4_records_case_domain
                    ON phase4_records(case_id, domain, record_type, archived_at, created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_phase4_records_idempotency
                    ON phase4_records(case_id, idempotency_key)
                    WHERE idempotency_key <> '';

                CREATE TABLE IF NOT EXISTS phase4_record_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_id TEXT NOT NULL REFERENCES phase4_records(id) ON DELETE CASCADE,
                    item_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    amount_paise INTEGER NOT NULL DEFAULT 0,
                    document_id TEXT REFERENCES documents(id),
                    data_json TEXT NOT NULL DEFAULT '{}',
                    source_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(record_id, item_key)
                );
                CREATE INDEX IF NOT EXISTS ix_phase4_items_case_record
                    ON phase4_record_items(case_id, record_id, sequence);

                CREATE TABLE IF NOT EXISTS phase4_record_links (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_id TEXT NOT NULL REFERENCES phase4_records(id) ON DELETE CASCADE,
                    linked_record_id TEXT REFERENCES phase4_records(id),
                    document_id TEXT REFERENCES documents(id),
                    link_type TEXT NOT NULL,
                    remarks TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(record_id, linked_record_id, document_id, link_type)
                );

                CREATE TABLE IF NOT EXISTS coc_cost_allocation_snapshots (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    cost_statement_id TEXT NOT NULL REFERENCES coc_cost_statements(id) ON DELETE CASCADE,
                    coc_constitution_id TEXT REFERENCES coc_constitutions(id),
                    meeting_id TEXT REFERENCES coc_meetings(id),
                    allocation_date TEXT NOT NULL,
                    total_paise INTEGER NOT NULL DEFAULT 0,
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(cost_statement_id)
                );
                CREATE TABLE IF NOT EXISTS coc_cost_allocation_rows (
                    id TEXT PRIMARY KEY,
                    allocation_snapshot_id TEXT NOT NULL REFERENCES coc_cost_allocation_snapshots(id) ON DELETE CASCADE,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    creditor_name TEXT NOT NULL,
                    claim_id TEXT REFERENCES claims(id),
                    voting_share_units INTEGER NOT NULL,
                    allocated_paise INTEGER NOT NULL,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS vdr_access_grants (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    workspace_record_id TEXT NOT NULL REFERENCES phase4_records(id) ON DELETE CASCADE,
                    recipient_record_id TEXT NOT NULL REFERENCES phase4_records(id) ON DELETE CASCADE,
                    undertaking_record_id TEXT REFERENCES phase4_records(id),
                    folder_scope_json TEXT NOT NULL DEFAULT '[]',
                    status TEXT NOT NULL DEFAULT 'GRANTED',
                    override_reason TEXT NOT NULL DEFAULT '',
                    valid_from TEXT,
                    valid_to TEXT,
                    granted_by TEXT NOT NULL REFERENCES users(id),
                    granted_at TEXT NOT NULL,
                    revoked_by TEXT REFERENCES users(id),
                    revoked_at TEXT,
                    UNIQUE(workspace_record_id, recipient_record_id)
                );
                CREATE TABLE IF NOT EXISTS vdr_access_logs (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    workspace_record_id TEXT NOT NULL REFERENCES phase4_records(id) ON DELETE CASCADE,
                    recipient_record_id TEXT REFERENCES phase4_records(id),
                    document_id TEXT REFERENCES documents(id),
                    action TEXT NOT NULL,
                    metadata_json TEXT NOT NULL DEFAULT '{}',
                    actor_id TEXT REFERENCES users(id),
                    occurred_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_vdr_access_log_case ON vdr_access_logs(case_id, occurred_at DESC);

                CREATE TABLE IF NOT EXISTS transaction_audit_engagements (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), auditor_contact_id TEXT REFERENCES contacts(id),
                    status TEXT NOT NULL DEFAULT 'DRAFT', quotation_request_id TEXT REFERENCES phase4_records(id), selection_record_id TEXT REFERENCES phase4_records(id),
                    coc_meeting_id TEXT REFERENCES coc_meetings(id), coc_resolution_id TEXT REFERENCES coc_resolutions(id), appointment_date TEXT,
                    scope_confirmed_date TEXT, audit_period_from TEXT, audit_period_to TEXT, draft_report_due_date TEXT, final_report_due_date TEXT,
                    draft_report_document_id TEXT REFERENCES documents(id), final_report_document_id TEXT REFERENCES documents(id), rp_comments_status TEXT NOT NULL DEFAULT 'NOT_STARTED',
                    remarks TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(case_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS transaction_review_scopes (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE,
                    review_type TEXT NOT NULL, counterparty_scope TEXT NOT NULL DEFAULT 'ALL', period_from TEXT, period_to TEXT, anchor_date TEXT,
                    basis TEXT NOT NULL DEFAULT '', legal_reference_text TEXT NOT NULL DEFAULT '', rule_id TEXT, rule_version TEXT, system_calculated_date TEXT,
                    status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', confirmed_by TEXT REFERENCES users(id), confirmed_at TEXT, superseded_by TEXT REFERENCES transaction_review_scopes(id),
                    data_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(case_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS transaction_audit_document_requirements (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE,
                    category TEXT NOT NULL, description TEXT NOT NULL DEFAULT '', period_from TEXT, period_to TEXT, requested_from TEXT NOT NULL DEFAULT '', requested_date TEXT, required_date TEXT,
                    status TEXT NOT NULL DEFAULT 'REQUESTED', received_date TEXT, management_requisition_record_id TEXT REFERENCES phase4_records(id), auditor_request_reference TEXT NOT NULL DEFAULT '',
                    rp_remarks TEXT NOT NULL DEFAULT '', auditor_remarks TEXT NOT NULL DEFAULT '', deficiency TEXT NOT NULL DEFAULT '', followup_count INTEGER NOT NULL DEFAULT 0,
                    reviewed_by TEXT REFERENCES users(id), reviewed_at TEXT, data_json TEXT NOT NULL DEFAULT '{}', idempotency_key TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
                    UNIQUE(case_id, idempotency_key)
                );
                CREATE TABLE IF NOT EXISTS transaction_audit_requirement_documents (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), requirement_id TEXT NOT NULL REFERENCES transaction_audit_document_requirements(id) ON DELETE CASCADE,
                    document_id TEXT NOT NULL REFERENCES documents(id), created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(requirement_id, document_id)
                );
                CREATE TABLE IF NOT EXISTS transaction_audit_bank_coverages (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE,
                    bank_name TEXT NOT NULL, account_identifier_masked TEXT NOT NULL DEFAULT '', account_type TEXT NOT NULL DEFAULT '', required_period_from TEXT, required_period_to TEXT,
                    available_period_from TEXT, available_period_to TEXT, coverage_status TEXT NOT NULL DEFAULT 'REVIEW_REQUIRED', remarks TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transaction_audit_bank_documents (
                    id TEXT PRIMARY KEY, bank_coverage_id TEXT NOT NULL REFERENCES transaction_audit_bank_coverages(id) ON DELETE CASCADE, case_id TEXT NOT NULL REFERENCES cases(id), document_id TEXT NOT NULL REFERENCES documents(id), created_at TEXT NOT NULL, UNIQUE(bank_coverage_id, document_id)
                );
                CREATE TABLE IF NOT EXISTS case_related_parties (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), contact_id TEXT NOT NULL REFERENCES contacts(id), relationship_type TEXT NOT NULL, relationship_description TEXT NOT NULL DEFAULT '', effective_from TEXT, effective_to TEXT,
                    source TEXT NOT NULL DEFAULT '', source_document_id TEXT REFERENCES documents(id), confirmed_status TEXT NOT NULL DEFAULT 'UNVERIFIED', confirmed_by TEXT REFERENCES users(id), confirmed_at TEXT, remarks TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(case_id, contact_id, relationship_type, effective_from)
                );
                CREATE TABLE IF NOT EXISTS transaction_review_workstreams (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE, scope_id TEXT REFERENCES transaction_review_scopes(id),
                    review_type TEXT NOT NULL, assigned_to TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'NOT_STARTED', start_date TEXT, completion_date TEXT, review_conclusion TEXT NOT NULL DEFAULT '', notes TEXT NOT NULL DEFAULT '',
                    professional_confirmed_by TEXT REFERENCES users(id), professional_confirmed_at TEXT, created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(case_id, engagement_id, review_type)
                );
                CREATE TABLE IF NOT EXISTS transaction_audit_transactions (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE, workstream_id TEXT REFERENCES transaction_review_workstreams(id),
                    transaction_date TEXT, narration TEXT NOT NULL DEFAULT '', transaction_amount_paise INTEGER NOT NULL DEFAULT 0, counterparty_contact_id TEXT REFERENCES contacts(id), related_party_id TEXT REFERENCES case_related_parties(id),
                    source_reference TEXT NOT NULL DEFAULT '', status TEXT NOT NULL DEFAULT 'UNREVIEWED', data_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS transaction_findings (
                    id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE, workstream_id TEXT REFERENCES transaction_review_workstreams(id),
                    finding_number TEXT NOT NULL, auditor_classification TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', title TEXT NOT NULL, narrative TEXT NOT NULL DEFAULT '', finding_amount_paise INTEGER NOT NULL DEFAULT 0,
                    transaction_amount_paise INTEGER NOT NULL DEFAULT 0, estimated_impact_paise INTEGER NOT NULL DEFAULT 0, amount_recoverable_paise INTEGER NOT NULL DEFAULT 0, auditor_finalized_by TEXT REFERENCES users(id), auditor_finalized_at TEXT,
                    data_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL, UNIQUE(case_id, finding_number)
                );
                CREATE TABLE IF NOT EXISTS transaction_finding_parties (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), finding_id TEXT NOT NULL REFERENCES transaction_findings(id) ON DELETE CASCADE, contact_id TEXT NOT NULL REFERENCES contacts(id), party_role TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(finding_id,contact_id,party_role));
                CREATE TABLE IF NOT EXISTS transaction_audit_source_index (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES documents(id), source_category TEXT NOT NULL, source_reference TEXT NOT NULL DEFAULT '', period_from TEXT, period_to TEXT, received_from TEXT NOT NULL DEFAULT '', received_date TEXT, availability_status TEXT NOT NULL DEFAULT 'RECEIVED', relied_upon INTEGER NOT NULL DEFAULT 0, remarks TEXT NOT NULL DEFAULT '', data_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(engagement_id,document_id,source_reference));
                CREATE TABLE IF NOT EXISTS transaction_finding_evidence (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), finding_id TEXT NOT NULL REFERENCES transaction_findings(id) ON DELETE CASCADE, document_id TEXT NOT NULL REFERENCES documents(id), source_index_id TEXT REFERENCES transaction_audit_source_index(id), page_reference TEXT NOT NULL DEFAULT '', exhibit_reference TEXT NOT NULL DEFAULT '', document_version INTEGER, remarks TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, UNIQUE(finding_id,document_id,page_reference,exhibit_reference));
                CREATE TABLE IF NOT EXISTS transaction_finding_reviews (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), finding_id TEXT NOT NULL REFERENCES transaction_findings(id) ON DELETE CASCADE, review_layer TEXT NOT NULL, status TEXT NOT NULL, opinion_text TEXT NOT NULL DEFAULT '', document_id TEXT REFERENCES documents(id), reviewed_by TEXT NOT NULL REFERENCES users(id), reviewed_at TEXT NOT NULL, created_at TEXT NOT NULL, UNIQUE(finding_id,review_layer));
                CREATE TABLE IF NOT EXISTS avoidance_decisions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT REFERENCES transaction_audit_engagements(id), status TEXT NOT NULL DEFAULT 'DRAFT', decision TEXT NOT NULL DEFAULT 'NO_CURRENT_AVOIDANCE_DECISION', rationale TEXT NOT NULL DEFAULT '', confirmed_by TEXT REFERENCES users(id), confirmed_at TEXT, superseded_by TEXT REFERENCES avoidance_decisions(id), created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS avoidance_decision_findings (id TEXT PRIMARY KEY, decision_id TEXT NOT NULL REFERENCES avoidance_decisions(id) ON DELETE CASCADE, finding_id TEXT NOT NULL REFERENCES transaction_findings(id), created_at TEXT NOT NULL, UNIQUE(decision_id,finding_id));
                CREATE TABLE IF NOT EXISTS avoidance_applications (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), decision_id TEXT NOT NULL REFERENCES avoidance_decisions(id), application_id TEXT REFERENCES applications(id), hearing_id TEXT REFERENCES hearings(id), status TEXT NOT NULL DEFAULT 'DRAFT', ia_number TEXT NOT NULL DEFAULT '', filed_date TEXT, next_hearing_date TEXT, order_document_id TEXT REFERENCES documents(id), data_json TEXT NOT NULL DEFAULT '{}', created_by TEXT NOT NULL REFERENCES users(id), updated_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, updated_at TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS avoidance_application_findings (id TEXT PRIMARY KEY, avoidance_application_id TEXT NOT NULL REFERENCES avoidance_applications(id) ON DELETE CASCADE, finding_id TEXT NOT NULL REFERENCES transaction_findings(id), created_at TEXT NOT NULL, UNIQUE(avoidance_application_id,finding_id));
                CREATE TABLE IF NOT EXISTS transaction_audit_report_versions (id TEXT PRIMARY KEY, case_id TEXT NOT NULL REFERENCES cases(id), engagement_id TEXT NOT NULL REFERENCES transaction_audit_engagements(id) ON DELETE CASCADE, report_type TEXT NOT NULL, version_number INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'DRAFT', document_id TEXT REFERENCES documents(id), received_date TEXT, auditor_contact_id TEXT REFERENCES contacts(id), remarks TEXT NOT NULL DEFAULT '', created_by TEXT NOT NULL REFERENCES users(id), created_at TEXT NOT NULL, finalized_by TEXT REFERENCES users(id), finalized_at TEXT, UNIQUE(engagement_id,report_type,version_number));
                CREATE INDEX IF NOT EXISTS ix_transaction_findings_case ON transaction_findings(case_id,status);
                CREATE INDEX IF NOT EXISTS ix_transaction_requirements_case ON transaction_audit_document_requirements(case_id,status);

                /* CIRP-085--105: one coherent EOI/PRA/resolution-plan domain.
                   The typed aggregate ledger keeps every issued version and
                   professional decision immutable without duplicating the
                   existing document, VDR, CoC, voting, application or hearing
                   engines.  Domain validation lives in phase6_core. */
                CREATE TABLE IF NOT EXISTS resolution_process_records (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_type TEXT NOT NULL,
                    process_id TEXT REFERENCES resolution_process_records(id),
                    pra_id TEXT REFERENCES resolution_process_records(id),
                    plan_id TEXT REFERENCES resolution_process_records(id),
                    parent_record_id TEXT REFERENCES resolution_process_records(id),
                    version_number INTEGER NOT NULL DEFAULT 1,
                    status TEXT NOT NULL DEFAULT 'DRAFT',
                    effective_date TEXT,
                    document_id TEXT REFERENCES documents(id),
                    confidentiality_level TEXT NOT NULL DEFAULT 'RESTRICTED_PRA',
                    data_json TEXT NOT NULL DEFAULT '{}',
                    snapshot_json TEXT NOT NULL DEFAULT '{}',
                    immutable_at TEXT,
                    immutable_by TEXT REFERENCES users(id),
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_resolution_process_case_type
                    ON resolution_process_records(case_id,record_type,status,created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_resolution_process_pra
                    ON resolution_process_records(case_id,pra_id,record_type,created_at DESC);
                CREATE UNIQUE INDEX IF NOT EXISTS ux_resolution_process_idempotency
                    ON resolution_process_records(case_id,idempotency_key)
                    WHERE idempotency_key <> '';

                CREATE TABLE IF NOT EXISTS resolution_process_items (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_id TEXT NOT NULL REFERENCES resolution_process_records(id) ON DELETE CASCADE,
                    item_type TEXT NOT NULL,
                    item_key TEXT NOT NULL,
                    sequence INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL DEFAULT 'REQUIRED',
                    response TEXT NOT NULL DEFAULT '',
                    document_id TEXT REFERENCES documents(id),
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    updated_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(record_id,item_type,item_key)
                );
                CREATE INDEX IF NOT EXISTS ix_resolution_process_items
                    ON resolution_process_items(case_id,record_id,item_type,sequence);

                CREATE TABLE IF NOT EXISTS resolution_process_links (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_id TEXT NOT NULL REFERENCES resolution_process_records(id) ON DELETE CASCADE,
                    linked_record_id TEXT REFERENCES resolution_process_records(id),
                    document_id TEXT REFERENCES documents(id),
                    external_entity_type TEXT NOT NULL DEFAULT '',
                    external_entity_id TEXT NOT NULL DEFAULT '',
                    link_type TEXT NOT NULL,
                    data_json TEXT NOT NULL DEFAULT '{}',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL,
                    UNIQUE(record_id,linked_record_id,document_id,external_entity_type,external_entity_id,link_type)
                );

                CREATE TABLE IF NOT EXISTS resolution_process_dispatches (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    record_id TEXT NOT NULL REFERENCES resolution_process_records(id) ON DELETE CASCADE,
                    pra_id TEXT REFERENCES resolution_process_records(id),
                    recipient TEXT NOT NULL DEFAULT '',
                    dispatch_type TEXT NOT NULL,
                    dispatched_at TEXT NOT NULL,
                    proof_document_id TEXT REFERENCES documents(id),
                    version_snapshot_json TEXT NOT NULL DEFAULT '{}',
                    idempotency_key TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS ux_resolution_dispatch_idempotency
                    ON resolution_process_dispatches(case_id,idempotency_key)
                    WHERE idempotency_key <> '';

                CREATE TABLE IF NOT EXISTS process_deadline_revisions (
                    id TEXT PRIMARY KEY,
                    case_id TEXT NOT NULL REFERENCES cases(id),
                    process_record_id TEXT NOT NULL REFERENCES resolution_process_records(id),
                    deadline_key TEXT NOT NULL,
                    old_date TEXT,
                    new_date TEXT NOT NULL,
                    reason TEXT NOT NULL,
                    approval_source TEXT NOT NULL,
                    coc_meeting_id TEXT REFERENCES coc_meetings(id),
                    coc_resolution_id TEXT REFERENCES coc_resolutions(id),
                    effective_date TEXT NOT NULL,
                    addendum_record_id TEXT REFERENCES resolution_process_records(id),
                    created_by TEXT NOT NULL REFERENCES users(id),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_process_deadline_revision
                    ON process_deadline_revisions(case_id,process_record_id,deadline_key,created_at);

                CREATE TABLE IF NOT EXISTS uat_feedback_reports (
                    id TEXT PRIMARY KEY,
                    case_id TEXT REFERENCES cases(id),
                    reporter_id TEXT NOT NULL REFERENCES users(id),
                    environment TEXT NOT NULL,
                    release_id TEXT NOT NULL,
                    module TEXT NOT NULL,
                    action_taken TEXT NOT NULL,
                    expected_result TEXT NOT NULL,
                    actual_result TEXT NOT NULL,
                    severity TEXT NOT NULL DEFAULT 'MEDIUM',
                    status TEXT NOT NULL DEFAULT 'NEW',
                    triage_notes TEXT NOT NULL DEFAULT '',
                    triaged_by TEXT REFERENCES users(id),
                    triaged_at TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS ix_uat_feedback_reporter
                    ON uat_feedback_reports(reporter_id, created_at DESC);
                CREATE INDEX IF NOT EXISTS ix_uat_feedback_status
                    ON uat_feedback_reports(status, created_at DESC);
                """
            )
            self._ensure_column(connection, "coc_cost_statements", "expense_period_label", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_cost_statements", "cost_kind", "TEXT NOT NULL DEFAULT 'ESTIMATED'")
            self._ensure_column(connection, "coc_cost_statements", "approval_status", "TEXT NOT NULL DEFAULT 'DRAFT'")
            self._ensure_column(connection, "coc_cost_statements", "approved_meeting_id", "TEXT REFERENCES coc_meetings(id)")
            self._ensure_column(connection, "coc_cost_statements", "approved_resolution_id", "TEXT REFERENCES coc_resolutions(id)")
            self._ensure_column(connection, "coc_cost_statement_rows", "expense_period_label", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "coc_cost_statement_rows", "expense_type", "TEXT NOT NULL DEFAULT 'ACTUAL'")
            self._ensure_column(connection, "coc_cost_statement_rows", "vendor_contact_id", "TEXT REFERENCES contacts(id)")
            self._ensure_column(connection, "coc_cost_statement_rows", "approval_status", "TEXT NOT NULL DEFAULT 'DRAFT'")
            self._ensure_column(connection, "coc_cost_statement_rows", "payment_date", "TEXT")
            self._ensure_column(connection, "coc_cost_statement_rows", "payment_reference", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "transaction_audit_source_index", "received_from", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "transaction_audit_source_index", "received_date", "TEXT")
            self._ensure_column(connection, "transaction_audit_source_index", "availability_status", "TEXT NOT NULL DEFAULT 'RECEIVED'")
            self._ensure_column(connection, "transaction_audit_source_index", "relied_upon", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "transaction_audit_source_index", "remarks", "TEXT NOT NULL DEFAULT ''")
            self._ensure_column(connection, "transaction_finding_evidence", "document_version", "INTEGER")
            self._ensure_column(connection, "transaction_audit_report_versions", "received_date", "TEXT")
            self._ensure_column(connection, "transaction_audit_report_versions", "auditor_contact_id", "TEXT REFERENCES contacts(id)")
            self._ensure_column(connection, "avoidance_applications", "hearing_id", "TEXT REFERENCES hearings(id)")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_case_number ON claims(case_id, claim_number) WHERE claim_number <> ''")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_claims_case_idempotency ON claims(case_id, idempotency_key) WHERE idempotency_key IS NOT NULL AND idempotency_key <> ''")
            connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_coc_meetings_case_number ON coc_meetings(case_id, meeting_number) WHERE archived_at IS NULL")
            self._seed_compliance_rules(connection)
            from workflow.seed_loader import seed_cirp_workflow
            seed_cirp_workflow(connection, Path(__file__).parent / "workflow" / "seeds" / "cirp_2026_v1.json")

    @staticmethod
    def _ensure_column(connection: sqlite3.Connection, table: str, column: str, declaration: str) -> None:
        existing = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})").fetchall()}
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {declaration}")

    def _seed_compliance_rules(self, connection: sqlite3.Connection) -> None:
        rules = [
            (0, "CIRP commencement and appointment of IRP", "Section 16(1)"),
            (3, "Public announcement inviting claims", "Regulation 6(1)"),
            (14, "Last date for submission of claims", "Regulations 6(2)(c), 12(1)"),
            (21, "Verification of claims", "Regulation 13(1)"),
            (23, "Report certifying constitution of CoC", "Regulation 17(1)"),
            (30, "First meeting of the CoC", "Section 22(1), Regulation 19(2)"),
            (47, "Appointment of registered valuers", "Regulation 27"),
            (60, "Invitation for expression of interest", "Regulation 36A"),
            (95, "Information Memorandum to CoC", "Regulation 36(1)"),
            (105, "Issue RFRP and evaluation matrix", "Regulation 36B"),
            (150, "Submit CoC-approved resolution plan to AA", "Regulation 39(4)"),
            (180, "Target for approval of resolution plan", "Section 31(1)"),
        ]
        now = utc_now()
        for offset, name, provision in rules:
            rule_id = f"cirp-model-{offset}"
            connection.execute(
                """INSERT OR IGNORE INTO compliance_rules
                (id, process_type, name, provision, offset_days, effective_from,
                 source_reference, created_at, updated_at)
                VALUES (?, 'CIRP', ?, ?, ?, '2016-12-01', 'Model rules: legal review required', ?, ?)""",
                (rule_id, name, provision, offset, now, now),
            )

    @staticmethod
    def row(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for key in list(result):
            if key.endswith("_json"):
                result[key[:-5]] = _from_json(result.pop(key), [] if key in {"checklist_json", "agenda_json"} else {})
            elif key in {"is_conditional", "active", "required", "received", "confidential", "cirp_cost_eligible", "voting_required", "present"}:
                result[key] = bool(result[key])
        return result

    def audit(
        self,
        connection: sqlite3.Connection,
        actor_id: str,
        action: str,
        entity_type: str,
        entity_id: str,
        case_id: Optional[str],
        before: Any = None,
        after: Any = None,
        title: str = "",
    ) -> None:
        now = utc_now()
        connection.execute(
            """INSERT INTO audit_logs
            (id, case_id, actor_id, action, entity_type, entity_id, before_json, after_json, occurred_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (new_id(), case_id, actor_id, action, entity_type, entity_id,
             _json(before) if before is not None else None, _json(after) if after is not None else None, now),
        )
        connection.execute(
            """INSERT INTO activity_events
            (id, case_id, event_type, title, description, entity_type, entity_id, actor_id, occurred_at)
            VALUES (?, ?, ?, ?, '', ?, ?, ?, ?)""",
            (new_id(), case_id, action, title or f"{entity_type.replace('_', ' ').title()} {action}", entity_type, entity_id, actor_id, now),
        )

    def create_uat_feedback(self, payload: Dict[str, Any], actor_id: str, environment: str, release_id: str) -> Dict[str, Any]:
        """Store a concise UAT report without attaching or duplicating case documents."""
        now = utc_now()
        report_id = new_id()
        severity = str(payload.get("severity") or "MEDIUM").upper()
        if severity not in {"LOW", "MEDIUM", "HIGH", "BLOCKER"}:
            raise ValueError("Invalid feedback severity")
        values = {
            "id": report_id,
            "case_id": payload.get("case_id") or None,
            "reporter_id": actor_id,
            "environment": environment,
            "release_id": release_id,
            "module": str(payload["module"]).strip(),
            "action_taken": str(payload["action_taken"]).strip(),
            "expected_result": str(payload["expected_result"]).strip(),
            "actual_result": str(payload["actual_result"]).strip(),
            "severity": severity,
            "status": "NEW",
            "created_at": now,
            "updated_at": now,
        }
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO uat_feedback_reports
                (id,case_id,reporter_id,environment,release_id,module,action_taken,expected_result,actual_result,severity,status,created_at,updated_at)
                VALUES (:id,:case_id,:reporter_id,:environment,:release_id,:module,:action_taken,:expected_result,:actual_result,:severity,:status,:created_at,:updated_at)""",
                values,
            )
            self.audit(connection, actor_id, "UAT_FEEDBACK_REPORTED", "uat_feedback", report_id, values["case_id"], after={key: values[key] for key in ("module", "severity", "status")}, title="UAT feedback reported")
            row = connection.execute("SELECT * FROM uat_feedback_reports WHERE id=?", (report_id,)).fetchone()
            return dict(row)

    def list_uat_feedback(self, reporter_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            query = """SELECT report.*, users.name AS reporter_name, users.role AS reporter_role,
                       cases.name AS case_name, triage_user.name AS triaged_by_name
                       FROM uat_feedback_reports report
                       JOIN users ON users.id=report.reporter_id
                       LEFT JOIN cases ON cases.id=report.case_id
                       LEFT JOIN users triage_user ON triage_user.id=report.triaged_by"""
            params: tuple[Any, ...] = ()
            if reporter_id:
                query += " WHERE report.reporter_id=?"
                params = (reporter_id,)
            query += " ORDER BY CASE report.status WHEN 'NEW' THEN 0 WHEN 'TRIAGED' THEN 1 WHEN 'IN_PROGRESS' THEN 2 ELSE 3 END, report.created_at DESC"
            return [dict(row) for row in connection.execute(query, params).fetchall()]

    def update_uat_feedback(self, report_id: str, status: str, triage_notes: str, actor_id: str) -> Optional[Dict[str, Any]]:
        clean_status = status.upper()
        if clean_status not in {"NEW", "TRIAGED", "IN_PROGRESS", "RESOLVED", "DEFERRED"}:
            raise ValueError("Invalid feedback status")
        now = utc_now()
        with self.transaction() as connection:
            before = connection.execute("SELECT * FROM uat_feedback_reports WHERE id=?", (report_id,)).fetchone()
            if not before:
                return None
            connection.execute(
                """UPDATE uat_feedback_reports SET status=?, triage_notes=?, triaged_by=?, triaged_at=?, updated_at=? WHERE id=?""",
                (clean_status, triage_notes.strip(), actor_id, now, now, report_id),
            )
            after = connection.execute("SELECT * FROM uat_feedback_reports WHERE id=?", (report_id,)).fetchone()
            self.audit(connection, actor_id, "UAT_FEEDBACK_TRIAGED", "uat_feedback", report_id, before["case_id"], before={"status": before["status"]}, after={"status": clean_status}, title="UAT feedback triaged")
            return dict(after)

    def get_profile(self, user_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
            return _from_json(row["data_json"], {}) if row else {}

    def save_profile(self, user_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        now = utc_now()
        clean = {str(key): value for key, value in data.items() if key not in {"_id", "user_id"}}
        clean.update({"user_id": user_id, "updated_at": now})
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO profiles(user_id, data_json, created_at, updated_at) VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET data_json=excluded.data_json, updated_at=excluded.updated_at""",
                (user_id, _json(clean), now, now),
            )
        return clean

    def ensure_admin(self, user_id: str, email: str, name: str, password_hash: str) -> None:
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """INSERT INTO users(id, email, name, role, password_hash, active, created_at, updated_at)
                VALUES (?, ?, ?, 'admin', ?, 1, ?, ?)
                ON CONFLICT(id) DO UPDATE SET email=excluded.email, name=excluded.name,
                role='admin', password_hash=excluded.password_hash, active=1, updated_at=excluded.updated_at""",
                (user_id, email.lower().strip(), name, password_hash, now, now),
            )

    def find_user_by_email(self, email: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE email=? AND active=1", (email.lower().strip(),)).fetchone()
            return dict(row) if row else None

    def get_user(self, user_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=? AND active=1", (user_id,)).fetchone()
            return dict(row) if row else None

    def list_users(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute("SELECT id,email,name,role,active,created_at,updated_at FROM users ORDER BY name").fetchall()
            return [dict(row) | {"active": bool(row["active"])} for row in rows]

    def create_user(self, email: str, name: str, role: str, password_hash: str) -> Dict[str, Any]:
        user_id, now = new_id(), utc_now()
        with self.transaction() as connection:
            connection.execute(
                "INSERT INTO users(id,email,name,role,password_hash,active,created_at,updated_at) VALUES (?,?,?,?,?,1,?,?)",
                (user_id, email.lower().strip(), name.strip(), role, password_hash, now, now),
            )
        return self.get_user(user_id) or {}

    def update_user(self, user_id: str, payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        allowed = {key: payload[key] for key in ("name", "role", "active", "password_hash") if key in payload}
        if not allowed:
            return self.get_user(user_id)
        allowed["updated_at"] = utc_now()
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE users SET {', '.join(f'{key}=?' for key in allowed)} WHERE id=?",
                (*allowed.values(), user_id),
            )
        return self.get_user(user_id) if payload.get("active", True) else {"id": user_id, "active": False}

    @staticmethod
    def has_full_case_access(role: str) -> bool:
        return str(role or "").lower() in FULL_CASE_ACCESS_ROLES

    def user_can_access_case(self, user_id: str, role: str, case_id: str) -> bool:
        if self.has_full_case_access(role):
            return self.get_case(case_id) is not None
        with self.connect() as connection:
            return connection.execute(
                """SELECT 1 FROM case_user_assignments
                JOIN cases ON cases.id=case_user_assignments.case_id
                WHERE case_user_assignments.case_id=? AND case_user_assignments.user_id=?
                AND cases.archived_at IS NULL""", (case_id, user_id),
            ).fetchone() is not None

    def _case_scope(self, user_id: Optional[str], role: Optional[str], column: str = "id") -> tuple[str, tuple[Any, ...]]:
        if user_id is None or role is None or self.has_full_case_access(role):
            return "1=1", ()
        return f"{column} IN (SELECT case_id FROM case_user_assignments WHERE user_id=?)", (user_id,)

    def list_cases(self, include_archived: bool = False, user_id: Optional[str] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
        scope, parameters = self._case_scope(user_id, role)
        archived = "1=1" if include_archived else "archived_at IS NULL"
        with self.connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM cases WHERE {archived} AND {scope} ORDER BY updated_at DESC", parameters
            ).fetchall()
            return [self._case_public(row) for row in rows]

    def list_case_assignments(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            cases = connection.execute("SELECT id,name,petition_number FROM cases WHERE archived_at IS NULL ORDER BY name").fetchall()
            assigned = connection.execute("""SELECT case_id,user_id FROM case_user_assignments
                JOIN users ON users.id=case_user_assignments.user_id WHERE users.active=1""").fetchall()
            by_case: Dict[str, List[str]] = {}
            for row in assigned:
                by_case.setdefault(row["case_id"], []).append(row["user_id"])
            return [{**dict(case), "user_ids": by_case.get(case["id"], [])} for case in cases]

    def set_case_assignments(self, case_id: str, user_ids: Iterable[str], actor_id: str) -> List[Dict[str, Any]]:
        unique_ids = sorted({str(user_id) for user_id in user_ids if str(user_id)})
        now = utc_now()
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            if unique_ids:
                placeholders = ",".join("?" for _ in unique_ids)
                valid = {row["id"] for row in connection.execute(
                    f"SELECT id FROM users WHERE active=1 AND id IN ({placeholders})", unique_ids
                ).fetchall()}
                if set(unique_ids) - valid:
                    raise ValueError("One or more selected users are unavailable")
            before = [row["user_id"] for row in connection.execute(
                "SELECT user_id FROM case_user_assignments WHERE case_id=?", (case_id,)
            ).fetchall()]
            connection.execute("DELETE FROM case_user_assignments WHERE case_id=?", (case_id,))
            connection.executemany(
                "INSERT INTO case_user_assignments(case_id,user_id,assigned_by,assigned_at) VALUES (?,?,?,?)",
                [(case_id, user_id, actor_id, now) for user_id in unique_ids],
            )
            self.audit(connection, actor_id, "assigned", "case_access", case_id, case_id, before=before, after=unique_ids, title="Case user assignments updated")
        return self.list_case_assignments()

    def get_case(self, case_id: str, include_archived: bool = False) -> Optional[Dict[str, Any]]:
        clause = "" if include_archived else "AND archived_at IS NULL"
        with self.connect() as connection:
            row = connection.execute(f"SELECT * FROM cases WHERE id = ? {clause}", (case_id,)).fetchone()
            return self._case_public(row) if row else None

    def _case_public(self, row: sqlite3.Row) -> Dict[str, Any]:
        record = self.row(row)
        legacy = record.pop("legacy_values", {})
        record["values"] = {
            **legacy,
            "cd_name": record["name"],
            "cin": record["cin"],
            "cd_address": record["registered_address"],
            "cd_email": record["registered_email"],
            "nclt_bench": record["nclt_bench"],
            "cp_ib_number": record["petition_number"],
            "applicant_name": record["applicant_name"],
            "creditor_type": record["applicant_category"],
            "nclt_order_date": record["order_date"] or "",
            "cirp_commencement_date": record["commencement_date"] or "",
        }
        record["tables"] = {}
        record["timeline"] = {}
        return record

    def create_case(self, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        case_id, now = new_id(), utc_now()
        values = dict(payload.get("values") or {})
        name = str(payload.get("name") or values.get("cd_name") or "").strip()
        if not name:
            raise ValueError("Company name is required")
        fields = {
            "id": case_id,
            "name": name,
            "internal_reference": payload.get("internal_reference", ""),
            "cin": payload.get("cin", values.get("cin", "")),
            "registered_address": payload.get("registered_address", values.get("cd_address", "")),
            "registered_email": payload.get("registered_email", values.get("cd_email", "")),
            "industry": payload.get("industry", ""),
            "nclt_bench": payload.get("nclt_bench", values.get("nclt_bench", "")),
            "petition_number": payload.get("petition_number", values.get("cp_ib_number", "")),
            "applicant_name": payload.get("applicant_name", values.get("applicant_name", "")),
            "applicant_category": payload.get("applicant_category", values.get("creditor_type", "")),
            "process_type": payload.get("process_type", "CIRP"),
            "order_date": payload.get("order_date", values.get("nclt_order_date") or None),
            "commencement_date": payload.get("commencement_date", values.get("cirp_commencement_date") or None),
            "status": payload.get("status", "active"),
            "current_stage": payload.get("current_stage", "Commencement"),
            "closure_date": payload.get("closure_date") or None,
            "outcome": payload.get("outcome", ""),
            "assigned_user_id": payload.get("assigned_user_id", actor_id),
            "notes": payload.get("notes", ""),
            "risk_level": payload.get("risk_level", "normal"),
        }
        columns = ", ".join(fields)
        placeholders = ", ".join("?" for _ in fields)
        with self.transaction() as connection:
            connection.execute(
                f"""INSERT INTO cases ({columns}, legacy_values_json, created_by, updated_by, created_at, updated_at)
                VALUES ({placeholders}, ?, ?, ?, ?, ?)""",
                (*fields.values(), _json(values), actor_id, actor_id, now, now),
            )
            self.audit(connection, actor_id, "created", "case", case_id, case_id, after=fields, title=f"Case created: {name}")
            self._generate_deadlines(connection, case_id, fields["process_type"], fields["commencement_date"], actor_id)
            connection.execute(
                """INSERT INTO tasks
                (id, case_id, title, category, priority, status, source_type, created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, 'Complete case master information', 'Case setup', 'high', 'open', 'case_creation', ?, ?, ?, ?)""",
                (new_id(), case_id, actor_id, actor_id, now, now),
            )
        return self.get_case(case_id) or {}

    def update_case(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        before = self.get_case(case_id)
        if not before:
            raise KeyError("Case not found")
        values = dict(payload.get("values") or {})
        allowed = [
            "name", "internal_reference", "cin", "registered_address", "registered_email", "industry",
            "nclt_bench", "petition_number", "applicant_name", "applicant_category", "process_type",
            "order_date", "commencement_date", "status", "current_stage", "closure_date", "outcome",
            "assigned_user_id", "notes", "risk_level",
        ]
        aliases = {
            "name": values.get("cd_name"), "cin": values.get("cin"),
            "registered_address": values.get("cd_address"), "registered_email": values.get("cd_email"),
            "nclt_bench": values.get("nclt_bench"), "petition_number": values.get("cp_ib_number"),
            "applicant_name": values.get("applicant_name"), "applicant_category": values.get("creditor_type"),
            "order_date": values.get("nclt_order_date"), "commencement_date": values.get("cirp_commencement_date"),
        }
        changes = {}
        for key in allowed:
            value = payload[key] if key in payload else aliases.get(key)
            if value is not None:
                changes[key] = value
        if not str(changes.get("name", before["name"])).strip():
            raise ValueError("Company name is required")
        now = utc_now()
        set_sql = ", ".join(f"{key} = ?" for key in changes)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE cases SET {set_sql}, legacy_values_json = ?, updated_by = ?, updated_at = ? WHERE id = ? AND archived_at IS NULL",
                (*changes.values(), _json({**before.get("values", {}), **values}), actor_id, now, case_id),
            )
            self.audit(connection, actor_id, "updated", "case", case_id, case_id, before=before, after=changes, title=f"Case updated: {changes.get('name', before['name'])}")
            if "commencement_date" in changes or "process_type" in changes:
                self._generate_deadlines(
                    connection, case_id, changes.get("process_type", before["process_type"]),
                    changes.get("commencement_date", before["commencement_date"]), actor_id, refresh=True,
                )
        return self.get_case(case_id) or {}

    def archive_case(self, case_id: str, actor_id: str) -> bool:
        before = self.get_case(case_id)
        if not before:
            return False
        now = utc_now()
        with self.transaction() as connection:
            connection.execute("UPDATE cases SET archived_at=?, status='archived', updated_by=?, updated_at=? WHERE id=?", (now, actor_id, now, case_id))
            self.audit(connection, actor_id, "archived", "case", case_id, case_id, before=before, title=f"Case archived: {before['name']}")
        return True

    def _generate_deadlines(
        self, connection: sqlite3.Connection, case_id: str, process_type: str,
        commencement_date: Optional[str], actor_id: str, refresh: bool = False,
    ) -> None:
        if not commencement_date:
            return
        try:
            trigger = date.fromisoformat(commencement_date)
        except ValueError:
            return
        rules = connection.execute(
            """SELECT * FROM compliance_rules WHERE process_type=? AND active=1
            AND effective_from <= ? AND (effective_to IS NULL OR effective_to >= ?)
            ORDER BY offset_days""",
            (process_type, commencement_date, commencement_date),
        ).fetchall()
        now = utc_now()
        for rule in rules:
            existing = connection.execute(
                "SELECT id, status, override_reason FROM deadlines WHERE case_id=? AND rule_id=? AND archived_at IS NULL",
                (case_id, rule["id"]),
            ).fetchone()
            due = date.fromordinal(trigger.toordinal() + rule["offset_days"]).isoformat()
            if existing:
                if refresh and existing["status"] == "open" and not existing["override_reason"]:
                    connection.execute(
                        "UPDATE deadlines SET trigger_date=?, calculated_date=?, due_date=?, updated_by=?, updated_at=? WHERE id=?",
                        (commencement_date, due, due, actor_id, now, existing["id"]),
                    )
                continue
            connection.execute(
                """INSERT INTO deadlines
                (id, case_id, rule_id, title, provision, trigger_date, calculated_date, due_date,
                 created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (new_id(), case_id, rule["id"], rule["name"], rule["provision"], commencement_date,
                 due, due, actor_id, actor_id, now, now),
            )

    def migrate_legacy_json(self, legacy_path: Path, actor_id: str) -> Dict[str, int]:
        """Import legacy data once.  The source file is never modified."""
        if not legacy_path.exists():
            return {"cases": 0, "profiles": 0, "documents": 0}
        with self.connect() as connection:
            marker = connection.execute("SELECT 1 FROM schema_migrations WHERE version = -1").fetchone()
        if marker:
            return {"cases": 0, "profiles": 0, "documents": 0}
        try:
            legacy = json.loads(legacy_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {"cases": 0, "profiles": 0, "documents": 0}
        counts = {"cases": 0, "profiles": 0, "documents": 0}
        id_map: Dict[str, str] = {}
        for old in legacy.get("matters", []):
            old_id = str(old.get("id") or "")
            existing = self.get_case(old_id, include_archived=True) if old_id else None
            if existing:
                id_map[old_id] = old_id
                continue
            created = self.create_case(old, actor_id)
            id_map[old_id] = created["id"]
            counts["cases"] += 1
        for user_id, profile in (legacy.get("profiles") or {}).items():
            self.save_profile(str(user_id), dict(profile or {}))
            counts["profiles"] += 1
        with self.transaction() as connection:
            for old in legacy.get("generated_documents", []):
                company = str(old.get("company_name") or "").strip().lower()
                case_id = next((case["id"] for case in self.list_cases() if case["name"].strip().lower() == company), None)
                if not case_id:
                    continue
                document_id = str(old.get("id") or new_id())
                connection.execute(
                    """INSERT OR IGNORE INTO documents
                    (id, case_id, template_id, name, category, status, source_type, metadata_json,
                     created_by, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'Generated document', ?, 'generated', ?, ?, ?, ?, ?)""",
                    (document_id, case_id, old.get("template_id"), old.get("template_name") or "Generated document",
                     old.get("status", "ready"), _json(old), actor_id, actor_id,
                     old.get("created_at") or utc_now(), old.get("created_at") or utc_now()),
                )
                counts["documents"] += 1
            connection.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (-1, ?)", (utc_now(),))
        return counts

    def dashboard(self, user_id: Optional[str] = None, role: Optional[str] = None) -> Dict[str, Any]:
        today = date.today().isoformat()
        case_scope, case_parameters = self._case_scope(user_id, role, "id")
        record_scope, record_parameters = self._case_scope(user_id, role, "case_id")
        with self.connect() as connection:
            counts = connection.execute(
                f"""SELECT
                (SELECT COUNT(*) FROM cases WHERE archived_at IS NULL AND {case_scope}) AS active_cases,
                (SELECT COUNT(*) FROM tasks WHERE archived_at IS NULL AND {record_scope} AND status NOT IN ('completed','cancelled') AND due_date < ?) AS overdue_tasks,
                (SELECT COUNT(*) FROM deadlines WHERE archived_at IS NULL AND {record_scope} AND status='open' AND due_date < ?) AS overdue_deadlines,
                (SELECT COUNT(*) FROM claims WHERE archived_at IS NULL AND {record_scope} AND status IN ('received','under_review','deficient')) AS pending_claims""",
                (*case_parameters, *record_parameters, today, *record_parameters, today, *record_parameters),
            ).fetchone()
            tasks = connection.execute(
                f"""SELECT tasks.*, cases.name AS case_name FROM tasks LEFT JOIN cases ON cases.id=tasks.case_id
                WHERE tasks.archived_at IS NULL AND {record_scope} AND tasks.status NOT IN ('completed','cancelled')
                ORDER BY CASE WHEN due_date IS NULL THEN 1 ELSE 0 END, due_date LIMIT 8""", record_parameters
            ).fetchall()
            hearings = connection.execute(
                f"""SELECT hearings.*, cases.name AS case_name FROM hearings JOIN cases ON cases.id=hearings.case_id
                WHERE hearings.archived_at IS NULL AND {record_scope} AND hearing_at >= ? ORDER BY hearing_at LIMIT 6""",
                (*record_parameters, today),
            ).fetchall()
            activity = connection.execute(
                f"""SELECT activity_events.*, cases.name AS case_name FROM activity_events
                LEFT JOIN cases ON cases.id=activity_events.case_id
                WHERE activity_events.case_id IS NULL OR {record_scope} ORDER BY occurred_at DESC LIMIT 10""", record_parameters
            ).fetchall()
        return {
            "counts": dict(counts),
            "tasks": [self.row(row) for row in tasks],
            "hearings": [self.row(row) for row in hearings],
            "activity": [self.row(row) for row in activity],
        }

    def firm_tasks(self, user_id: Optional[str] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
        scope, parameters = self._case_scope(user_id, role, "tasks.case_id")
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT tasks.*, cases.name AS case_name FROM tasks LEFT JOIN cases ON cases.id=tasks.case_id
                WHERE tasks.archived_at IS NULL AND {scope} ORDER BY
                CASE tasks.status WHEN 'open' THEN 0 WHEN 'in-progress' THEN 1 WHEN 'blocked' THEN 2 ELSE 3 END,
                CASE WHEN tasks.due_date IS NULL THEN 1 ELSE 0 END, tasks.due_date""", parameters
            ).fetchall()
            return [self.row(row) for row in rows]

    def firm_calendar(self, start: Optional[str] = None, end: Optional[str] = None, user_id: Optional[str] = None, role: Optional[str] = None) -> List[Dict[str, Any]]:
        start = start or date.today().replace(day=1).isoformat()
        end = end or date(date.today().year + 1, 12, 31).isoformat()
        task_scope, task_parameters = self._case_scope(user_id, role, "tasks.case_id")
        deadline_scope, deadline_parameters = self._case_scope(user_id, role, "deadlines.case_id")
        hearing_scope, hearing_parameters = self._case_scope(user_id, role, "hearings.case_id")
        meeting_scope, meeting_parameters = self._case_scope(user_id, role, "coc_meetings.case_id")
        with self.connect() as connection:
            rows = connection.execute(
                f"""SELECT tasks.id, tasks.case_id, cases.name case_name, 'task' item_type,
                tasks.title title, tasks.due_date item_date, tasks.status status
                FROM tasks LEFT JOIN cases ON cases.id=tasks.case_id
                WHERE tasks.archived_at IS NULL AND {task_scope} AND tasks.due_date BETWEEN ? AND ?
                UNION ALL
                SELECT deadlines.id, deadlines.case_id, cases.name, 'deadline', deadlines.title,
                deadlines.due_date, deadlines.status FROM deadlines JOIN cases ON cases.id=deadlines.case_id
                WHERE deadlines.archived_at IS NULL AND {deadline_scope} AND deadlines.due_date BETWEEN ? AND ?
                UNION ALL
                SELECT hearings.id, hearings.case_id, cases.name, 'hearing', hearings.purpose,
                hearings.hearing_at, hearings.status FROM hearings JOIN cases ON cases.id=hearings.case_id
                WHERE hearings.archived_at IS NULL AND {hearing_scope} AND substr(hearings.hearing_at,1,10) BETWEEN ? AND ?
                UNION ALL
                SELECT coc_meetings.id, coc_meetings.case_id, cases.name, 'coc-meeting',
                'CoC meeting ' || coc_meetings.meeting_number, coc_meetings.meeting_at, coc_meetings.status
                FROM coc_meetings JOIN cases ON cases.id=coc_meetings.case_id
                WHERE coc_meetings.archived_at IS NULL AND {meeting_scope} AND substr(coc_meetings.meeting_at,1,10) BETWEEN ? AND ?
                ORDER BY item_date""",
                (*task_parameters, start, end, *deadline_parameters, start, end,
                 *hearing_parameters, start, end, *meeting_parameters, start, end),
            ).fetchall()
            return [dict(row) for row in rows]

    def case_report(self, case_id: str) -> Dict[str, Any]:
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            result: Dict[str, Any] = {}
            for label, table in {
                "open_tasks": "tasks", "open_deadlines": "deadlines", "claims": "claims",
                "documents": "documents", "hearings": "hearings", "communications": "communications",
                "assets": "assets", "expenses": "expenses",
            }.items():
                status_clause = " AND status NOT IN ('completed','cancelled','closed')" if table in {"tasks", "deadlines"} else ""
                result[label] = connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE case_id=? AND archived_at IS NULL {status_clause}", (case_id,)
                ).fetchone()[0]
            money = connection.execute(
                "SELECT COALESCE(SUM(claimed_amount),0),COALESCE(SUM(admitted_amount),0) FROM claims WHERE case_id=? AND archived_at IS NULL",
                (case_id,),
            ).fetchone()
            expenses = connection.execute(
                "SELECT COALESCE(SUM(amount+tax_amount),0) FROM expenses WHERE case_id=? AND archived_at IS NULL",
                (case_id,),
            ).fetchone()[0]
            result.update({"claimed_amount": money[0], "admitted_amount": money[1], "expense_total": expenses})
            return result

    def list_compliance_rules(self) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            return [self.row(row) for row in connection.execute("SELECT * FROM compliance_rules ORDER BY process_type, offset_days, version").fetchall()]

    def ensure_case(self, connection: sqlite3.Connection, case_id: str) -> None:
        exists = connection.execute(
            "SELECT 1 FROM cases WHERE id=? AND archived_at IS NULL", (case_id,)
        ).fetchone()
        if not exists:
            raise KeyError("Case not found")

    def list_module_records(self, case_id: str, module: str) -> List[Dict[str, Any]]:
        table = MODULE_TABLES.get(module)
        if not table:
            raise KeyError("Unknown module")
        archived_filter = "" if module in {"claim-documents", "order-directions", "coc-votes"} else "AND archived_at IS NULL"
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            order_by = "position ASC, created_at ASC" if module == "coc-agenda-items" else "created_at DESC"
            rows = connection.execute(
                f"SELECT * FROM {table} WHERE case_id=? {archived_filter} ORDER BY {order_by}",
                (case_id,),
            ).fetchall()
            return [self.row(row) for row in rows]

    @staticmethod
    def _module_values(module: str, payload: Dict[str, Any]) -> Dict[str, Any]:
        values: Dict[str, Any] = {}
        for key in MODULE_FIELDS[module]:
            public_key = key[:-5] if key.endswith("_json") else key
            if public_key in payload:
                values[key] = _json(payload[public_key]) if key.endswith("_json") else payload[public_key]
            elif key in payload:
                values[key] = _json(payload[key]) if key.endswith("_json") else payload[key]
        return values

    def create_module_record(self, case_id: str, module: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        table = MODULE_TABLES.get(module)
        if not table:
            raise KeyError("Unknown module")
        values = self._module_values(module, payload)
        record_id, now = new_id(), utc_now()
        base: Dict[str, Any] = {"id": record_id, "case_id": case_id, **values}
        if module not in {"claim-documents", "order-directions", "coc-votes"}:
            base.update({"created_by": actor_id, "updated_by": actor_id})
        base.update({"created_at": now, "updated_at": now})
        columns = ", ".join(base)
        placeholders = ", ".join("?" for _ in base)
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            connection.execute(f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", tuple(base.values()))
            if module == "hearings":
                self._create_automated_task(connection, case_id, f"Prepare for hearing: {payload.get('purpose') or 'Hearing'}", "Hearing", str(payload.get("hearing_at") or "")[:10] or None, module, record_id, actor_id)
            elif module == "claims":
                self._create_automated_task(connection, case_id, "Verify newly received claim", "Claims", payload.get("received_date"), module, record_id, actor_id)
            elif module == "coc-meetings":
                number = payload.get("meeting_number", "")
                due = str(payload.get("meeting_at") or "")[:10] or None
                self._create_automated_task(connection, case_id, f"Prepare notice and agenda for CoC meeting {number}", "CoC", due, module, record_id, actor_id)
                self._create_automated_task(connection, case_id, f"Prepare minutes for CoC meeting {number}", "CoC", due, module, record_id, actor_id)
            elif module == "order-directions" and not payload.get("task_id"):
                task_id = self._create_automated_task(connection, case_id, str(payload.get("direction") or "Comply with order direction"), "Order direction", payload.get("due_date"), module, record_id, actor_id)
                connection.execute("UPDATE order_directions SET task_id=? WHERE id=? AND case_id=?", (task_id, record_id, case_id))
            self.audit(connection, actor_id, "created", module, record_id, case_id, after=payload, title=f"{module.replace('-', ' ').title()} record created")
        return self.get_module_record(case_id, module, record_id) or {}

    def get_coc_workflow(self, case_id: str, meeting_id: str) -> Dict[str, Any]:
        """Return one meeting with its ordered, historically linked workflow data."""
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            meeting_row = connection.execute(
                "SELECT * FROM coc_meetings WHERE id=? AND case_id=? AND archived_at IS NULL",
                (meeting_id, case_id),
            ).fetchone()
            if not meeting_row:
                raise KeyError("CoC meeting not found")
            agenda_rows = connection.execute(
                """SELECT * FROM coc_agenda_items
                WHERE meeting_id=? AND case_id=? AND archived_at IS NULL
                ORDER BY position, created_at""",
                (meeting_id, case_id),
            ).fetchall()
            attendance_rows = connection.execute(
                """SELECT * FROM coc_attendance
                WHERE meeting_id=? AND case_id=? AND archived_at IS NULL
                ORDER BY present DESC, participant_name""",
                (meeting_id, case_id),
            ).fetchall()
            vote_rows = connection.execute(
                """SELECT coc_votes.*, coc_members.authorized_representative
                FROM coc_votes LEFT JOIN coc_members ON coc_members.id=coc_votes.member_id
                WHERE coc_votes.meeting_id=? AND coc_votes.case_id=? ORDER BY coc_votes.agenda_key, coc_votes.created_at""",
                (meeting_id, case_id),
            ).fetchall()
            member_rows = connection.execute(
                """SELECT coc_members.*, contacts.name AS contact_name, contacts.organization,
                contacts.email, contacts.phone, contacts.address, claims.creditor_category
                FROM coc_members LEFT JOIN contacts ON contacts.id=coc_members.contact_id
                LEFT JOIN claims ON claims.id=coc_members.claim_id
                WHERE coc_members.case_id=? AND coc_members.archived_at IS NULL
                ORDER BY coc_members.voting_share DESC, coc_members.created_at""",
                (case_id,),
            ).fetchall()
            meeting = self.row(meeting_row)
            attendance = [self.row(row) for row in attendance_rows]
            present_share = round(sum(float(row.get("voting_share_snapshot") or 0) for row in attendance if row.get("present")), 6)
            threshold = float(meeting.get("quorum_threshold") or 33)
            return {
                "meeting": meeting,
                "agenda": [self.row(row) for row in agenda_rows],
                "attendance": attendance,
                "votes": [self.row(row) for row in vote_rows],
                "members": [self.row(row) for row in member_rows],
                "quorum": {"present_voting_share": present_share, "threshold": threshold, "met": present_share >= threshold},
            }

    def issue_coc_notice(self, case_id: str, meeting_id: str, actor_id: str) -> Dict[str, Any]:
        """Freeze the notice inputs so later edits cannot alter the issued record."""
        workflow = self.get_coc_workflow(case_id, meeting_id)
        if not workflow["agenda"]:
            raise ValueError("Add at least one agenda item before issuing the notice")
        snapshot = {
            "issued_at": utc_now(),
            "meeting": workflow["meeting"],
            "agenda": workflow["agenda"],
            "members": workflow["members"],
        }
        before = workflow["meeting"]
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                """UPDATE coc_meetings SET notice_snapshot_json=?, status='notice-issued',
                notice_date=COALESCE(notice_date, substr(?,1,10)), updated_by=?, updated_at=?
                WHERE id=? AND case_id=?""",
                (_json(snapshot), now, actor_id, now, meeting_id, case_id),
            )
            self.audit(connection, actor_id, "issued", "coc_notice", meeting_id, case_id, before=before, after=snapshot, title=f"CoC meeting {before['meeting_number']} notice issued")
        return self.get_coc_workflow(case_id, meeting_id)

    @staticmethod
    def _public_announcement_row(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for key, fallback in (
            ("draft_data_json", {}), ("provenance_json", {}),
            ("extraction_json", {}), ("published_review_json", {}),
            ("conflicts_json", []),
        ):
            result[key[:-5]] = _from_json(result.pop(key), fallback)
        return result

    def get_public_announcement(self, case_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            row = connection.execute(
                "SELECT * FROM public_announcements WHERE case_id=?", (case_id,)
            ).fetchone()
            return self._public_announcement_row(row) if row else None

    def save_public_announcement_draft(
        self, case_id: str, data: Dict[str, Any], provenance: Dict[str, Any],
        actor_id: str, document: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Persist editable Form A data and optionally a new immutable DOCX version."""
        now = utc_now()
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            existing = connection.execute("SELECT * FROM public_announcements WHERE case_id=?", (case_id,)).fetchone()
            if existing and existing["status"] in {"PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED", "PUBLISHED"}:
                raise ValueError("Form A cannot be changed after the published-copy review has started")
            announcement_id = existing["id"] if existing else new_id()
            document_id = None
            if document:
                document_id = new_id()
                previous = connection.execute(
                    """SELECT id,version FROM documents WHERE case_id=? AND linked_type='public-announcement'
                    AND linked_id=? AND category='Public Announcement Form A' AND archived_at IS NULL
                    ORDER BY version DESC LIMIT 1""", (case_id, announcement_id),
                ).fetchone()
                version = int(previous["version"]) + 1 if previous else 1
                connection.execute(
                    """INSERT INTO documents
                    (id,case_id,template_id,name,category,status,version,parent_document_id,storage_path,
                     mime_type,source_type,linked_type,linked_id,metadata_json,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?, 'public-announcement-form-a', ?, 'Public Announcement Form A', 'DRAFT', ?, ?, ?,
                    'application/vnd.openxmlformats-officedocument.wordprocessingml.document','public-announcement-generated',
                    'public-announcement',?,?,?,?,?,?)""",
                    (document_id, case_id, document["name"], version, previous["id"] if previous else None,
                     document["storage_path"], announcement_id, _json(document.get("metadata", {})),
                     actor_id, actor_id, now, now),
                )
            if existing:
                connection.execute(
                    """UPDATE public_announcements SET status='DRAFT',draft_data_json=?,provenance_json=?,
                    draft_document_id=COALESCE(?,draft_document_id),draft_generated_at=CASE WHEN ? IS NULL THEN draft_generated_at ELSE ? END,
                    updated_at=? WHERE case_id=?""",
                    (_json(data), _json(provenance), document_id, document_id, now, now, case_id),
                )
            else:
                connection.execute(
                    """INSERT INTO public_announcements
                    (id,case_id,status,draft_data_json,provenance_json,draft_document_id,draft_generated_at,created_at,updated_at)
                    VALUES (?,?,'DRAFT',?,?,?,?,?,?)""",
                    (announcement_id, case_id, _json(data), _json(provenance), document_id,
                     now if document_id else None, now, now),
                )
            action = "generated" if document_id else "saved"
            self.audit(connection, actor_id, action, "public_announcement", announcement_id, case_id,
                       after={"status": "DRAFT", "document_id": document_id},
                       title="Public Announcement Form A generated" if document_id else "Public Announcement Form A draft saved")
        return self.get_public_announcement(case_id) or {}

    def set_public_announcement_stage(self, case_id: str, stage: str, actor_id: str) -> Dict[str, Any]:
        allowed = {"READY_FOR_PUBLICATION", "SENT_FOR_PUBLICATION"}
        if stage not in allowed:
            raise ValueError("Invalid Public Announcement stage")
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM public_announcements WHERE case_id=?", (case_id,)).fetchone()
            if not row or not row["draft_document_id"]:
                raise ValueError("Generate Form A before changing its publication status")
            if stage == "READY_FOR_PUBLICATION" and row["status"] not in {"DRAFT", "READY_FOR_PUBLICATION"}:
                raise ValueError("Only a draft Form A can be marked ready for publication")
            if stage == "SENT_FOR_PUBLICATION" and row["status"] != "READY_FOR_PUBLICATION":
                raise ValueError("Mark Form A ready for publication before marking it sent")
            fields = ("finalised_by=?,finalised_at=?" if stage == "READY_FOR_PUBLICATION" else "sent_by=?,sent_at=?")
            connection.execute(f"UPDATE public_announcements SET status=?,{fields},updated_at=? WHERE case_id=?",
                               (stage, actor_id, now, now, case_id))
            self.audit(connection, actor_id, "finalised" if stage == "READY_FOR_PUBLICATION" else "sent",
                       "public_announcement", row["id"], case_id, before={"status": row["status"]}, after={"status": stage},
                       title="Form A finalised" if stage == "READY_FOR_PUBLICATION" else "Form A marked sent for publication")
        return self.get_public_announcement(case_id) or {}

    def store_published_announcement(
        self, case_id: str, original_name: str, storage_path: str, mime_type: str,
        extraction: Dict[str, Any], review: Dict[str, Any], conflicts: List[Dict[str, Any]], actor_id: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            row = connection.execute("SELECT * FROM public_announcements WHERE case_id=?", (case_id,)).fetchone()
            if not row:
                raise ValueError("Generate Form A before uploading the published announcement")
            if row["status"] not in {"SENT_FOR_PUBLICATION", "PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED"}:
                raise ValueError("Mark Form A sent for publication before uploading the newspaper copy")
            document_id = new_id()
            connection.execute(
                """INSERT INTO documents
                (id,case_id,name,category,status,storage_path,mime_type,source_type,linked_type,linked_id,metadata_json,
                 created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,'Published Public Announcement','PUBLISHED_UPLOADED',?,?,'public-announcement-upload',
                'public-announcement',?,?,?,?,?,?)""",
                (document_id, case_id, original_name, storage_path, mime_type, row["id"],
                 _json({"extraction_method": extraction.get("method"), "page_count": extraction.get("page_count")}),
                 actor_id, actor_id, now, now),
            )
            connection.execute(
                """UPDATE public_announcements SET status='PUBLISHED_UPLOADED',published_document_id=?,
                extraction_method=?,extraction_json=?,published_review_json=?,conflicts_json=?,uploaded_by=?,uploaded_at=?,
                reviewed_by=NULL,reviewed_at=NULL,confirmed_by=NULL,confirmed_at=NULL,updated_at=? WHERE case_id=?""",
                (document_id, extraction.get("method", ""), _json(extraction), _json(review), _json(conflicts),
                 actor_id, now, now, case_id),
            )
            self.audit(connection, actor_id, "uploaded", "public_announcement", row["id"], case_id,
                       after={"published_document_id": document_id, "extraction_method": extraction.get("method")},
                       title="Published Public Announcement uploaded")
        return self.get_public_announcement(case_id) or {}

    def review_published_announcement(
        self, case_id: str, review: Dict[str, Any], conflicts: List[Dict[str, Any]], actor_id: str,
    ) -> Dict[str, Any]:
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM public_announcements WHERE case_id=?", (case_id,)).fetchone()
            if not row or not row["published_document_id"]:
                raise ValueError("Upload the published announcement before reviewing it")
            if row["status"] not in {"PUBLISHED_UPLOADED", "PUBLISHED_REVIEWED"}:
                raise ValueError("A confirmed published announcement cannot be edited")
            previous = _from_json(row["published_review_json"], {})
            connection.execute(
                """UPDATE public_announcements SET status='PUBLISHED_REVIEWED',published_review_json=?,conflicts_json=?,
                reviewed_by=?,reviewed_at=?,updated_at=? WHERE case_id=?""",
                (_json(review), _json(conflicts), actor_id, now, now, case_id),
            )
            corrections = {key: {"before": previous.get(key), "after": value} for key, value in review.items() if previous.get(key) != value}
            self.audit(connection, actor_id, "reviewed", "public_announcement", row["id"], case_id,
                       before=previous, after={"values": review, "corrections": corrections},
                       title="Published Public Announcement extracted values reviewed")
        return self.get_public_announcement(case_id) or {}

    def confirm_published_announcement(self, case_id: str, actor_id: str, accept_conflicts: bool = False) -> Dict[str, Any]:
        """Confirm publication and only then update canonical downstream references."""
        now = utc_now()
        with self.transaction() as connection:
            row = connection.execute("SELECT * FROM public_announcements WHERE case_id=?", (case_id,)).fetchone()
            if not row or not row["published_document_id"]:
                raise ValueError("Upload the published announcement before confirming it")
            if row["status"] != "PUBLISHED_REVIEWED":
                raise ValueError("Complete the mandatory published-announcement review before confirmation")
            conflicts = _from_json(row["conflicts_json"], [])
            if conflicts and not accept_conflicts:
                raise ValueError("Published announcement differs from existing case information. Explicit conflict confirmation is required.")
            review = _from_json(row["published_review_json"], {})
            if not review.get("publication_date") or not review.get("claims_submission_last_date"):
                raise ValueError("Publication date and claims deadline are required before confirmation")
            case_row = connection.execute("SELECT legacy_values_json,current_stage FROM cases WHERE id=?", (case_id,)).fetchone()
            values = _from_json(case_row["legacy_values_json"], {})
            values.update({
                "pa_date": review.get("publication_date"),
                "claim_cutoff_date": review.get("claims_submission_last_date"),
                "public_announcement_id": row["id"],
                "public_announcement_document_id": row["published_document_id"],
            })
            connection.execute(
                "UPDATE cases SET legacy_values_json=?,current_stage='Claims',updated_by=?,updated_at=? WHERE id=?",
                (_json(values), actor_id, now, case_id),
            )
            deadline = connection.execute(
                "SELECT id FROM deadlines WHERE case_id=? AND rule_id='cirp-model-14' AND archived_at IS NULL", (case_id,)
            ).fetchone()
            if deadline:
                connection.execute(
                    """UPDATE deadlines SET trigger_date=?,calculated_date=?,due_date=?,override_reason=?,
                    evidence_document_id=?,updated_by=?,updated_at=? WHERE id=?""",
                    (review.get("publication_date"), review.get("claims_submission_last_date"),
                     review.get("claims_submission_last_date"), "Confirmed published Public Announcement",
                     row["published_document_id"], actor_id, now, deadline["id"]),
                )
            connection.execute(
                """UPDATE public_announcements SET status='PUBLISHED',confirmed_by=?,confirmed_at=?,updated_at=?
                WHERE case_id=?""", (actor_id, now, now, case_id),
            )
            task_exists = connection.execute(
                "SELECT id FROM tasks WHERE case_id=? AND source_type='public_announcement' AND source_id=? AND archived_at IS NULL",
                (case_id, row["id"]),
            ).fetchone()
            if not task_exists:
                self._create_automated_task(connection, case_id, "Collate and verify claims received",
                                            "Claims", review.get("claims_submission_last_date"),
                                            "public_announcement", row["id"], actor_id)
            self.audit(connection, actor_id, "confirmed", "public_announcement", row["id"], case_id,
                       before={"status": row["status"]}, after={"status": "PUBLISHED", "accepted_conflicts": bool(conflicts)},
                       title="Published Public Announcement confirmed; downstream workflow started")
        return self.get_public_announcement(case_id) or {}

    def store_coc_document(
        self, case_id: str, meeting_id: str, document_type: str, name: str,
        storage_path: str, status: str, metadata: Dict[str, Any], actor_id: str,
    ) -> Dict[str, Any]:
        """Store an immutable document version linked to its meeting."""
        now, document_id = utc_now(), new_id()
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            previous = connection.execute(
                """SELECT id,version FROM documents WHERE case_id=? AND linked_type='coc-meeting'
                AND linked_id=? AND category=? AND archived_at IS NULL ORDER BY version DESC LIMIT 1""",
                (case_id, meeting_id, document_type),
            ).fetchone()
            version = int(previous["version"]) + 1 if previous else 1
            parent_id = previous["id"] if previous else None
            connection.execute(
                """INSERT INTO documents
                (id,case_id,template_id,name,category,status,version,parent_document_id,storage_path,
                 mime_type,source_type,linked_type,linked_id,metadata_json,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        'coc-generated','coc-meeting',?,?,?,?,?,?)""",
                (document_id, case_id, f"coc-{document_type}", name, document_type, status,
                 version, parent_id, storage_path, meeting_id, _json(metadata), actor_id, actor_id, now, now),
            )
            self.audit(connection, actor_id, "generated", "document", document_id, case_id, after={"meeting_id": meeting_id, "document_type": document_type, "version": version}, title=f"{document_type} version {version} generated")
        return self.get_module_record(case_id, "documents", document_id) or {}

    def store_coc_constitution_document(
        self, case_id: str, constitution_id: str, name: str, storage_path: str,
        status: str, metadata: Dict[str, Any], actor_id: str,
    ) -> Dict[str, Any]:
        """Store an immutable Constitution Report version linked to one CoC version."""
        now, document_id = utc_now(), new_id()
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            constitution = connection.execute(
                "SELECT 1 FROM coc_constitutions WHERE id=? AND case_id=?", (constitution_id, case_id)
            ).fetchone()
            if not constitution:
                raise KeyError("CoC Constitution not found")
            previous = connection.execute(
                """SELECT id,version FROM documents WHERE case_id=? AND linked_type='coc_constitution'
                AND linked_id=? AND category='CoC Constitution Report' AND archived_at IS NULL
                ORDER BY version DESC LIMIT 1""", (case_id, constitution_id)
            ).fetchone()
            version = int(previous["version"]) + 1 if previous else 1
            parent_id = previous["id"] if previous else None
            connection.execute(
                """INSERT INTO documents
                (id,case_id,template_id,name,category,status,version,parent_document_id,storage_path,
                 mime_type,source_type,linked_type,linked_id,metadata_json,created_by,updated_by,created_at,updated_at)
                VALUES (?,?, 'constitution-coc',?, 'CoC Constitution Report',?,?,?,?,
                        'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
                        'retained-template','coc_constitution',?,?,?,?,?,?)""",
                (document_id, case_id, name, status, version, parent_id, storage_path, constitution_id,
                 _json(metadata), actor_id, actor_id, now, now),
            )
            self.audit(connection, actor_id, "generated", "document", document_id, case_id,
                       after={"constitution_id": constitution_id, "version": version, "status": status},
                       title=f"CoC Constitution Report version {version} generated")
        return self.get_module_record(case_id, "documents", document_id) or {}

    def _create_automated_task(
        self, connection: sqlite3.Connection, case_id: str, title: str, category: str,
        due_date: Optional[str], source_type: str, source_id: str, actor_id: str,
    ) -> str:
        task_id, now = new_id(), utc_now()
        connection.execute(
            """INSERT INTO tasks
            (id, case_id, title, category, priority, status, due_date, source_type, source_id,
             created_by, updated_by, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'normal', 'open', ?, ?, ?, ?, ?, ?, ?)""",
            (task_id, case_id, title, category, due_date, source_type, source_id, actor_id, actor_id, now, now),
        )
        return task_id

    def get_module_record(self, case_id: str, module: str, record_id: str) -> Optional[Dict[str, Any]]:
        table = MODULE_TABLES.get(module)
        if not table:
            raise KeyError("Unknown module")
        with self.connect() as connection:
            row = connection.execute(
                f"SELECT * FROM {table} WHERE id=? AND case_id=?", (record_id, case_id)
            ).fetchone()
            return self.row(row) if row else None

    def update_module_record(self, case_id: str, module: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        table = MODULE_TABLES.get(module)
        if not table:
            raise KeyError("Unknown module")
        before = self.get_module_record(case_id, module, record_id)
        if not before:
            raise KeyError("Record not found")
        values = self._module_values(module, payload)
        if not values:
            return before
        if module not in {"claim-documents", "order-directions", "coc-votes"}:
            values["updated_by"] = actor_id
        values["updated_at"] = utc_now()
        assignments = ", ".join(f"{key}=?" for key in values)
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE {table} SET {assignments} WHERE id=? AND case_id=?",
                (*values.values(), record_id, case_id),
            )
            if module == "claims" and payload.get("status") == "deficient" and before.get("status") != "deficient":
                self._create_automated_task(connection, case_id, "Send claim deficiency communication", "Claims", None, module, record_id, actor_id)
            self.audit(connection, actor_id, "updated", module, record_id, case_id, before=before, after=payload, title=f"{module.replace('-', ' ').title()} record updated")
        return self.get_module_record(case_id, module, record_id) or {}

    def archive_module_record(self, case_id: str, module: str, record_id: str, actor_id: str) -> bool:
        table = MODULE_TABLES.get(module)
        if not table:
            raise KeyError("Unknown module")
        if module in {"claim-documents", "order-directions", "coc-votes"}:
            raise ValueError("This dependent record cannot be archived directly")
        before = self.get_module_record(case_id, module, record_id)
        if not before:
            return False
        now = utc_now()
        with self.transaction() as connection:
            connection.execute(
                f"UPDATE {table} SET archived_at=?, updated_by=?, updated_at=? WHERE id=? AND case_id=?",
                (now, actor_id, now, record_id, case_id),
            )
            self.audit(connection, actor_id, "archived", module, record_id, case_id, before=before, title=f"{module.replace('-', ' ').title()} record archived")
        return True

    def list_contacts(self, case_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            if case_id:
                self.ensure_case(connection, case_id)
                rows = connection.execute(
                    """SELECT contacts.*, case_contacts.id AS case_contact_id, case_contacts.role,
                    case_contacts.category, case_contacts.communication_preference,
                    case_contacts.notes AS case_notes
                    FROM case_contacts JOIN contacts ON contacts.id=case_contacts.contact_id
                    WHERE case_contacts.case_id=? AND case_contacts.archived_at IS NULL
                    AND contacts.archived_at IS NULL ORDER BY contacts.name""",
                    (case_id,),
                ).fetchall()
            else:
                rows = connection.execute("SELECT * FROM contacts WHERE archived_at IS NULL ORDER BY name").fetchall()
            return [self.row(row) for row in rows]

    def create_contact(self, payload: Dict[str, Any], actor_id: str, case_id: Optional[str] = None) -> Dict[str, Any]:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise ValueError("Contact name is required")
        contact_id, now = new_id(), utc_now()
        with self.transaction() as connection:
            if case_id:
                self.ensure_case(connection, case_id)
            connection.execute(
                """INSERT INTO contacts
                (id, kind, name, organization, email, phone, address, identifiers_json, notes,
                 created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (contact_id, payload.get("kind", "person"), name, payload.get("organization", ""),
                 payload.get("email", ""), payload.get("phone", ""), payload.get("address", ""),
                 _json(payload.get("identifiers", {})), payload.get("notes", ""), actor_id, actor_id, now, now),
            )
            if case_id:
                connection.execute(
                    """INSERT INTO case_contacts
                    (id, case_id, contact_id, role, category, communication_preference, notes,
                     created_by, updated_by, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (new_id(), case_id, contact_id, payload.get("role", "Stakeholder"), payload.get("category", ""),
                     payload.get("communication_preference", ""), payload.get("case_notes", ""),
                     actor_id, actor_id, now, now),
                )
            self.audit(connection, actor_id, "created", "contact", contact_id, case_id, after=payload, title=f"Contact created: {name}")
        records = self.list_contacts(case_id)
        return next(item for item in records if item["id"] == contact_id)

    def activity(self, case_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            rows = connection.execute(
                "SELECT * FROM activity_events WHERE case_id=? ORDER BY occurred_at DESC LIMIT ?",
                (case_id, max(1, min(limit, 500))),
            ).fetchall()
            return [self.row(row) for row in rows]

    def store_generated_document(self, record: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        case_id = str(record.get("case_id") or "")
        document_id = str(record["id"])
        now = str(record.get("created_at") or utc_now())
        with self.transaction() as connection:
            self.ensure_case(connection, case_id)
            connection.execute(
                """INSERT INTO documents
                (id, case_id, template_id, name, category, status, source_type, metadata_json,
                 created_by, updated_by, created_at, updated_at)
                VALUES (?, ?, ?, ?, 'Generated document', ?, 'generated', ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET metadata_json=excluded.metadata_json,
                status=excluded.status, updated_by=excluded.updated_by, updated_at=excluded.updated_at""",
                (document_id, case_id, record.get("template_id"), record.get("template_name") or "Generated document",
                 record.get("status", "Ready"), _json(record), actor_id, actor_id, now, now),
            )
            self.audit(connection, actor_id, "generated", "document", document_id, case_id, after=record, title=f"Document generated: {record.get('template_name', 'Document')}")
        return record

    def get_generated_document(self, document_id: str) -> Optional[Dict[str, Any]]:
        with self.connect() as connection:
            row = connection.execute(
                "SELECT metadata_json FROM documents WHERE id=? AND source_type='generated' AND archived_at IS NULL",
                (document_id,),
            ).fetchone()
            return _from_json(row["metadata_json"], {}) if row else None

    def audit_history(self, case_id: str, limit: int = 100) -> List[Dict[str, Any]]:
        with self.connect() as connection:
            self.ensure_case(connection, case_id)
            rows = connection.execute(
                "SELECT * FROM audit_logs WHERE case_id=? ORDER BY occurred_at DESC LIMIT ?",
                (case_id, max(1, min(limit, 500))),
            ).fetchall()
            return [self.row(row) for row in rows]

    def backup(self, destination_dir: Path) -> Path:
        destination_dir.mkdir(parents=True, exist_ok=True)
        output = destination_dir / f"casefile-backup-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
        source = self.connect()
        target = sqlite3.connect(output)
        try:
            source.backup(target)
        finally:
            target.close()
            source.close()
        return output

    def _validate_sqlite_backup_path(self, candidate_path: Path) -> None:
        """Check that a candidate has the minimum Casefile database structure."""
        candidate = sqlite3.connect(candidate_path)
        try:
            integrity = candidate.execute("PRAGMA integrity_check").fetchone()[0]
            tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        finally:
            candidate.close()
        required = {"cases", "tasks", "audit_logs", "schema_migrations", "users"}
        if integrity != "ok" or not required.issubset(tables):
            raise ValueError("The backup is not a valid Casefile database")

    def validate_sqlite_bytes(self, data: bytes) -> None:
        """Validate a backup without changing the live database."""
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.validate.tmp")
        temporary.write_bytes(data)
        try:
            self._validate_sqlite_backup_path(temporary)
        finally:
            temporary.unlink(missing_ok=True)

    def restore_sqlite_bytes(self, data: bytes) -> None:
        """Validate a complete SQLite backup before atomically replacing the database."""
        temporary = self.path.with_name(f".{self.path.name}.{uuid.uuid4().hex}.restore.tmp")
        temporary.write_bytes(data)
        try:
            self._validate_sqlite_backup_path(temporary)
            with self.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            temporary.replace(self.path)
            self.initialize()
        finally:
            temporary.unlink(missing_ok=True)
