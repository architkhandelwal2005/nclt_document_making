"""Data-driven CIRP workflow, event, deadline, task, approval and evidence engine."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import sqlite3

from database import CasefileDatabase, _from_json, _json, new_id, utc_now


DEFAULT_WORKFLOW_VERSION = "CIRP_2026_V1"

STEP_STATUSES = {
    "NOT_TRIGGERED", "READY", "IN_PROGRESS", "WAITING", "BLOCKED",
    "PENDING_APPROVAL", "COMPLETED", "WAIVED", "CANCELLED",
}
TERMINAL_STEP_STATUSES = {"COMPLETED", "WAIVED", "CANCELLED"}
EVENT_STATUSES = {"PENDING_REVIEW", "CONFIRMED", "REJECTED", "SUPERSEDED"}
EVENT_TYPES = {
    "CASE_CREATED",
    "ADMISSION_ORDER_UPLOADED",
    "ADMISSION_ORDER_CONFIRMED",
    "CIRP_COMMENCEMENT_CONFIRMED",
    "IRP_APPOINTMENT_CONFIRMED",
    "PUBLIC_ANNOUNCEMENT_DRAFT_READY",
    "PUBLIC_ANNOUNCEMENT_SENT_FOR_PUBLICATION",
    "PUBLIC_ANNOUNCEMENT_PUBLISHED",
    "PUBLIC_ANNOUNCEMENT_CONFIRMED",
    "DOCUMENT_RECEIVED",
    "WORKFLOW_STEP_COMPLETED",
    "CLAIM_RECEIVED", "CLAIM_ACKNOWLEDGED", "CLAIM_CLASSIFICATION_CONFIRMED",
    "CLAIM_DEFICIENCY_IDENTIFIED", "CLAIM_QUERY_CREATED", "CLAIM_QUERY_SENT",
    "CLAIM_QUERY_RESPONSE_RECEIVED", "CLAIM_QUERY_CLOSED", "CLAIM_VERIFICATION_STARTED",
    "CLAIM_VERIFICATION_COMPLETED", "CLAIM_ADMITTED", "CLAIM_PARTLY_ADMITTED",
    "CLAIM_NOT_ADMITTED", "CLAIM_REVISED", "LATE_CLAIM_IDENTIFIED",
    "RELATED_PARTY_STATUS_CONFIRMED", "RELATED_PARTY_STATUS_CHANGED", "SECURITY_REVIEW_REQUIRED",
    "LIST_OF_CREDITORS_SNAPSHOT_CREATED", "LIST_OF_CREDITORS_PUBLISHED",
    "COC_ELIGIBILITY_CONFIRMED", "COC_VOTING_SHARE_CALCULATED", "CREDITORS_IN_CLASS_CONFIRMED",
    "AR_REQUIRED", "COC_CONSTITUTED", "COC_REVIEW_REQUIRED", "COC_RECONSTITUTED",
    "COC_CONSTITUTION_REPORT_GENERATED", "COC_CONSTITUTION_REPORT_FINALIZED",
    "FIRST_COC_MEETING_REQUIRED", "COC_MEETING_CREATED", "COC_MEETING_SCHEDULED",
    "COC_NOTICE_DRAFTED", "COC_AGENDA_FINALIZED", "COC_NOTICE_ISSUED", "COC_NOTICE_DISPATCH_RECORDED",
    "COC_ATTENDANCE_RECORDED", "COC_QUORUM_CONFIRMED", "COC_QUORUM_NOT_MET", "COC_MEETING_STARTED",
    "COC_MEETING_HELD", "COC_MEETING_ADJOURNED", "COC_RESOLUTION_PLACED", "COC_COST_STATEMENT_RECORDED",
    "COC_OPERATIONS_UPDATE_RECORDED", "COC_PROFESSIONAL_PROPOSAL_RECORDED", "COC_VOTING_OPENED", "COC_VOTE_RECORDED",
    "COC_VOTING_CLOSED", "COC_MINUTES_CONTENT_READY", "COC_MINUTES_DRAFTED", "COC_MINUTES_FINALIZED",
    "COC_MINUTES_CIRCULATED", "COC_VOTING_RESULT_FINALIZED", "COC_ATR_CREATED",
    "MEETING_MEMBERSHIP_REVIEW_REQUIRED",
    # Operations, valuation, IM and VDR (CIRP-057--076).
    "GOING_CONCERN_ASSESSMENT_CREATED", "GOING_CONCERN_ASSESSMENT_APPROVED",
    "CASH_FLOW_PERIOD_CREATED", "CASH_FLOW_PERIOD_FINALIZED", "BANK_RECONCILIATION_RECORDED",
    "RECEIVABLE_CREATED", "RECEIVABLE_FOLLOWUP_RECORDED", "RECEIVABLE_COLLECTION_RECEIVED",
    "ASSET_MOVEMENT_RECORDED", "ASSET_PROTECTION_INCIDENT_RECORDED",
    "STATUTORY_COMPLIANCE_DUE", "STATUTORY_COMPLIANCE_FILED", "MORATORIUM_REVIEW_REQUIRED",
    "MANAGEMENT_REQUISITION_SENT", "MANAGEMENT_REMINDER_SENT", "MANAGEMENT_FINAL_DEMAND_SENT",
    "MANAGEMENT_RESPONSE_RECEIVED", "MANAGEMENT_DEFICIENCY_IDENTIFIED", "NON_COOPERATION_REVIEW_REQUIRED",
    "SECTION_19_APPLICATION_APPROVED", "SECTION_19_APPLICATION_FILED",
    "INTERIM_FINANCE_PROPOSED", "SECTION_28_REVIEW_CREATED", "SECTION_28_APPROVAL_REQUIRED",
    "VALUATION_REQUIREMENT_RECORDED", "VALUER_QUOTATION_REQUESTED", "VALUER_QUOTATION_INVITED", "VALUER_APPOINTMENT_ISSUED",
    "VALUER_DECLARATION_RECEIVED", "VALUATION_DATA_PACK_READY", "VALUATION_REPORT_RECEIVED",
    "VALUATION_QUERY_RECORDED", "IM_DATA_COLLECTION_STARTED", "IM_FINALIZED",
    "CONFIDENTIALITY_UNDERTAKING_ISSUED", "CONFIDENTIALITY_UNDERTAKING_VERIFIED",
    "VDR_ACCESS_GRANTED", "VDR_ACCESS_REVOKED", "VDR_DOCUMENT_PUBLISHED",
    # Transaction audit and avoidance (CIRP-077--084). These events record
    # human-controlled process stages; they do not create legal conclusions.
    "TRANSACTION_AUDIT_REQUIRED", "TRANSACTION_AUDITOR_QUOTATION_INVITED", "TRANSACTION_AUDITOR_QUOTATION_RECEIVED", "TRANSACTION_AUDITOR_SELECTED", "TRANSACTION_AUDITOR_APPOINTED",
    "TRANSACTION_REVIEW_SCOPE_DRAFTED", "TRANSACTION_REVIEW_SCOPE_CONFIRMED", "TRANSACTION_AUDIT_DOCUMENT_REQUESTED", "TRANSACTION_AUDIT_DOCUMENT_RECEIVED", "TRANSACTION_AUDIT_DATA_GAP_RECORDED",
    "TRANSACTION_AUDIT_DRAFT_REPORT_RECEIVED", "TRANSACTION_AUDIT_RP_COMMENTS_RECORDED", "TRANSACTION_AUDIT_FINAL_REPORT_RECEIVED",
    "TRANSACTION_FINDING_CREATED", "TRANSACTION_FINDING_FINALIZED", "TRANSACTION_FINDING_EVIDENCE_LINKED", "TRANSACTION_FINDING_RP_REVIEWED", "TRANSACTION_FINDING_LEGAL_REVIEWED",
    "AVOIDANCE_DECISION_CONFIRMED", "AVOIDANCE_APPLICATION_CREATED", "AVOIDANCE_APPLICATION_FILED",
}


class WorkflowError(Exception):
    """Stable service error carrying an API-safe code and HTTP status."""

    def __init__(self, code: str, message: str, status_code: int = 422):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code

    def as_detail(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message}


def _parse_date(value: Optional[str], code: str = "MISSING_ANCHOR_DATE") -> date:
    text = str(value or "").strip()[:10]
    if not text:
        raise WorkflowError(code, "A confirmed ISO date is required.")
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise WorkflowError(code, f"Invalid ISO date: {value}") from exc


def _bool_fields(record: Dict[str, Any], fields: Iterable[str]) -> Dict[str, Any]:
    for field in fields:
        if field in record:
            record[field] = bool(record[field])
    return record


class DeadlineEngine:
    """Calculate versioned statutory dates from confirmed event anchors."""

    def __init__(self, database: CasefileDatabase):
        self.database = database

    @staticmethod
    def calculate(anchor_date: str, rule: sqlite3.Row) -> str:
        anchor = _parse_date(anchor_date)
        if rule["offset_days"] is None:
            raise WorkflowError("MISSING_ANCHOR_DATE", "This deadline requires legal or user determination.")
        if rule["calendar_basis"] != "CALENDAR_DAYS":
            raise WorkflowError("MISSING_ANCHOR_DATE", "Unsupported calendar basis requires review.")
        multiplier = -1 if rule["offset_direction"] == "BEFORE" else 1
        return (anchor + timedelta(days=multiplier * int(rule["offset_days"]))).isoformat()

    def ensure_for_workflow(self, connection: sqlite3.Connection, case_id: str, workflow_id: str,
                            actor_id: Optional[str] = None) -> List[str]:
        rules = connection.execute(
            """SELECT dr.*,cws.id AS case_step_id,cws.status AS step_status
            FROM case_workflow_steps cws
            JOIN deadline_rules dr ON dr.step_definition_id=cws.step_definition_id
            WHERE cws.case_workflow_id=? ORDER BY cws.created_at""",
            (workflow_id,),
        ).fetchall()
        now = utc_now()
        touched: List[str] = []
        for rule in rules:
            anchor = connection.execute(
                """SELECT * FROM case_events
                WHERE case_id=? AND event_type=? AND status='CONFIRMED'
                ORDER BY event_date,created_at LIMIT 1""",
                (case_id, rule["anchor_event_type"]),
            ).fetchone()
            calculated: Optional[str] = None
            status = "REVIEW_REQUIRED"
            if anchor and rule["offset_days"] is not None:
                try:
                    calculated = self.calculate(anchor["event_date"], rule)
                    status = "OPEN"
                except WorkflowError:
                    calculated = None
            existing = connection.execute(
                "SELECT * FROM case_deadlines WHERE case_workflow_step_id=? AND deadline_rule_id=?",
                (rule["case_step_id"], rule["id"]),
            ).fetchone()
            deadline_id = existing["id"] if existing else new_id()
            changed_calculation = bool(calculated) and (
                not existing or existing["calculated_due_date"] != calculated
            )
            if existing:
                # A reviewed override is immutable unless the dedicated override API changes it.
                connection.execute(
                    """UPDATE case_deadlines SET anchor_event_id=?,anchor_date=?,calculated_due_date=?,
                    status=CASE WHEN override_due_date IS NOT NULL THEN status ELSE ? END,updated_at=? WHERE id=?""",
                    (anchor["id"] if anchor else None, anchor["event_date"] if anchor else None,
                     calculated, status, now, deadline_id),
                )
            else:
                connection.execute(
                    """INSERT INTO case_deadlines
                    (id,case_id,case_workflow_step_id,deadline_rule_id,anchor_event_id,anchor_date,
                     calculated_due_date,status,rule_version,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                    (deadline_id, case_id, rule["case_step_id"], rule["id"],
                     anchor["id"] if anchor else None, anchor["event_date"] if anchor else None,
                     calculated, status, rule["rule_version"], now, now),
                )
            connection.execute(
                "UPDATE case_workflow_steps SET statutory_due_date=?,updated_at=? WHERE id=?",
                (calculated, now, rule["case_step_id"]),
            )
            if changed_calculation and actor_id:
                self.database.audit(
                    connection, actor_id, "DEADLINE_CALCULATED", "case_deadline", deadline_id, case_id,
                    after={"workflow_step_id": rule["case_step_id"], "anchor_event_id": anchor["id"],
                           "anchor_date": anchor["event_date"], "calculated_due_date": calculated,
                           "rule_version": rule["rule_version"]},
                    title=f"Workflow deadline calculated: {calculated}",
                )
            touched.append(deadline_id)
        return touched

    def override(self, case_id: str, case_step_id: str, due_date: str, reason: str, actor_id: str) -> Dict[str, Any]:
        _parse_date(due_date, "INVALID_DEADLINE_OVERRIDE")
        if not str(reason or "").strip():
            raise WorkflowError("INVALID_DEADLINE_OVERRIDE", "A reason is required for a deadline override.")
        now = utc_now()
        with self.database.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM case_deadlines WHERE case_id=? AND case_workflow_step_id=?",
                (case_id, case_step_id),
            ).fetchone()
            if not row:
                raise WorkflowError("MISSING_ANCHOR_DATE", "No deadline record exists for this workflow step.", 404)
            before = dict(row)
            connection.execute(
                """UPDATE case_deadlines SET override_due_date=?,override_reason=?,override_by=?,
                status='OPEN',updated_at=? WHERE id=?""",
                (due_date[:10], reason.strip(), actor_id, now, row["id"]),
            )
            self.database.audit(
                connection, actor_id, "DEADLINE_OVERRIDDEN", "case_deadline", row["id"], case_id,
                before=before, after={"override_due_date": due_date[:10], "override_reason": reason.strip()},
                title="Workflow deadline overridden",
            )
            updated = connection.execute("SELECT * FROM case_deadlines WHERE id=?", (row["id"],)).fetchone()
            return dict(updated)


class WorkflowService:
    """Manage master definitions and case-specific workflow execution."""

    def __init__(self, database: CasefileDatabase, workflow_version: str = DEFAULT_WORKFLOW_VERSION):
        self.database = database
        self.workflow_version = workflow_version
        self.deadlines = DeadlineEngine(database)

    @staticmethod
    def _definition_public(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        return _bool_fields(result, (
            "requires_coc_approval", "requires_nclt_filing", "requires_ibbi_filing",
            "creates_task", "is_active",
        ))

    def definitions(self, workflow_version: Optional[str] = None) -> List[Dict[str, Any]]:
        version = workflow_version or self.workflow_version
        with self.database.connect() as connection:
            rows = connection.execute(
                """SELECT wsd.*,wp.code AS phase_code,wp.name AS phase_name,wp.sequence AS phase_sequence
                FROM workflow_step_definitions wsd JOIN workflow_phases wp ON wp.id=wsd.phase_id
                WHERE wsd.workflow_version=? AND wsd.is_active=1
                ORDER BY wsd.sequence""",
                (version,),
            ).fetchall()
            return [self._definition_public(row) for row in rows]

    def definition(self, step_code: str, workflow_version: Optional[str] = None) -> Dict[str, Any]:
        version = workflow_version or self.workflow_version
        with self.database.connect() as connection:
            row = connection.execute(
                """SELECT wsd.*,wp.code AS phase_code,wp.name AS phase_name
                FROM workflow_step_definitions wsd JOIN workflow_phases wp ON wp.id=wsd.phase_id
                WHERE wsd.workflow_version=? AND wsd.step_code=? AND wsd.is_active=1
                ORDER BY wsd.rule_version DESC LIMIT 1""",
                (version, step_code.upper()),
            ).fetchone()
            if not row:
                raise WorkflowError("WORKFLOW_DEFINITION_NOT_FOUND", f"Workflow definition {step_code} was not found.", 404)
            result = self._definition_public(row)
            result["deadline_rules"] = [dict(item) for item in connection.execute(
                "SELECT * FROM deadline_rules WHERE step_definition_id=? ORDER BY rule_version", (row["id"],)
            ).fetchall()]
            result["evidence_requirements"] = [
                _bool_fields(dict(item), ("mandatory",)) for item in connection.execute(
                    "SELECT * FROM workflow_evidence_requirements WHERE step_definition_id=? ORDER BY evidence_type",
                    (row["id"],),
                ).fetchall()
            ]
            result["template_links"] = [dict(item) for item in connection.execute(
                "SELECT * FROM workflow_template_links WHERE step_definition_id=? ORDER BY template_role",
                (row["id"],),
            ).fetchall()]
            return result

    def _ensure_workflow(self, connection: sqlite3.Connection, case_id: str, actor_id: str,
                         workflow_version: Optional[str] = None) -> tuple[str, bool]:
        version = workflow_version or self.workflow_version
        self.database.ensure_case(connection, case_id)
        existing = connection.execute(
            "SELECT * FROM case_workflows WHERE case_id=? AND workflow_type='CIRP'", (case_id,)
        ).fetchone()
        if existing:
            # The same versioned master may be implemented in backend phases.  Add
            # only missing definition instances; never replace existing state.
            definitions = connection.execute(
                """SELECT * FROM workflow_step_definitions WHERE workflow_version=?
                AND workflow_type='CIRP' AND is_active=1 ORDER BY sequence""",
                (existing["workflow_version"],),
            ).fetchall()
            now = utc_now()
            for definition in definitions:
                connection.execute(
                    """INSERT OR IGNORE INTO case_workflow_steps
                    (id,case_workflow_id,case_id,step_definition_id,status,approval_status,priority,created_at,updated_at)
                    VALUES (?,?,?,?, 'NOT_TRIGGERED', ?,?,?,?)""",
                    (new_id(), existing["id"], case_id, definition["id"],
                     "NOT_REQUESTED" if definition["approval_role"] else "NOT_REQUIRED",
                     definition["default_priority"], now, now),
                )
            self.deadlines.ensure_for_workflow(connection, case_id, existing["id"], actor_id)
            return existing["id"], False
        definitions = connection.execute(
            """SELECT * FROM workflow_step_definitions
            WHERE workflow_version=? AND workflow_type='CIRP' AND is_active=1 ORDER BY sequence""",
            (version,),
        ).fetchall()
        if not definitions:
            raise WorkflowError("WORKFLOW_DEFINITION_NOT_FOUND", f"No active definitions exist for {version}.", 404)
        workflow_id, now = new_id(), utc_now()
        connection.execute(
            """INSERT INTO case_workflows
            (id,case_id,workflow_type,workflow_version,status,started_at,created_by,updated_by,created_at,updated_at)
            VALUES (?,?,'CIRP',?,'ACTIVE',?,?,?,?,?)""",
            (workflow_id, case_id, version, now, actor_id, actor_id, now, now),
        )
        for definition in definitions:
            connection.execute(
                """INSERT INTO case_workflow_steps
                (id,case_workflow_id,case_id,step_definition_id,status,approval_status,priority,created_at,updated_at)
                VALUES (?,?,?,?, 'NOT_TRIGGERED', ?,?,?,?)""",
                (new_id(), workflow_id, case_id, definition["id"],
                 "NOT_REQUESTED" if definition["approval_role"] else "NOT_REQUIRED",
                 definition["default_priority"], now, now),
            )
        self.deadlines.ensure_for_workflow(connection, case_id, workflow_id)
        self.database.audit(
            connection, actor_id, "CASE_WORKFLOW_INITIALIZED", "case_workflow", workflow_id, case_id,
            after={"workflow_type": "CIRP", "workflow_version": version, "steps": len(definitions)},
            title=f"CIRP workflow {version} initialized",
        )
        return workflow_id, True

    def initialize_cirp(self, case_id: str, actor_id: str,
                        workflow_version: Optional[str] = None) -> Dict[str, Any]:
        with self.database.transaction() as connection:
            workflow_id, created = self._ensure_workflow(connection, case_id, actor_id, workflow_version)
            events = connection.execute(
                "SELECT * FROM case_events WHERE case_id=? AND status='CONFIRMED' ORDER BY event_date,created_at",
                (case_id,),
            ).fetchall()
            for event in events:
                self._activate_for_event(connection, case_id, event, actor_id)
            self.deadlines.ensure_for_workflow(connection, case_id, workflow_id, actor_id)
        result = self.get_case_workflow(case_id)
        result["created"] = created
        result["idempotent_replay"] = not created
        return result

    def _workflow_row(self, connection: sqlite3.Connection, case_id: str) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM case_workflows WHERE case_id=? AND workflow_type='CIRP'", (case_id,)
        ).fetchone()
        if not row:
            raise WorkflowError("WORKFLOW_NOT_INITIALIZED", "The CIRP workflow has not been initialized.", 404)
        return row

    def _unmet_required_dependencies(self, connection: sqlite3.Connection, case_step_id: str) -> int:
        return int(connection.execute(
            """SELECT COUNT(*) FROM workflow_dependencies wd
            JOIN case_workflow_steps child ON child.step_definition_id=wd.child_step_definition_id
            JOIN case_workflow_steps parent ON parent.case_workflow_id=child.case_workflow_id
                AND parent.step_definition_id=wd.parent_step_definition_id
            WHERE child.id=? AND wd.dependency_type='REQUIRES_COMPLETION'
              AND parent.status NOT IN ('COMPLETED','WAIVED')""",
            (case_step_id,),
        ).fetchone()[0])

    def _ensure_task(self, connection: sqlite3.Connection, case_step_id: str, actor_id: str) -> Optional[str]:
        step = connection.execute(
            """SELECT cws.*,wsd.task_title,wsd.activity,wsd.area,wsd.creates_task,wsd.default_priority
            FROM case_workflow_steps cws JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cws.id=?""",
            (case_step_id,),
        ).fetchone()
        if not step or not step["creates_task"]:
            return None
        linked = connection.execute("SELECT id FROM tasks WHERE workflow_step_id=?", (case_step_id,)).fetchone()
        if linked:
            return linked["id"]
        title = step["task_title"] or step["area"] or "CIRP workflow action"
        reusable = connection.execute(
            """SELECT id FROM tasks WHERE case_id=? AND workflow_step_id IS NULL AND archived_at IS NULL
            AND LOWER(title)=LOWER(?) AND status NOT IN ('cancelled') ORDER BY created_at LIMIT 1""",
            (step["case_id"], title),
        ).fetchone()
        if reusable:
            connection.execute(
                "UPDATE tasks SET workflow_step_id=?,updated_by=?,updated_at=? WHERE id=?",
                (case_step_id, actor_id, utc_now(), reusable["id"]),
            )
            return reusable["id"]
        task_id, now = new_id(), utc_now()
        connection.execute(
            """INSERT INTO tasks
            (id,case_id,title,description,category,priority,status,due_date,source_type,source_id,
             workflow_step_id,created_by,updated_by,created_at,updated_at)
            VALUES (?,?,?,?,?,?,'open',?,'workflow_step',?,?,?,?,?,?)""",
            (task_id, step["case_id"], title, step["activity"], step["area"], step["default_priority"],
             step["statutory_due_date"], case_step_id, case_step_id, actor_id, actor_id, now, now),
        )
        self.database.audit(
            connection, actor_id, "WORKFLOW_TASK_CREATED", "task", task_id, step["case_id"],
            after={"workflow_step_id": case_step_id, "title": title}, title=f"Workflow task created: {title}",
        )
        return task_id

    def _activate_for_event(self, connection: sqlite3.Connection, case_id: str,
                            event: sqlite3.Row, actor_id: str) -> List[str]:
        workflow = connection.execute(
            "SELECT * FROM case_workflows WHERE case_id=? AND workflow_type='CIRP'", (case_id,)
        ).fetchone()
        if not workflow:
            return []
        self.deadlines.ensure_for_workflow(connection, case_id, workflow["id"], actor_id)
        rows = connection.execute(
            """SELECT cws.id,cws.status,wsd.step_code
            FROM case_workflow_steps cws JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cws.case_workflow_id=? AND wsd.trigger_event_type=? AND wsd.is_active=1
            ORDER BY wsd.sequence""",
            (workflow["id"], event["event_type"]),
        ).fetchall()
        activated: List[str] = []
        for row in rows:
            if row["status"] in TERMINAL_STEP_STATUSES:
                continue
            status = "WAITING" if self._unmet_required_dependencies(connection, row["id"]) else "READY"
            if row["status"] in {"NOT_TRIGGERED", "WAITING"}:
                connection.execute(
                    """UPDATE case_workflow_steps SET status=?,trigger_event_id=?,trigger_date=?,updated_at=?
                    WHERE id=?""",
                    (status, event["id"], event["event_date"], utc_now(), row["id"]),
                )
                if status == "READY":
                    self._ensure_task(connection, row["id"], actor_id)
                self.database.audit(
                    connection, actor_id, "WORKFLOW_STEP_ACTIVATED", "case_workflow_step", row["id"], case_id,
                    after={"step_code": row["step_code"], "status": status, "trigger_event_id": event["id"]},
                    title=f"{row['step_code']} activated",
                )
                activated.append(row["id"])
        return activated

    def _step_row(self, connection: sqlite3.Connection, case_id: str, step_id: str) -> sqlite3.Row:
        row = connection.execute(
            """SELECT cws.*,wsd.step_code,wsd.activity,wsd.approval_role,wsd.standard_output
            FROM case_workflow_steps cws JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cws.id=? AND cws.case_id=?""",
            (step_id, case_id),
        ).fetchone()
        if not row:
            raise WorkflowError("WORKFLOW_STEP_NOT_FOUND", "Workflow step was not found for this case.", 404)
        return row

    def _sync_task_status(self, connection: sqlite3.Connection, step_id: str, status: str,
                          actor_id: str, completed_at: Optional[str] = None) -> None:
        mapping = {
            "READY": "open", "IN_PROGRESS": "in-progress", "WAITING": "blocked",
            "BLOCKED": "blocked", "PENDING_APPROVAL": "in-progress", "COMPLETED": "completed",
            "WAIVED": "cancelled", "CANCELLED": "cancelled",
        }
        task_status = mapping.get(status)
        if task_status:
            connection.execute(
                """UPDATE tasks SET status=?,completed_at=?,updated_by=?,updated_at=?
                WHERE workflow_step_id=? AND archived_at IS NULL""",
                (task_status, completed_at if task_status == "completed" else None, actor_id, utc_now(), step_id),
            )

    def _missing_mandatory_evidence(self, connection: sqlite3.Connection, step: sqlite3.Row) -> List[str]:
        rows = connection.execute(
            """SELECT wer.evidence_type FROM workflow_evidence_requirements wer
            WHERE wer.step_definition_id=? AND wer.mandatory=1
              AND NOT EXISTS (
                SELECT 1 FROM workflow_step_evidence wse
                WHERE wse.case_workflow_step_id=?
                  AND (wse.evidence_requirement_id=wer.id OR wse.evidence_type=wer.evidence_type)
              ) ORDER BY wer.evidence_type""",
            (step["step_definition_id"], step["id"]),
        ).fetchall()
        return [row["evidence_type"] for row in rows]

    def start_step(self, case_id: str, step_id: str, actor_id: str) -> Dict[str, Any]:
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            if step["status"] not in {"READY", "BLOCKED"}:
                raise WorkflowError("INVALID_STEP_TRANSITION", f"Cannot start a step in {step['status']} status.", 409)
            now = utc_now()
            connection.execute(
                "UPDATE case_workflow_steps SET status='IN_PROGRESS',started_at=COALESCE(started_at,?),updated_at=? WHERE id=?",
                (now, now, step_id),
            )
            self._sync_task_status(connection, step_id, "IN_PROGRESS", actor_id)
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_STARTED", "case_workflow_step", step_id, case_id,
                                before={"status": step["status"]}, after={"status": "IN_PROGRESS"},
                                title=f"{step['step_code']} started")
        return self.get_step(case_id, step_id)

    def block_step(self, case_id: str, step_id: str, reason: str, actor_id: str) -> Dict[str, Any]:
        if not str(reason or "").strip():
            raise WorkflowError("INVALID_STEP_TRANSITION", "A blocking reason is required.")
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            if step["status"] in TERMINAL_STEP_STATUSES:
                raise WorkflowError("INVALID_STEP_TRANSITION", "A terminal workflow step cannot be blocked.", 409)
            connection.execute(
                "UPDATE case_workflow_steps SET status='BLOCKED',remarks=?,updated_at=? WHERE id=?",
                (reason.strip(), utc_now(), step_id),
            )
            self._sync_task_status(connection, step_id, "BLOCKED", actor_id)
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_BLOCKED", "case_workflow_step", step_id, case_id,
                                before={"status": step["status"]}, after={"status": "BLOCKED", "reason": reason.strip()},
                                title=f"{step['step_code']} blocked")
        return self.get_step(case_id, step_id)

    def waive_step(self, case_id: str, step_id: str, reason: str, actor_id: str) -> Dict[str, Any]:
        if not str(reason or "").strip():
            raise WorkflowError("INVALID_STEP_TRANSITION", "A waiver reason is required.")
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            if step["status"] in TERMINAL_STEP_STATUSES:
                raise WorkflowError("INVALID_STEP_TRANSITION", "A terminal workflow step cannot be waived.", 409)
            now = utc_now()
            connection.execute(
                "UPDATE case_workflow_steps SET status='WAIVED',remarks=?,completed_at=?,updated_at=? WHERE id=?",
                (reason.strip(), now, now, step_id),
            )
            self._sync_task_status(connection, step_id, "WAIVED", actor_id)
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_WAIVED", "case_workflow_step", step_id, case_id,
                                before={"status": step["status"]}, after={"status": "WAIVED", "reason": reason.strip()},
                                title=f"{step['step_code']} waived")
            self._release_children(connection, step["case_workflow_id"], actor_id)
        return self.get_step(case_id, step_id)

    def complete_step(self, case_id: str, step_id: str, actor_id: str,
                      remarks: str = "", evidence_override_reason: str = "") -> Dict[str, Any]:
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            if step["status"] not in {"READY", "IN_PROGRESS", "BLOCKED", "PENDING_APPROVAL"}:
                raise WorkflowError("INVALID_STEP_TRANSITION", f"Cannot complete a step in {step['status']} status.", 409)
            missing = self._missing_mandatory_evidence(connection, step)
            if missing and not str(evidence_override_reason or "").strip():
                raise WorkflowError("MISSING_REQUIRED_EVIDENCE", f"Missing mandatory evidence: {', '.join(missing)}.", 409)
            now = utc_now()
            requires_approval = bool(step["approval_role"]) and step["approval_status"] != "APPROVED"
            new_status = "PENDING_APPROVAL" if requires_approval else "COMPLETED"
            approval_status = "PENDING" if requires_approval else step["approval_status"]
            completed_at = None if requires_approval else now
            connection.execute(
                """UPDATE case_workflow_steps SET status=?,approval_status=?,completed_at=?,remarks=?,
                evidence_override_reason=?,evidence_override_by=?,updated_at=? WHERE id=?""",
                (new_status, approval_status, completed_at, remarks.strip(),
                 evidence_override_reason.strip(), actor_id if evidence_override_reason.strip() else None,
                 now, step_id),
            )
            self._sync_task_status(connection, step_id, new_status, actor_id, completed_at)
            action = "WORKFLOW_STEP_COMPLETED" if new_status == "COMPLETED" else "WORKFLOW_STEP_PENDING_APPROVAL"
            self.database.audit(connection, actor_id, action, "case_workflow_step", step_id, case_id,
                                before={"status": step["status"]},
                                after={"status": new_status, "evidence_override_reason": evidence_override_reason.strip()},
                                title=f"{step['step_code']} {new_status.lower().replace('_', ' ')}")
            if new_status == "COMPLETED":
                self._record_step_completed_event(connection, step, actor_id, now)
                self._release_children(connection, step["case_workflow_id"], actor_id)
        return self.get_step(case_id, step_id)

    def approve_step(self, case_id: str, step_id: str, actor_id: str) -> Dict[str, Any]:
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            if step["status"] != "PENDING_APPROVAL":
                raise WorkflowError("INVALID_STEP_TRANSITION", "Only a pending-approval step can be approved.", 409)
            now = utc_now()
            connection.execute(
                """UPDATE case_workflow_steps SET status='COMPLETED',approval_status='APPROVED',
                approved_by=?,approved_at=?,completed_at=?,updated_at=? WHERE id=?""",
                (actor_id, now, now, now, step_id),
            )
            self._sync_task_status(connection, step_id, "COMPLETED", actor_id, now)
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_APPROVED", "case_workflow_step", step_id, case_id,
                                before={"status": step["status"]}, after={"status": "COMPLETED", "approval_status": "APPROVED"},
                                title=f"{step['step_code']} approved and completed")
            self._record_step_completed_event(connection, step, actor_id, now)
            self._release_children(connection, step["case_workflow_id"], actor_id)
        return self.get_step(case_id, step_id)

    def _record_step_completed_event(self, connection: sqlite3.Connection, step: sqlite3.Row,
                                     actor_id: str, now: str) -> None:
        event_id = new_id()
        key = f"workflow-step-completed:{step['id']}"
        connection.execute(
            """INSERT OR IGNORE INTO case_events
            (id,case_id,event_type,event_date,source_type,source_id,status,metadata_json,idempotency_key,
             processed_at,created_by,confirmed_by,confirmed_at,created_at)
            VALUES (?,?, 'WORKFLOW_STEP_COMPLETED',?,'workflow_step',?,'CONFIRMED',?,?,?, ?,?,?,?)""",
            (event_id, step["case_id"], now[:10], step["id"], _json({"step_code": step["step_code"]}),
             key, now, actor_id, actor_id, now, now),
        )

    def _release_children(self, connection: sqlite3.Connection, workflow_id: str, actor_id: str) -> None:
        waiting = connection.execute(
            "SELECT id FROM case_workflow_steps WHERE case_workflow_id=? AND status='WAITING'", (workflow_id,)
        ).fetchall()
        for child in waiting:
            if self._unmet_required_dependencies(connection, child["id"]) == 0:
                connection.execute(
                    "UPDATE case_workflow_steps SET status='READY',updated_at=? WHERE id=?",
                    (utc_now(), child["id"]),
                )
                self._ensure_task(connection, child["id"], actor_id)

    def assign_step(self, case_id: str, step_id: str, actor_id: str, owner_user_id: Optional[str],
                    checker_user_id: Optional[str], internal_due_date: Optional[str], priority: Optional[str]) -> Dict[str, Any]:
        if internal_due_date:
            _parse_date(internal_due_date, "INVALID_INTERNAL_DUE_DATE")
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            for user_id in (owner_user_id, checker_user_id):
                if user_id and not connection.execute("SELECT 1 FROM users WHERE id=? AND active=1", (user_id,)).fetchone():
                    raise WorkflowError("UNAUTHORIZED", "Assigned user is unavailable.", 403)
            updates = {
                "owner_user_id": owner_user_id,
                "checker_user_id": checker_user_id,
                "internal_due_date": internal_due_date[:10] if internal_due_date else None,
                "priority": priority or step["priority"],
                "updated_at": utc_now(),
            }
            connection.execute(
                """UPDATE case_workflow_steps SET owner_user_id=?,checker_user_id=?,internal_due_date=?,
                priority=?,updated_at=? WHERE id=?""",
                (*updates.values(), step_id),
            )
            connection.execute(
                """UPDATE tasks SET assignee_id=COALESCE(?,assignee_id),reviewer_id=COALESCE(?,reviewer_id),
                due_date=COALESCE(?,due_date),priority=?,updated_by=?,updated_at=? WHERE workflow_step_id=?""",
                (owner_user_id, checker_user_id, internal_due_date, updates["priority"], actor_id, utc_now(), step_id),
            )
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_ASSIGNED", "case_workflow_step", step_id, case_id,
                                before={"owner_user_id": step["owner_user_id"], "checker_user_id": step["checker_user_id"]},
                                after=updates, title=f"{step['step_code']} assignment updated")
        return self.get_step(case_id, step_id)

    def attach_evidence(self, case_id: str, step_id: str, actor_id: str, evidence_type: str,
                        document_id: Optional[str] = None, event_id: Optional[str] = None,
                        source_type: str = "document", source_id: str = "") -> Dict[str, Any]:
        with self.database.transaction() as connection:
            step = self._step_row(connection, case_id, step_id)
            evidence_id = self._attach_evidence(
                connection, step, actor_id, evidence_type, document_id, event_id, source_type, source_id,
            )
            self.database.audit(connection, actor_id, "WORKFLOW_EVIDENCE_ATTACHED", "workflow_step_evidence",
                                evidence_id, case_id, after={"workflow_step_id": step_id, "evidence_type": evidence_type,
                                                            "document_id": document_id, "event_id": event_id},
                                title=f"Evidence attached to {step['step_code']}")
        return self.get_step(case_id, step_id)

    def _attach_evidence(self, connection: sqlite3.Connection, step: sqlite3.Row, actor_id: str,
                         evidence_type: str, document_id: Optional[str], event_id: Optional[str],
                         source_type: str, source_id: str) -> str:
        if document_id and not connection.execute(
            "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, step["case_id"])
        ).fetchone():
            raise WorkflowError("EVIDENCE_NOT_FOUND", "Evidence document was not found for this case.", 404)
        if event_id and not connection.execute(
            "SELECT 1 FROM case_events WHERE id=? AND case_id=?", (event_id, step["case_id"])
        ).fetchone():
            raise WorkflowError("EVIDENCE_NOT_FOUND", "Evidence event was not found for this case.", 404)
        requirement = connection.execute(
            """SELECT id FROM workflow_evidence_requirements
            WHERE step_definition_id=? AND evidence_type=?""",
            (step["step_definition_id"], evidence_type),
        ).fetchone()
        duplicate = connection.execute(
            """SELECT id FROM workflow_step_evidence WHERE case_workflow_step_id=? AND evidence_type=?
            AND COALESCE(document_id,'')=COALESCE(?,'') AND COALESCE(event_id,'')=COALESCE(?,'')
            AND source_type=? AND source_id=?""",
            (step["id"], evidence_type, document_id, event_id, source_type, source_id),
        ).fetchone()
        if duplicate:
            return duplicate["id"]
        evidence_id = new_id()
        connection.execute(
            """INSERT INTO workflow_step_evidence
            (id,case_id,case_workflow_step_id,evidence_requirement_id,evidence_type,document_id,event_id,
             source_type,source_id,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (evidence_id, step["case_id"], step["id"], requirement["id"] if requirement else None,
             evidence_type, document_id, event_id, source_type, source_id, actor_id, utc_now()),
        )
        if document_id:
            connection.execute(
                """UPDATE documents SET workflow_step_id=COALESCE(workflow_step_id,?),
                event_id=COALESCE(event_id,?),updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (step["id"], event_id, actor_id, utc_now(), document_id, step["case_id"]),
            )
        return evidence_id

    def get_step(self, case_id: str, step_id: str) -> Dict[str, Any]:
        workflow = self.get_case_workflow(case_id)
        for step in workflow["steps"]:
            if step["id"] == step_id:
                return step
        raise WorkflowError("WORKFLOW_STEP_NOT_FOUND", "Workflow step was not found for this case.", 404)

    def get_case_workflow(self, case_id: str) -> Dict[str, Any]:
        with self.database.connect() as connection:
            self.database.ensure_case(connection, case_id)
            workflow = self._workflow_row(connection, case_id)
            rows = connection.execute(
                """SELECT cws.*,wsd.step_code,wsd.sequence,wsd.day_trigger_text,wsd.legal_reference,
                wsd.area,wsd.activity,wsd.standard_output,wsd.automation_type,wsd.approval_role,
                wsd.evidence_requirement_text,wp.code AS phase_code,wp.name AS phase_name,
                cd.id AS deadline_id,cd.anchor_date,cd.calculated_due_date,cd.override_due_date,
                cd.override_reason,cd.status AS deadline_status,t.id AS task_id,t.status AS task_status,
                t.title AS task_title,
                (SELECT COUNT(*) FROM workflow_evidence_requirements wer
                 WHERE wer.step_definition_id=cws.step_definition_id AND wer.mandatory=1) AS mandatory_evidence_count,
                (SELECT COUNT(*) FROM workflow_step_evidence wse
                 WHERE wse.case_workflow_step_id=cws.id) AS evidence_count
                FROM case_workflow_steps cws
                JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
                JOIN workflow_phases wp ON wp.id=wsd.phase_id
                LEFT JOIN case_deadlines cd ON cd.case_workflow_step_id=cws.id
                LEFT JOIN tasks t ON t.workflow_step_id=cws.id AND t.archived_at IS NULL
                WHERE cws.case_workflow_id=? ORDER BY wsd.sequence""",
                (workflow["id"],),
            ).fetchall()
            steps = [dict(row) for row in rows]
            for step in steps:
                step["effective_due_date"] = step["override_due_date"] or step["calculated_due_date"] or step["statutory_due_date"]
                step["evidence_status"] = (
                    "SATISFIED" if step["evidence_count"] >= step["mandatory_evidence_count"]
                    else "OVERRIDDEN" if step["evidence_override_reason"]
                    else "MISSING" if step["mandatory_evidence_count"] else "NOT_REQUIRED"
                )
            return {"workflow": dict(workflow), "steps": steps}

    def summary(self, case_id: str) -> Dict[str, Any]:
        payload = self.get_case_workflow(case_id)
        workflow, steps = payload["workflow"], payload["steps"]
        counts = {status.lower(): 0 for status in STEP_STATUSES}
        today = date.today()
        overdue = due_today = due_next_7 = 0
        next_deadline: Optional[Dict[str, Any]] = None
        pending_approvals = 0
        for step in steps:
            counts[step["status"].lower()] += 1
            if step["status"] == "PENDING_APPROVAL":
                pending_approvals += 1
            due_text = step.get("effective_due_date")
            if due_text and step["status"] not in TERMINAL_STEP_STATUSES | {"NOT_TRIGGERED"}:
                due = _parse_date(due_text)
                if due < today:
                    overdue += 1
                if due == today:
                    due_today += 1
                if today < due <= today + timedelta(days=7):
                    due_next_7 += 1
                candidate = {"step": step["step_code"], "title": step["standard_output"], "date": due.isoformat()}
                if next_deadline is None or candidate["date"] < next_deadline["date"]:
                    next_deadline = candidate
        active_steps = [step for step in steps if step["status"] not in {"NOT_TRIGGERED", *TERMINAL_STEP_STATUSES}]
        current_phase = active_steps[0]["phase_name"] if active_steps else (steps[0]["phase_name"] if steps else "")
        result = {
            "case_id": case_id,
            "workflow": workflow["workflow_version"],
            "current_phase": current_phase,
            "total_steps": len(steps),
            **counts,
            "active": sum(counts[key] for key in ("ready", "in_progress", "waiting", "blocked", "pending_approval")),
            "overdue_count": overdue,
            "due_today_count": due_today,
            "due_next_7_days_count": due_next_7,
            "next_statutory_deadline": next_deadline,
            "pending_rp_approvals": pending_approvals,
        }
        # Import lazily to avoid coupling the generic engine to the Claims/CoC
        # domain during module initialization.
        from claims_coc_core import ClaimsCocCore
        result.update(ClaimsCocCore(self.database).workflow_summary(case_id))
        from coc_meeting_core import CocMeetingCore
        result.update(CocMeetingCore(self.database).workflow_summary(case_id))
        from phase4_core import Phase4Core
        result.update(Phase4Core(self.database).workflow_summary(case_id))
        from transaction_audit_core import TransactionAuditCore
        result.update(TransactionAuditCore(self.database).workflow_summary(case_id))
        return result


class EventEngine:
    """Record confirmed case events and deterministically advance workflow state."""

    def __init__(self, database: CasefileDatabase, workflow_version: str = DEFAULT_WORKFLOW_VERSION):
        self.database = database
        self.workflow = WorkflowService(database, workflow_version)

    @staticmethod
    def _event_public(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["metadata"] = _from_json(result.pop("metadata_json"), {})
        return result

    @staticmethod
    def _key(case_id: str, event_type: str, event_date: str, source_type: str,
             source_id: str, metadata: Dict[str, Any], explicit: Optional[str]) -> str:
        if explicit:
            return explicit
        canonical = json.dumps(
            {"case_id": case_id, "event_type": event_type, "event_date": event_date[:10],
             "source_type": source_type, "source_id": source_id, "metadata": metadata},
            sort_keys=True, separators=(",", ":"), ensure_ascii=False,
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def record_event(self, case_id: str, event_type: str, event_date: str, actor_id: str,
                     source_type: str = "manual", source_id: str = "",
                     source_document_id: Optional[str] = None, status: str = "CONFIRMED",
                     metadata: Optional[Dict[str, Any]] = None,
                     idempotency_key: Optional[str] = None) -> Dict[str, Any]:
        event_type = str(event_type or "").upper()
        status = str(status or "").upper()
        if event_type not in EVENT_TYPES:
            raise WorkflowError("UNSUPPORTED_EVENT_TYPE", f"Unsupported event type: {event_type}.")
        if status not in EVENT_STATUSES:
            raise WorkflowError("INVALID_EVENT_STATUS", f"Unsupported event status: {status}.")
        event_day = _parse_date(event_date, "MISSING_ANCHOR_DATE").isoformat()
        metadata = dict(metadata or {})
        key = self._key(case_id, event_type, event_day, source_type, source_id, metadata, idempotency_key)
        actions: List[Dict[str, Any]] = []
        with self.database.transaction() as connection:
            self.database.ensure_case(connection, case_id)
            existing = connection.execute(
                "SELECT * FROM case_events WHERE case_id=? AND idempotency_key=?", (case_id, key)
            ).fetchone()
            if existing:
                result = self._event_public(existing)
                result.update({"idempotent_replay": True, "actions": []})
                return result
            if source_document_id and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL",
                (source_document_id, case_id),
            ).fetchone():
                raise WorkflowError("SOURCE_DOCUMENT_NOT_FOUND", "Source document was not found for this case.", 404)
            event_id, now = new_id(), utc_now()
            confirmed_by = actor_id if status == "CONFIRMED" else None
            confirmed_at = now if status == "CONFIRMED" else None
            connection.execute(
                """INSERT INTO case_events
                (id,case_id,event_type,event_date,source_type,source_id,source_document_id,status,metadata_json,
                 idempotency_key,created_by,confirmed_by,confirmed_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (event_id, case_id, event_type, event_day, source_type, source_id, source_document_id,
                 status, _json(metadata), key, actor_id, confirmed_by, confirmed_at, now),
            )
            self.database.audit(
                connection, actor_id, "CASE_EVENT_RECORDED", "case_event", event_id, case_id,
                after={"event_type": event_type, "event_date": event_day, "status": status,
                       "source_type": source_type, "source_id": source_id},
                title=f"Case event recorded: {event_type}",
            )
            event = connection.execute("SELECT * FROM case_events WHERE id=?", (event_id,)).fetchone()
            if status == "CONFIRMED":
                self.database.audit(
                    connection, actor_id, "CASE_EVENT_CONFIRMED", "case_event", event_id, case_id,
                    after={"event_type": event_type, "event_date": event_day},
                    title=f"Case event confirmed: {event_type}",
                )
                actions = self._process(connection, event, actor_id)
                connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), event_id))
            stored = connection.execute("SELECT * FROM case_events WHERE id=?", (event_id,)).fetchone()
            result = self._event_public(stored)
            result.update({"idempotent_replay": False, "actions": actions})
            return result

    def confirm_event(self, case_id: str, event_id: str, actor_id: str) -> Dict[str, Any]:
        with self.database.transaction() as connection:
            event = connection.execute("SELECT * FROM case_events WHERE id=? AND case_id=?", (event_id, case_id)).fetchone()
            if not event:
                raise WorkflowError("EVENT_NOT_FOUND", "Case event was not found.", 404)
            if event["status"] == "CONFIRMED" and event["processed_at"]:
                result = self._event_public(event)
                result.update({"idempotent_replay": True, "actions": []})
                return result
            if event["status"] not in {"PENDING_REVIEW", "CONFIRMED"}:
                raise WorkflowError("INVALID_EVENT_STATUS", f"Cannot confirm an event in {event['status']} status.", 409)
            now = utc_now()
            connection.execute(
                "UPDATE case_events SET status='CONFIRMED',confirmed_by=?,confirmed_at=? WHERE id=?",
                (actor_id, now, event_id),
            )
            event = connection.execute("SELECT * FROM case_events WHERE id=?", (event_id,)).fetchone()
            actions = self._process(connection, event, actor_id)
            connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), event_id))
            self.database.audit(connection, actor_id, "CASE_EVENT_CONFIRMED", "case_event", event_id, case_id,
                                after={"event_type": event["event_type"], "event_date": event["event_date"]},
                                title=f"Case event confirmed: {event['event_type']}")
            stored = connection.execute("SELECT * FROM case_events WHERE id=?", (event_id,)).fetchone()
            result = self._event_public(stored)
            result.update({"idempotent_replay": False, "actions": actions})
            return result

    def _derived_event(self, connection: sqlite3.Connection, case_id: str, event_type: str,
                       event_date: str, source_event: sqlite3.Row, actor_id: str) -> sqlite3.Row:
        key = f"derived:{source_event['id']}:{event_type}"
        existing = connection.execute(
            "SELECT * FROM case_events WHERE case_id=? AND idempotency_key=?", (case_id, key)
        ).fetchone()
        if existing:
            return existing
        event_id, now = new_id(), utc_now()
        connection.execute(
            """INSERT INTO case_events
            (id,case_id,event_type,event_date,source_type,source_id,source_document_id,status,metadata_json,
             idempotency_key,created_by,confirmed_by,confirmed_at,created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (event_id, case_id, event_type, event_date[:10], source_event["source_type"],
             source_event["source_id"], source_event["source_document_id"], "CONFIRMED", "{}",
             key, actor_id, actor_id, now, now),
        )
        self.database.audit(connection, actor_id, "CASE_EVENT_CONFIRMED", "case_event", event_id, case_id,
                            after={"event_type": event_type, "event_date": event_date[:10], "derived_from": source_event["id"]},
                            title=f"Derived case event confirmed: {event_type}")
        return connection.execute("SELECT * FROM case_events WHERE id=?", (event_id,)).fetchone()

    def _process(self, connection: sqlite3.Connection, event: sqlite3.Row, actor_id: str) -> List[Dict[str, Any]]:
        case_id, event_type = event["case_id"], event["event_type"]
        actions: List[Dict[str, Any]] = []
        if event_type == "ADMISSION_ORDER_CONFIRMED":
            workflow_id, created = self.workflow._ensure_workflow(connection, case_id, actor_id)
            actions.append({"action": "CASE_WORKFLOW_INITIALIZED" if created else "CASE_WORKFLOW_REUSED",
                            "workflow_id": workflow_id})
        workflow = connection.execute(
            "SELECT * FROM case_workflows WHERE case_id=? AND workflow_type='CIRP'", (case_id,)
        ).fetchone()
        if workflow:
            activated = self.workflow._activate_for_event(connection, case_id, event, actor_id)
            actions.extend({"action": "WORKFLOW_STEP_ACTIVATED", "workflow_step_id": step_id} for step_id in activated)

        metadata = _from_json(event["metadata_json"], {})
        if event_type == "ADMISSION_ORDER_CONFIRMED":
            case_row = connection.execute("SELECT commencement_date,order_date FROM cases WHERE id=?", (case_id,)).fetchone()
            commencement = metadata.get("cirp_commencement_date") or case_row["commencement_date"]
            appointment = metadata.get("irp_appointment_date") or commencement
            if commencement:
                derived = self._derived_event(connection, case_id, "CIRP_COMMENCEMENT_CONFIRMED", commencement, event, actor_id)
                activated = self.workflow._activate_for_event(connection, case_id, derived, actor_id)
                connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), derived["id"]))
                actions.append({"action": "DERIVED_EVENT", "event_id": derived["id"], "event_type": derived["event_type"]})
                actions.extend({"action": "WORKFLOW_STEP_ACTIVATED", "workflow_step_id": step_id} for step_id in activated)
            if appointment:
                derived = self._derived_event(connection, case_id, "IRP_APPOINTMENT_CONFIRMED", appointment, event, actor_id)
                activated = self.workflow._activate_for_event(connection, case_id, derived, actor_id)
                connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), derived["id"]))
                actions.append({"action": "DERIVED_EVENT", "event_id": derived["id"], "event_type": derived["event_type"]})
                actions.extend({"action": "WORKFLOW_STEP_ACTIVATED", "workflow_step_id": step_id} for step_id in activated)
            if event["source_document_id"]:
                self._link_event_evidence(connection, case_id, "CIRP-001", "ADMISSION_ORDER", event, actor_id)

        if event_type == "PUBLIC_ANNOUNCEMENT_DRAFT_READY":
            self._set_step_states(connection, case_id, {"CIRP-021": "PENDING_APPROVAL"}, actor_id, event)
        elif event_type == "PUBLIC_ANNOUNCEMENT_SENT_FOR_PUBLICATION":
            self._set_step_states(connection, case_id, {"CIRP-021": "IN_PROGRESS", "CIRP-022": "IN_PROGRESS"}, actor_id, event)
        elif event_type == "PUBLIC_ANNOUNCEMENT_PUBLISHED":
            self._set_step_states(connection, case_id, {"CIRP-021": "IN_PROGRESS", "CIRP-022": "IN_PROGRESS", "CIRP-023": "IN_PROGRESS"}, actor_id, event)
        elif event_type == "PUBLIC_ANNOUNCEMENT_CONFIRMED":
            self._complete_public_announcement_steps(connection, case_id, event, actor_id)
            self._set_step_states(connection, case_id, {"CIRP-024": "COMPLETED"}, actor_id, event)
            actions.append({
                "action": "PUBLIC_ANNOUNCEMENT_WORKFLOW_UPDATED",
                "completed_steps": ["CIRP-021", "CIRP-022"],
                "evidence_pending_steps": ["CIRP-023"],
            })

        domain_states = {
            "CLAIM_ACKNOWLEDGED": {"CIRP-025": "COMPLETED"},
            "CLAIM_CLASSIFICATION_CONFIRMED": {"CIRP-026": "COMPLETED", "CIRP-027": "IN_PROGRESS"},
            "CLAIM_DEFICIENCY_IDENTIFIED": {"CIRP-027": "IN_PROGRESS", "CIRP-028": "READY"},
            "CLAIM_QUERY_CREATED": {"CIRP-028": "IN_PROGRESS"},
            "CLAIM_QUERY_CLOSED": {"CIRP-028": "COMPLETED"},
            "LATE_CLAIM_IDENTIFIED": {"CIRP-036": "READY"},
            "CLAIM_VERIFICATION_STARTED": {"CIRP-030": "IN_PROGRESS"},
            "CLAIM_VERIFICATION_COMPLETED": {"CIRP-027": "COMPLETED", "CIRP-030": "COMPLETED", "CIRP-031": "READY", "CIRP-032": "READY"},
            "RELATED_PARTY_STATUS_CONFIRMED": {"CIRP-031": "COMPLETED"},
            "CLAIM_ADMITTED": {"CIRP-032": "COMPLETED", "CIRP-033": "READY", "CIRP-034": "READY", "CIRP-039": "READY"},
            "CLAIM_PARTLY_ADMITTED": {"CIRP-032": "COMPLETED", "CIRP-033": "READY", "CIRP-034": "READY", "CIRP-039": "READY"},
            "CLAIM_NOT_ADMITTED": {"CIRP-032": "COMPLETED", "CIRP-033": "READY", "CIRP-034": "READY"},
            "CLAIM_REVISED": {"CIRP-037": "COMPLETED", "CIRP-030": "READY"},
            "SECURITY_REVIEW_REQUIRED": {"CIRP-038": "READY"},
            "LIST_OF_CREDITORS_SNAPSHOT_CREATED": {"CIRP-034": "COMPLETED", "CIRP-035": "READY"},
            "LIST_OF_CREDITORS_PUBLISHED": {"CIRP-035": "COMPLETED"},
            "COC_ELIGIBILITY_CONFIRMED": {"CIRP-039": "COMPLETED", "CIRP-040": "READY"},
            "COC_VOTING_SHARE_CALCULATED": {"CIRP-040": "COMPLETED", "CIRP-043": "READY"},
            "CREDITORS_IN_CLASS_CONFIRMED": {"CIRP-041": "COMPLETED"},
            "AR_REQUIRED": {"CIRP-042": "READY"},
            "COC_CONSTITUTED": {"CIRP-043": "COMPLETED", "CIRP-044": "READY"},
            "COC_CONSTITUTION_REPORT_GENERATED": {"CIRP-044": "IN_PROGRESS"},
            "COC_CONSTITUTION_REPORT_FINALIZED": {"CIRP-044": "COMPLETED"},
            "COC_RECONSTITUTED": {"CIRP-045": "COMPLETED"},
            "FIRST_COC_MEETING_REQUIRED": {"CIRP-046": "READY"},
            "COC_MEETING_CREATED": {"CIRP-046": "IN_PROGRESS", "CIRP-047": "READY"},
            "COC_MEETING_SCHEDULED": {"CIRP-046": "IN_PROGRESS"},
            "COC_NOTICE_DRAFTED": {"CIRP-046": "IN_PROGRESS", "CIRP-047": "IN_PROGRESS"},
            "COC_AGENDA_FINALIZED": {"CIRP-047": "COMPLETED"},
            "COC_NOTICE_ISSUED": {"CIRP-046": "COMPLETED", "CIRP-048": "READY", "CIRP-049": "READY"},
            "COC_ATTENDANCE_RECORDED": {"CIRP-049": "IN_PROGRESS"},
            "COC_QUORUM_CONFIRMED": {"CIRP-048": "COMPLETED", "CIRP-049": "IN_PROGRESS"},
            "COC_QUORUM_NOT_MET": {"CIRP-048": "IN_PROGRESS"},
            "COC_RESOLUTION_PLACED": {"CIRP-050": "IN_PROGRESS"},
            "COC_COST_STATEMENT_RECORDED": {"CIRP-051": "COMPLETED"},
            "COC_OPERATIONS_UPDATE_RECORDED": {"CIRP-052": "COMPLETED"},
            "COC_PROFESSIONAL_PROPOSAL_RECORDED": {"CIRP-053": "COMPLETED"},
            "COC_MEETING_HELD": {"CIRP-054": "READY"},
            "COC_MINUTES_CONTENT_READY": {"CIRP-054": "IN_PROGRESS"},
            "COC_MINUTES_DRAFTED": {"CIRP-054": "IN_PROGRESS"},
            "COC_MINUTES_FINALIZED": {"CIRP-054": "COMPLETED"},
            "COC_VOTING_OPENED": {"CIRP-055": "IN_PROGRESS"},
            "COC_VOTING_CLOSED": {"CIRP-055": "COMPLETED", "CIRP-056": "READY"},
            "COC_VOTING_RESULT_FINALIZED": {"CIRP-056": "COMPLETED"},
            "COC_ATR_CREATED": {"CIRP-056": "IN_PROGRESS"},
        }
        if event_type in domain_states:
            self._set_step_states(connection, case_id, domain_states[event_type], actor_id, event)
        if event_type == "COC_REVIEW_REQUIRED":
            connection.execute(
                """UPDATE coc_constitutions SET review_required=1 WHERE id=(
                SELECT id FROM coc_constitutions WHERE case_id=? ORDER BY constitution_version DESC LIMIT 1)""",
                (case_id,),
            )
        if event_type == "COC_CONSTITUTED":
            derived = self._derived_event(connection, case_id, "FIRST_COC_MEETING_REQUIRED", event["event_date"], event, actor_id)
            activated = self.workflow._activate_for_event(connection, case_id, derived, actor_id)
            connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), derived["id"]))
            actions.append({"action": "DERIVED_EVENT", "event_id": derived["id"], "event_type": derived["event_type"]})
            actions.extend({"action": "WORKFLOW_STEP_ACTIVATED", "workflow_step_id": step_id} for step_id in activated)
        if event_type == "COC_RECONSTITUTED":
            issued = connection.execute(
                """SELECT id FROM coc_meetings WHERE case_id=? AND archived_at IS NULL
                AND (status='NOTICE_ISSUED' OR notice_snapshot_json <> '{}')""", (case_id,)
            ).fetchall()
            if issued:
                connection.execute(
                    """UPDATE coc_meetings SET membership_review_required=1,updated_by=?,updated_at=?
                    WHERE case_id=? AND archived_at IS NULL AND (status='NOTICE_ISSUED' OR notice_snapshot_json <> '{}')""",
                    (actor_id, utc_now(), case_id),
                )
                derived = self._derived_event(connection, case_id, "MEETING_MEMBERSHIP_REVIEW_REQUIRED", event["event_date"], event, actor_id)
                connection.execute("UPDATE case_events SET processed_at=? WHERE id=?", (utc_now(), derived["id"]))
                actions.append({"action": "DERIVED_EVENT", "event_id": derived["id"], "event_type": derived["event_type"], "meeting_count": len(issued)})

        workflow = connection.execute(
            "SELECT id FROM case_workflows WHERE case_id=? AND workflow_type='CIRP'", (case_id,)
        ).fetchone()
        if workflow:
            deadline_ids = self.workflow.deadlines.ensure_for_workflow(connection, case_id, workflow["id"], actor_id)
            actions.append({"action": "DEADLINES_REFRESHED", "count": len(deadline_ids)})
        return actions

    def _step_by_code(self, connection: sqlite3.Connection, case_id: str, step_code: str) -> Optional[sqlite3.Row]:
        return connection.execute(
            """SELECT cws.*,wsd.step_code FROM case_workflow_steps cws
            JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cws.case_id=? AND wsd.step_code=?""",
            (case_id, step_code),
        ).fetchone()

    def _link_event_evidence(self, connection: sqlite3.Connection, case_id: str, step_code: str,
                             evidence_type: str, event: sqlite3.Row, actor_id: str) -> None:
        step = self._step_by_code(connection, case_id, step_code)
        if not step:
            return
        self.workflow._attach_evidence(
            connection, step, actor_id, evidence_type, event["source_document_id"], event["id"],
            event["source_type"], event["source_id"],
        )

    def _set_step_states(self, connection: sqlite3.Connection, case_id: str,
                         states: Dict[str, str], actor_id: str, event: sqlite3.Row) -> None:
        for code, status in states.items():
            step = self._step_by_code(connection, case_id, code)
            if not step or step["status"] in TERMINAL_STEP_STATUSES:
                continue
            approval = "PENDING" if status == "PENDING_APPROVAL" else ("APPROVED" if status == "COMPLETED" else step["approval_status"])
            completed_at = utc_now() if status == "COMPLETED" else None
            connection.execute(
                """UPDATE case_workflow_steps SET status=?,approval_status=?,trigger_event_id=COALESCE(trigger_event_id,?),
                trigger_date=COALESCE(trigger_date,?),started_at=CASE WHEN ?='IN_PROGRESS' THEN COALESCE(started_at,?) ELSE started_at END,
                completed_at=CASE WHEN ?='COMPLETED' THEN ? ELSE completed_at END,updated_at=? WHERE id=?""",
                (status, approval, event["id"], event["event_date"], status, utc_now(), status, completed_at, utc_now(), step["id"]),
            )
            self.workflow._ensure_task(connection, step["id"], actor_id)
            self.workflow._sync_task_status(connection, step["id"], status, actor_id, completed_at)

    def _complete_public_announcement_steps(self, connection: sqlite3.Connection, case_id: str,
                                            event: sqlite3.Row, actor_id: str) -> None:
        evidence_by_step = {
            "CIRP-021": "PUBLICATION_PROOF",
            "CIRP-022": "NEWSPAPER_COPIES_INVOICES",
        }
        now = utc_now()
        for code, evidence_type in evidence_by_step.items():
            step = self._step_by_code(connection, case_id, code)
            if not step:
                continue
            self.workflow._attach_evidence(
                connection, step, actor_id, evidence_type, event["source_document_id"], event["id"],
                event["source_type"], event["source_id"],
            )
            connection.execute(
                """UPDATE case_workflow_steps SET status='COMPLETED',approval_status='APPROVED',
                approved_by=?,approved_at=?,completed_at=?,updated_at=? WHERE id=?""",
                (actor_id, now, now, now, step["id"]),
            )
            self.workflow._ensure_task(connection, step["id"], actor_id)
            self.workflow._sync_task_status(connection, step["id"], "COMPLETED", actor_id, now)
            self.database.audit(connection, actor_id, "WORKFLOW_STEP_COMPLETED", "case_workflow_step", step["id"], case_id,
                                after={"step_code": code, "status": "COMPLETED", "event_id": event["id"]},
                                title=f"{code} completed from confirmed Public Announcement")

        website_step = self._step_by_code(connection, case_id, "CIRP-023")
        if website_step and website_step["status"] not in TERMINAL_STEP_STATUSES:
            connection.execute(
                """UPDATE case_workflow_steps SET status='IN_PROGRESS',remarks=?,updated_at=? WHERE id=?""",
                ("Published announcement confirmed; website upload evidence remains required.", now, website_step["id"]),
            )
            self.workflow._ensure_task(connection, website_step["id"], actor_id)
            self.workflow._sync_task_status(connection, website_step["id"], "IN_PROGRESS", actor_id)

    def list_events(self, case_id: str) -> List[Dict[str, Any]]:
        with self.database.connect() as connection:
            self.database.ensure_case(connection, case_id)
            rows = connection.execute(
                """SELECT ce.*,u.name AS actor_name FROM case_events ce
                LEFT JOIN users u ON u.id=ce.created_by
                WHERE ce.case_id=? ORDER BY ce.event_date,ce.created_at""",
                (case_id,),
            ).fetchall()
            return [self._event_public(row) for row in rows]
