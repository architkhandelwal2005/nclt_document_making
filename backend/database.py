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


SCHEMA_VERSION = 7

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
    "claims": ("claimant_contact_id", "creditor_category", "form_type", "received_date", "claimed_amount", "admitted_amount", "security_details", "status", "deficiency_notes", "decision_reason", "revision", "parent_claim_id"),
    "claim-documents": ("claim_id", "name", "required", "received", "document_id", "notes"),
    "coc-members": ("claim_id", "contact_id", "admitted_debt", "voting_share", "valid_from", "valid_to", "authorized_representative"),
    "coc-meetings": ("meeting_number", "meeting_at", "actual_start_at", "actual_end_at", "mode", "venue_or_link", "notice_date", "notice_place", "voting_start", "voting_end", "status", "agenda_json", "notice_snapshot_json", "minutes", "quorum_threshold", "chair_name", "signed_at"),
    "coc-agenda-items": ("meeting_id", "position", "section", "title", "notes", "discussion", "decision", "proposed_resolution", "resolution_text", "voting_required", "status"),
    "coc-attendance": ("meeting_id", "member_id", "participant_name", "organization", "capacity", "email", "present", "attendance_mode", "joined_at", "left_at", "voting_share_snapshot", "notes"),
    "coc-votes": ("meeting_id", "member_id", "agenda_key", "vote", "voting_share", "cast_at"),
    "documents": ("template_id", "name", "category", "status", "version", "parent_document_id", "storage_path", "mime_type", "source_type", "linked_type", "linked_id", "metadata_json"),
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
            self._ensure_column(connection, "ai_jobs", "api_calls", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "ai_jobs", "latency_ms", "INTEGER NOT NULL DEFAULT 0")
            self._ensure_column(connection, "ai_jobs", "actual_cost", "REAL")
            self._ensure_column(connection, "ai_jobs", "estimated_list_cost", "REAL")
            self._ensure_column(connection, "coc_meetings", "signed_at", "TEXT")
            self._seed_compliance_rules(connection)

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

    def restore_sqlite_bytes(self, data: bytes) -> None:
        """Validate a complete SQLite backup before atomically replacing the database."""
        temporary = self.path.with_suffix(".restore.tmp")
        temporary.write_bytes(data)
        try:
            candidate = sqlite3.connect(temporary)
            try:
                integrity = candidate.execute("PRAGMA integrity_check").fetchone()[0]
                tables = {row[0] for row in candidate.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            finally:
                candidate.close()
            required = {"cases", "tasks", "audit_logs", "schema_migrations", "users"}
            if integrity != "ok" or not required.issubset(tables):
                raise ValueError("The backup is not a valid Casefile database")
            with self.connect() as connection:
                connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            temporary.replace(self.path)
            self.initialize()
        finally:
            temporary.unlink(missing_ok=True)
