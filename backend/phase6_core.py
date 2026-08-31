"""CIRP-085--105 EOI, PRA and resolution-plan control service.

The service is deliberately a professional workflow controller, not a deal
platform.  It keeps eligibility, compliance, evaluation, CoC selection and
NCLT approval as separate, versioned stages and never makes a legal decision.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional
import sqlite3

from coc_meeting_core import CocMeetingCore
from database import CasefileDatabase, _from_json, _json, new_id, utc_now
from phase4_core import Phase4Core
from workflow import EventEngine


RECORD_TYPES = {
    "EOI_PROCESS", "ELIGIBILITY_CRITERION", "PRA", "CONSORTIUM_MEMBER",
    "EOI_SUBMISSION", "EOI_CHECKLIST", "PROCESS_DEPOSIT",
    "ELIGIBILITY_REVIEW", "CONNECTED_PERSON", "PROVISIONAL_LIST",
    "ELIGIBILITY_OBJECTION", "FINAL_LIST", "RFRP", "EVALUATION_MATRIX",
    "ISSUE_PACKAGE", "PRA_QUERY", "SITE_VISIT", "PROCESS_ADDENDUM",
    "RESOLUTION_PLAN", "SECTION30_REVIEW", "PLAN_REVIEW_QUERY",
    "PLAN_EVALUATION", "NEGOTIATION_PROCESS", "NEGOTIATION_ROUND",
    "NEGOTIATION_SUBMISSION", "COC_PLAN_PLACEMENT", "PLAN_VOTE_LINK",
    "SUCCESSFUL_RA", "PLAN_APPROVAL_WORKSPACE", "HEARING_LINK",
}
CONFIDENTIALITY_LEVELS = {
    "NORMAL", "CONFIDENTIAL_CIRP", "RESTRICTED_PRA",
    "RESTRICTED_RESOLUTION_PLAN", "RESTRICTED_EVALUATION", "RESTRICTED_LEGAL",
}
PRA_TYPES = {"COMPANY", "LLP", "PARTNERSHIP", "TRUST", "FUND", "INDIVIDUAL", "CONSORTIUM", "OTHER"}
EOI_STATUSES = {"DRAFT", "COC_REVIEW", "APPROVED", "PUBLISHED", "REVISED", "CLOSED"}
SUBMISSION_STATUSES = {
    "RECEIVED", "LATE", "INCOMPLETE", "UNDER_SCRUTINY", "ELIGIBLE_PROVISIONALLY",
    "INELIGIBLE_PROVISIONALLY", "OBJECTION_PENDING", "ELIGIBLE_FINAL",
    "INELIGIBLE_FINAL", "WITHDRAWN",
}
ELIGIBILITY_STAGES = {"EOI_INITIAL", "PROVISIONAL_LIST", "FINAL_LIST", "PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"}
ELIGIBILITY_STATUSES = {
    "NOT_STARTED", "IN_PROGRESS", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED",
    "ELIGIBLE", "INELIGIBLE", "CONDITIONALLY_ELIGIBLE", "REVIEW_REQUIRED",
}
CHECKLIST_RESPONSES = {"YES", "NO", "UNKNOWN", "NOT_APPLICABLE"}
EOI_CHECKLIST_STATUSES = {"REQUIRED", "RECEIVED", "DEFICIENT", "NOT_APPLICABLE", "REVIEW_REQUIRED", "ACCEPTED"}
SECTION30_RESPONSES = {"COMPLIANT", "NON_COMPLIANT", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED", "NOT_APPLICABLE"}
DEPOSIT_TYPES = {"EOI_DEPOSIT", "PLAN_DEPOSIT", "PERFORMANCE_SECURITY", "OTHER"}
DEPOSIT_STATUSES = {"NOT_REQUIRED", "REQUIRED", "RECEIVED", "VERIFIED", "DEFICIENT", "RETURN_DUE", "RETURNED", "FORFEITURE_REVIEW", "FORFEITED"}
INSTRUMENT_TYPES = {"DD", "BANK_GUARANTEE", "BANK_TRANSFER", "OTHER"}
PLAN_STATUSES = {
    "RECEIVED", "LATE", "INCOMPLETE", "UNDER_COMPLIANCE_REVIEW", "NON_COMPLIANT",
    "COMPLIANT", "CLARIFICATION_REQUIRED", "UNDER_EVALUATION", "COC_CONSIDERATION",
    "VOTING", "SELECTED", "NOT_SELECTED", "WITHDRAWN", "SUPERSEDED",
}
SRA_STATUSES = {"PROVISIONAL_SELECTION", "SELECTED_BY_COC", "PERFORMANCE_SECURITY_PENDING", "READY_FOR_NCLT", "NCLT_PENDING", "NCLT_APPROVED", "NCLT_REJECTED"}
IMMUTABLE_STATUSES = {"PUBLISHED", "ISSUED", "APPROVED", "LOCKED", "FILED", "FINAL"}


def _decimal(value: Any, label: str = "Value") -> Decimal:
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc


def _money(value: Any) -> str:
    return format(_decimal(value or 0, "Amount").quantize(Decimal("0.01"), rounding=ROUND_HALF_UP), ".2f")


class Phase6Core:
    """Case-isolated aggregate service for the complete resolution process."""

    def __init__(self, store: CasefileDatabase):
        self.store = store

    def _emit(self, case_id: str, event_type: str, actor: str, source_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        EventEngine(self.store).record_event(
            case_id, event_type, date.today().isoformat(), actor,
            source_type="resolution_process", source_id=source_id,
            metadata=metadata or {}, idempotency_key=f"phase6:{event_type}:{source_id}",
        )

    @staticmethod
    def _public(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["data"] = _from_json(result.pop("data_json"), {})
        result["snapshot"] = _from_json(result.pop("snapshot_json"), {})
        return result

    @staticmethod
    def _item_public(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        result["data"] = _from_json(result.pop("data_json"), {})
        return result

    def _row(self, connection: sqlite3.Connection, case_id: str, record_id: str, expected: Optional[Iterable[str]] = None) -> sqlite3.Row:
        row = connection.execute(
            "SELECT * FROM resolution_process_records WHERE id=? AND case_id=?",
            (record_id, case_id),
        ).fetchone()
        if not row:
            raise KeyError("Resolution-process record not found in this case")
        if expected and row["record_type"] not in set(expected):
            raise ValueError(f"Expected {', '.join(expected)} record")
        return row

    def _document(self, connection: sqlite3.Connection, case_id: str, document_id: Optional[str]) -> None:
        if document_id and not connection.execute(
            "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, case_id)
        ).fetchone():
            raise ValueError("Linked document does not belong to this case")

    def _external(self, connection: sqlite3.Connection, table: str, case_id: str, record_id: Optional[str], label: str) -> None:
        allowed = {"coc_meetings", "coc_resolutions", "coc_voting_results", "applications", "hearings", "phase4_records"}
        if record_id and table not in allowed:
            raise ValueError("Unsupported external link")
        if record_id and not connection.execute(f"SELECT 1 FROM {table} WHERE id=? AND case_id=?", (record_id, case_id)).fetchone():
            raise ValueError(f"{label} does not belong to this case")

    def _create(self, case_id: str, record_type: str, payload: Dict[str, Any], actor: str,
                *, process_id: Optional[str] = None, pra_id: Optional[str] = None,
                plan_id: Optional[str] = None, parent_id: Optional[str] = None,
                event_type: Optional[str] = None) -> Dict[str, Any]:
        kind = record_type.upper()
        if kind not in RECORD_TYPES:
            raise ValueError("Unsupported Phase 6 record type")
        key = str(payload.get("idempotency_key") or "").strip()
        level = str(payload.get("confidentiality_level") or "RESTRICTED_PRA").upper()
        if level not in CONFIDENTIALITY_LEVELS:
            raise ValueError("Unsupported Phase 6 confidentiality classification")
        with self.store.transaction() as connection:
            self.store.ensure_case(connection, case_id)
            if key:
                replay = connection.execute(
                    "SELECT * FROM resolution_process_records WHERE case_id=? AND idempotency_key=?", (case_id, key)
                ).fetchone()
                if replay:
                    return self._public(replay) | {"idempotent_replay": True}
            for linked in (process_id, pra_id, plan_id, parent_id):
                if linked:
                    self._row(connection, case_id, linked)
            self._document(connection, case_id, payload.get("document_id"))
            now, record_id = utc_now(), new_id()
            reserved = {
                "idempotency_key", "status", "version_number", "effective_date", "document_id",
                "confidentiality_level", "snapshot", "data",
            }
            data = dict(payload.get("data") or {})
            data.update({key: value for key, value in payload.items() if key not in reserved})
            status = str(payload.get("status") or "DRAFT").upper()
            immutable_at = now if status in {"PUBLISHED", "ISSUED", "LOCKED", "FILED", "FINAL"} else None
            immutable_by = actor if immutable_at else None
            connection.execute(
                """INSERT INTO resolution_process_records
                (id,case_id,record_type,process_id,pra_id,plan_id,parent_record_id,version_number,status,
                 effective_date,document_id,confidentiality_level,data_json,snapshot_json,idempotency_key,
                 immutable_at,immutable_by,created_by,updated_by,created_at,updated_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, case_id, kind, process_id, pra_id, plan_id, parent_id,
                 int(payload.get("version_number") or 1), status,
                 payload.get("effective_date"), payload.get("document_id"), level, _json(data),
                 _json(payload.get("snapshot") or {}), key, immutable_at, immutable_by, actor, actor, now, now),
            )
            self.store.audit(connection, actor, f"{kind}_CREATED", "resolution_process_record", record_id, case_id,
                             after={"record_type": kind, "status": payload.get("status", "DRAFT")}, title=f"{kind} created")
        result = self.get(case_id, record_id)
        if event_type:
            self._emit(case_id, event_type, actor, record_id)
        return result

    def get(self, case_id: str, record_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            result = self._public(self._row(connection, case_id, record_id))
            result["items"] = [self._item_public(row) for row in connection.execute(
                "SELECT * FROM resolution_process_items WHERE case_id=? AND record_id=? ORDER BY sequence,created_at",
                (case_id, record_id),
            ).fetchall()]
            result["links"] = [dict(row) | {"data": _from_json(row["data_json"], {})} for row in connection.execute(
                "SELECT * FROM resolution_process_links WHERE case_id=? AND record_id=? ORDER BY created_at", (case_id, record_id)
            ).fetchall()]
            return result

    def list(self, case_id: str, record_type: Optional[str] = None, pra_id: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            sql, args = "SELECT * FROM resolution_process_records WHERE case_id=?", [case_id]
            if record_type:
                sql += " AND record_type=?"; args.append(record_type.upper())
            if pra_id:
                self._row(connection, case_id, pra_id, {"PRA"})
                sql += " AND pra_id=?"; args.append(pra_id)
            return [self._public(row) for row in connection.execute(sql + " ORDER BY created_at,version_number", tuple(args)).fetchall()]

    def _update(self, case_id: str, record_id: str, payload: Dict[str, Any], actor: str,
                *, event_type: Optional[str] = None, freeze: bool = False) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            before = self._row(connection, case_id, record_id)
            if before["immutable_at"]:
                raise ValueError("Issued/approved Phase 6 record is immutable; create a new version or addendum")
            self._document(connection, case_id, payload.get("document_id"))
            data = _from_json(before["data_json"], {})
            data.update(payload.get("data") or {})
            reserved = {"status", "effective_date", "document_id", "confidentiality_level", "data", "snapshot"}
            data.update({key: value for key, value in payload.items() if key not in reserved})
            status = str(payload.get("status") or before["status"]).upper()
            level = str(payload.get("confidentiality_level") or before["confidentiality_level"]).upper()
            if level not in CONFIDENTIALITY_LEVELS:
                raise ValueError("Unsupported Phase 6 confidentiality classification")
            now = utc_now()
            immutable_at = now if freeze else None
            immutable_by = actor if immutable_at else None
            connection.execute(
                """UPDATE resolution_process_records SET status=?,effective_date=?,document_id=?,confidentiality_level=?,
                data_json=?,snapshot_json=?,immutable_at=?,immutable_by=?,updated_by=?,updated_at=? WHERE id=?""",
                (status, payload.get("effective_date", before["effective_date"]), payload.get("document_id", before["document_id"]),
                 level, _json(data), _json(payload.get("snapshot", _from_json(before["snapshot_json"], {}))),
                 immutable_at, immutable_by, actor, now, record_id),
            )
            self.store.audit(connection, actor, f"{before['record_type']}_UPDATED", "resolution_process_record", record_id, case_id,
                             before={"status": before["status"]}, after={"status": status}, title=f"{before['record_type']} updated")
        result = self.get(case_id, record_id)
        if event_type:
            self._emit(case_id, event_type, actor, record_id)
        return result

    def add_item(self, case_id: str, record_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            record = self._row(connection, case_id, record_id)
            if record["immutable_at"]:
                raise ValueError("Issued/approved Phase 6 record items are immutable")
            self._document(connection, case_id, payload.get("document_id"))
            item_type = str(payload.get("item_type") or "CHECKLIST").upper()
            item_key = str(payload.get("item_key") or "").strip()
            if not item_key:
                raise ValueError("item_key is required")
            now = utc_now()
            existing = connection.execute(
                "SELECT * FROM resolution_process_items WHERE record_id=? AND item_type=? AND item_key=?",
                (record_id, item_type, item_key),
            ).fetchone()
            data = dict(payload.get("data") or {})
            reserved = {"item_type", "item_key", "sequence", "status", "response", "document_id", "data"}
            data.update({key: value for key, value in payload.items() if key not in reserved})
            if existing:
                connection.execute(
                    """UPDATE resolution_process_items SET sequence=?,status=?,response=?,document_id=?,data_json=?,
                    updated_by=?,updated_at=? WHERE id=?""",
                    (int(payload.get("sequence", existing["sequence"])), str(payload.get("status") or existing["status"]).upper(),
                     str(payload.get("response", existing["response"])).upper(), payload.get("document_id", existing["document_id"]),
                     _json(data or _from_json(existing["data_json"], {})), actor, now, existing["id"]),
                )
                item_id = existing["id"]
            else:
                item_id = new_id()
                connection.execute(
                    """INSERT INTO resolution_process_items
                    (id,case_id,record_id,item_type,item_key,sequence,status,response,document_id,data_json,
                     created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (item_id, case_id, record_id, item_type, item_key, int(payload.get("sequence") or 0),
                     str(payload.get("status") or "REQUIRED").upper(), str(payload.get("response") or "").upper(),
                     payload.get("document_id"), _json(data), actor, actor, now, now),
                )
        return next(item for item in self.get(case_id, record_id)["items"] if item["id"] == item_id)

    def _link(self, case_id: str, record_id: str, actor: str, link_type: str,
              *, linked_record_id: Optional[str] = None, document_id: Optional[str] = None,
              external_entity_type: str = "", external_entity_id: str = "", data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            self._row(connection, case_id, record_id)
            if linked_record_id:
                self._row(connection, case_id, linked_record_id)
            self._document(connection, case_id, document_id)
            link_id = new_id()
            connection.execute(
                """INSERT OR IGNORE INTO resolution_process_links
                (id,case_id,record_id,linked_record_id,document_id,external_entity_type,external_entity_id,link_type,data_json,created_by,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (link_id, case_id, record_id, linked_record_id, document_id, external_entity_type,
                 external_entity_id, link_type, _json(data or {}), actor, utc_now()),
            )
            row = connection.execute(
                """SELECT * FROM resolution_process_links WHERE case_id=? AND record_id=? AND
                linked_record_id IS ? AND document_id IS ? AND external_entity_type=? AND external_entity_id=? AND link_type=?""",
                (case_id, record_id, linked_record_id, document_id, external_entity_type, external_entity_id, link_type),
            ).fetchone()
            return dict(row) | {"data": _from_json(row["data_json"], {})}

    # EOI process, PRA and submissions -----------------------------------
    def eoi_process(self, case_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "DRAFT").upper()
        if status not in EOI_STATUSES - {"PUBLISHED", "REVISED", "CLOSED"}:
            raise ValueError("A new EOI process must start as DRAFT, COC_REVIEW or APPROVED")
        with self.store.connect() as connection:
            version = connection.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='EOI_PROCESS'",
                (case_id,),
            ).fetchone()[0]
        data = dict(payload) | {
            "process_version": int(payload.get("process_version") or version),
            "deposit_amount": _money(payload.get("deposit_amount")) if payload.get("deposit_required") else "0.00",
            "deposit_currency": str(payload.get("deposit_currency") or "INR"),
            "template_status": "EOI_EDITABLE_TEMPLATE_REQUIRED",
        }
        return self._create(case_id, "EOI_PROCESS", data | {"version_number": version, "status": status}, actor,
                            event_type="EOI_PROCESS_DRAFTED")

    def eligibility_criterion(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            process = self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            if process["immutable_at"]:
                raise ValueError("Published EOI criteria are immutable; revise the EOI process")
        if not str(payload.get("title") or "").strip() or not str(payload.get("criterion_type") or "").strip():
            raise ValueError("Criterion title and type are required; the system will not invent eligibility criteria")
        data = dict(payload) | {"approved_version": int(process["version_number"])}
        return self._create(case_id, "ELIGIBILITY_CRITERION", data, actor, process_id=process_id, parent_id=process_id)

    def approve_eoi_process(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        if payload.get("professional_confirmed") is not True:
            raise ValueError("RP/CoC professional confirmation is required")
        with self.store.connect() as connection:
            self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            self._external(connection, "coc_meetings", case_id, payload.get("coc_meeting_id"), "CoC meeting")
            self._external(connection, "coc_resolutions", case_id, payload.get("coc_resolution_id"), "CoC resolution")
            criteria = connection.execute(
                "SELECT COUNT(*) FROM resolution_process_records WHERE case_id=? AND process_id=? AND record_type='ELIGIBILITY_CRITERION'",
                (case_id, process_id),
            ).fetchone()[0]
        if not criteria:
            raise ValueError("At least one professionally entered eligibility criterion is required")
        return self._update(case_id, process_id, payload | {"status": "APPROVED", "approved_by": actor, "approved_at": utc_now()}, actor,
                            event_type="EOI_PROCESS_APPROVED")

    def publish_eoi_process(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            process = self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            if process["status"] == "PUBLISHED":
                return self.get(case_id, process_id) | {"idempotent_replay": True}
            if process["status"] != "APPROVED":
                raise ValueError("Only an approved EOI process may be published")
            self._document(connection, case_id, payload.get("document_id"))
            self._document(connection, case_id, payload.get("publication_proof_document_id"))
        if not payload.get("document_id") or not payload.get("publication_proof_document_id"):
            raise ValueError("Uploaded final EOI and publication proof are required")
        return self._update(case_id, process_id, payload | {"status": "PUBLISHED", "publication_date": payload.get("publication_date") or date.today().isoformat()}, actor,
                            event_type="EOI_PUBLISHED", freeze=True)

    def revise_eoi_process(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        original = self.get(case_id, process_id)
        if original["record_type"] != "EOI_PROCESS" or original["status"] not in {"PUBLISHED", "APPROVED"}:
            raise ValueError("Only an approved/published EOI process may be revised")
        merged = dict(original["data"])
        merged.update(payload)
        merged.update({"status": "DRAFT", "supersedes_process_id": process_id, "idempotency_key": payload.get("idempotency_key")})
        return self.eoi_process(case_id, merged, actor)

    def pra(self, case_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        pra_type = str(payload.get("pra_type") or "OTHER").upper()
        if pra_type not in PRA_TYPES:
            raise ValueError("Unsupported PRA type")
        if not str(payload.get("legal_name") or "").strip():
            raise ValueError("PRA legal name is required")
        return self._create(case_id, "PRA", dict(payload) | {"pra_type": pra_type, "status": payload.get("status", "ACTIVE")}, actor,
                            pra_id=None)

    def consortium_member(self, case_id: str, consortium_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        consortium = self.get(case_id, consortium_id)
        if consortium["record_type"] != "PRA" or consortium["data"].get("pra_type") != "CONSORTIUM":
            raise ValueError("Consortium member must link to a consortium PRA")
        percentage = _decimal(payload.get("percentage_holding") or 0, "Participation percentage")
        if percentage < 0 or percentage > 100:
            raise ValueError("Participation percentage must be between 0 and 100")
        if not str(payload.get("member_legal_name") or "").strip():
            raise ValueError("Consortium member legal name is required")
        if payload.get("lead_member"):
            existing_leads = [row for row in self.list(case_id, "CONSORTIUM_MEMBER", consortium_id) if row["data"].get("lead_member")]
            if existing_leads:
                raise ValueError("A consortium may have only one lead member")
        data = dict(payload) | {"percentage_holding": format(percentage, "f"), "eligibility_status": payload.get("eligibility_status", "NOT_STARTED")}
        return self._create(case_id, "CONSORTIUM_MEMBER", data, actor, pra_id=consortium_id, parent_id=consortium_id)

    def eoi_submission(self, case_id: str, process_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            process = self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            self._row(connection, case_id, pra_id, {"PRA"})
            if process["status"] != "PUBLISHED":
                raise ValueError("EOI submissions require a published EOI process version")
        status = str(payload.get("submission_status") or payload.get("status") or "RECEIVED").upper()
        if status not in SUBMISSION_STATUSES:
            raise ValueError("Unsupported EOI submission status")
        channel = str(payload.get("submission_channel") or "OTHER").upper()
        if channel not in {"EMAIL", "PHYSICAL", "PORTAL", "OTHER"}:
            raise ValueError("Unsupported EOI submission channel")
        data = dict(payload) | {"submission_channel": channel, "received_at": payload.get("received_at") or utc_now()}
        return self._create(case_id, "EOI_SUBMISSION", data | {"status": status}, actor, process_id=process_id, pra_id=pra_id,
                            event_type="EOI_RECEIVED")

    def eoi_checklist(self, case_id: str, submission_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            submission = self._row(connection, case_id, submission_id, {"EOI_SUBMISSION"})
        checklist = self._create(case_id, "EOI_CHECKLIST", {"status": "IN_PROGRESS"}, actor,
                                 process_id=submission["process_id"], pra_id=submission["pra_id"], parent_id=submission_id)
        for sequence, item in enumerate(payload.get("items") or [], 1):
            status = str(item.get("status") or "REQUIRED").upper()
            if status not in EOI_CHECKLIST_STATUSES:
                raise ValueError("Unsupported EOI checklist status")
            self.add_item(case_id, checklist["id"], dict(item) | {"item_type": "EOI_DOCUMENT", "item_key": item.get("item_key") or f"ITEM-{sequence}", "sequence": sequence, "status": status}, actor)
        if any(str(item.get("status") or "").upper() == "DEFICIENT" for item in payload.get("items") or []):
            self._emit(case_id, "EOI_DOCUMENT_DEFICIENCY_IDENTIFIED", actor, checklist["id"])
        return self.get(case_id, checklist["id"])

    def checklist_summary(self, case_id: str, checklist_id: str) -> Dict[str, int]:
        checklist = self.get(case_id, checklist_id)
        if checklist["record_type"] != "EOI_CHECKLIST":
            raise ValueError("EOI checklist not found")
        counts = {status: 0 for status in EOI_CHECKLIST_STATUSES}
        for item in checklist["items"]:
            counts[item["status"]] = counts.get(item["status"], 0) + 1
        return {"total": len(checklist["items"]), **{key.lower(): value for key, value in counts.items()}}

    def process_deposit(self, case_id: str, pra_id: str, payload: Dict[str, Any], actor: str, process_id: Optional[str] = None) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, pra_id, {"PRA"})
            if process_id:
                self._row(connection, case_id, process_id, {"EOI_PROCESS", "RFRP"})
        deposit_type = str(payload.get("deposit_type") or "OTHER").upper()
        status = str(payload.get("status") or "REQUIRED").upper()
        instrument = str(payload.get("instrument_type") or "OTHER").upper()
        if deposit_type not in DEPOSIT_TYPES or status not in DEPOSIT_STATUSES or instrument not in INSTRUMENT_TYPES:
            raise ValueError("Unsupported deposit type, status or instrument")
        if status == "FORFEITED" and payload.get("professional_confirmed") is not True:
            raise ValueError("Deposit forfeiture requires an explicit professional/CoC decision")
        data = dict(payload) | {
            "deposit_type": deposit_type, "status": status, "instrument_type": instrument,
            "required_amount": _money(payload.get("required_amount")), "received_amount": _money(payload.get("received_amount")),
            "currency": payload.get("currency", "INR"),
        }
        event = "PERFORMANCE_SECURITY_RECEIVED" if deposit_type == "PERFORMANCE_SECURITY" and status in {"RECEIVED", "VERIFIED"} else None
        return self._create(case_id, "PROCESS_DEPOSIT", data | {"status": status, "confidentiality_level": "RESTRICTED_PRA"}, actor,
                            process_id=process_id, pra_id=pra_id, event_type=event)

    # Eligibility, lists and objections ---------------------------------
    def eligibility_review(self, case_id: str, pra_id: str, payload: Dict[str, Any], actor: str,
                           submission_id: Optional[str] = None, process_id: Optional[str] = None) -> Dict[str, Any]:
        stage = str(payload.get("review_stage") or "EOI_INITIAL").upper()
        status = str(payload.get("status") or "IN_PROGRESS").upper()
        if stage not in ELIGIBILITY_STAGES or status not in ELIGIBILITY_STATUSES:
            raise ValueError("Unsupported eligibility stage or status")
        with self.store.connect() as connection:
            self._row(connection, case_id, pra_id, {"PRA", "CONSORTIUM_MEMBER"})
            if submission_id:
                submission = self._row(connection, case_id, submission_id, {"EOI_SUBMISSION"})
                process_id = process_id or submission["process_id"]
            version = connection.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='ELIGIBILITY_REVIEW' AND pra_id=? AND json_extract(data_json,'$.review_stage')=?",
                (case_id, pra_id, stage),
            ).fetchone()[0]
        data = dict(payload) | {"review_stage": stage, "submission_id": submission_id, "review_version": int(version)}
        review = self._create(case_id, "ELIGIBILITY_REVIEW", data | {"status": status, "version_number": version, "confidentiality_level": "RESTRICTED_LEGAL"}, actor,
                              process_id=process_id, pra_id=pra_id, parent_id=submission_id, event_type="PRA_ELIGIBILITY_REVIEW_STARTED")
        for sequence, item in enumerate(payload.get("checklist") or [], 1):
            self.eligibility_checklist_item(case_id, review["id"], dict(item) | {"sequence": sequence}, actor)
        return self.get(case_id, review["id"])

    def eligibility_checklist_item(self, case_id: str, review_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, review_id, {"ELIGIBILITY_REVIEW"})
        response = str(payload.get("response") or "UNKNOWN").upper()
        if response not in CHECKLIST_RESPONSES:
            raise ValueError("Unsupported Section 29A checklist response")
        return self.add_item(case_id, review_id, dict(payload) | {
            "item_type": "SECTION_29A", "item_key": payload.get("item_key") or payload.get("section") or new_id(),
            "status": "REVIEW_REQUIRED" if response == "UNKNOWN" or payload.get("legal_review_required") else "RECORDED",
            "response": response,
        }, actor)

    def finalize_eligibility(self, case_id: str, review_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        review = self.get(case_id, review_id)
        if review["record_type"] != "ELIGIBILITY_REVIEW":
            raise ValueError("Eligibility review not found")
        conclusion = str(payload.get("status") or "REVIEW_REQUIRED").upper()
        if conclusion not in ELIGIBILITY_STATUSES:
            raise ValueError("Unsupported professional eligibility conclusion")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("Professional confirmation is required; the system cannot determine Section 29A eligibility")
        unresolved = [item for item in review["items"] if item["response"] == "UNKNOWN" or (item["data"].get("legal_review_required") and not item["data"].get("professional_conclusion"))]
        if conclusion == "ELIGIBLE" and unresolved:
            raise ValueError("UNKNOWN or unresolved legal-review checklist items prevent an ELIGIBLE conclusion")
        pra = self.get(case_id, str(review["pra_id"]))
        if conclusion == "ELIGIBLE" and pra["record_type"] == "PRA" and pra["data"].get("pra_type") == "CONSORTIUM":
            members = self.list(case_id, "CONSORTIUM_MEMBER", pra["id"])
            if not members:
                raise ValueError("A consortium requires member records and independent eligibility reviews")
            with self.store.connect() as connection:
                for member in members:
                    eligible = connection.execute(
                        """SELECT 1 FROM resolution_process_records WHERE case_id=? AND record_type='ELIGIBILITY_REVIEW'
                        AND pra_id=? AND status='ELIGIBLE' ORDER BY version_number DESC LIMIT 1""", (case_id, member["id"])
                    ).fetchone()
                    if not eligible:
                        raise ValueError("Every consortium member needs an independent ELIGIBLE professional review")
        event = "PRA_FINAL_ELIGIBILITY_REVIEW_COMPLETED" if review["data"].get("review_stage") in {"PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"} else "PRA_ELIGIBILITY_REVIEW_COMPLETED"
        return self._update(case_id, review_id, dict(payload) | {"status": conclusion, "reviewed_by": actor, "review_date": payload.get("review_date") or date.today().isoformat()}, actor,
                            event_type=event, freeze=True)

    def connected_person(self, case_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, pra_id, {"PRA", "CONSORTIUM_MEMBER"})
            self._document(connection, case_id, payload.get("evidence_document_id"))
        beneficial = str(payload.get("beneficial_owner") or "UNKNOWN").upper()
        if beneficial not in {"YES", "NO", "UNKNOWN"}:
            raise ValueError("Beneficial-owner status must be YES, NO or UNKNOWN")
        return self._create(case_id, "CONNECTED_PERSON", dict(payload) | {"beneficial_owner": beneficial, "status": payload.get("verification_status", "UNVERIFIED")}, actor, pra_id=pra_id)

    def _generate_list(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str, final: bool) -> Dict[str, Any]:
        record_type = "FINAL_LIST" if final else "PROVISIONAL_LIST"
        with self.store.connect() as connection:
            self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            version = connection.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type=? AND process_id=?",
                (case_id, record_type, process_id),
            ).fetchone()[0]
        if payload.get("professional_confirmed") is not True:
            raise ValueError("Professional confirmation is required before generating a PRA list")
        entries = payload.get("entries") or []
        if not entries:
            raise ValueError("At least one PRA list entry is required")
        listing = self._create(case_id, record_type, {
            "status": "DRAFT", "version_number": version, "generated_at": utc_now(),
            "template_status": f"{record_type}_STRUCTURED_REGISTER", "confidentiality_level": "RESTRICTED_PRA",
        }, actor, process_id=process_id)
        allowed = {"ELIGIBLE", "INELIGIBLE"} if final else {"ELIGIBLE", "INELIGIBLE", "REVIEW_REQUIRED"}
        for sequence, entry in enumerate(entries, 1):
            result = str(entry.get("result") or "REVIEW_REQUIRED").upper()
            if result not in allowed:
                raise ValueError("Unsupported PRA list result")
            review = self.get(case_id, str(entry.get("review_id") or ""))
            if review["record_type"] != "ELIGIBILITY_REVIEW" or review["pra_id"] != entry.get("pra_id"):
                raise ValueError("Every list entry must point to that PRA's eligibility review")
            if result == "ELIGIBLE" and review["status"] != "ELIGIBLE":
                raise ValueError("An unconfirmed eligibility review cannot produce an ELIGIBLE list result")
            self.add_item(case_id, listing["id"], {
                "item_type": "PRA_LIST_ENTRY", "item_key": str(entry["pra_id"]), "sequence": sequence,
                "status": result, "data": dict(entry) | {"review_version": review["version_number"]},
            }, actor)
        return self.get(case_id, listing["id"])

    def provisional_list(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        return self._generate_list(case_id, process_id, payload, actor, False)

    def final_list(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        return self._generate_list(case_id, process_id, payload, actor, True)

    def approve_list(self, case_id: str, list_id: str, actor: str) -> Dict[str, Any]:
        listing = self.get(case_id, list_id)
        if listing["record_type"] not in {"PROVISIONAL_LIST", "FINAL_LIST"}:
            raise ValueError("PRA list not found")
        event = "PROVISIONAL_PRA_LIST_APPROVED" if listing["record_type"] == "PROVISIONAL_LIST" else "FINAL_PRA_LIST_APPROVED"
        return self._update(case_id, list_id, {"status": "APPROVED", "approved_by": actor, "approved_at": utc_now()}, actor,
                            event_type=event, freeze=True)

    def issue_list(self, case_id: str, list_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        listing = self.get(case_id, list_id)
        if listing["record_type"] not in {"PROVISIONAL_LIST", "FINAL_LIST"} or listing["status"] not in {"APPROVED", "ISSUED"}:
            raise ValueError("Only an approved PRA list may be issued")
        if listing["status"] == "ISSUED":
            return listing | {"idempotent_replay": True}
        with self.store.transaction() as connection:
            now = utc_now()
            for entry in listing["items"]:
                pra = self._row(connection, case_id, entry["item_key"], {"PRA"})
                data = _from_json(pra["data_json"], {})
                key = f"list:{list_id}:{pra['id']}"
                connection.execute(
                    """INSERT OR IGNORE INTO resolution_process_dispatches
                    (id,case_id,record_id,pra_id,recipient,dispatch_type,dispatched_at,proof_document_id,version_snapshot_json,idempotency_key,created_by,created_at)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id(), case_id, list_id, pra["id"], data.get("email", ""), listing["record_type"], now,
                     payload.get("proof_document_id"), _json({"list_version": listing["version_number"], "result": entry["status"]}), key, actor, now),
                )
            connection.execute("UPDATE resolution_process_records SET status='ISSUED',updated_by=?,updated_at=? WHERE id=?", (actor, now, list_id))
        event = "PROVISIONAL_PRA_LIST_ISSUED" if listing["record_type"] == "PROVISIONAL_LIST" else "FINAL_PRA_LIST_ISSUED"
        self._emit(case_id, event, actor, list_id)
        return self.get(case_id, list_id)

    def objection(self, case_id: str, provisional_list_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            listing = self._row(connection, case_id, provisional_list_id, {"PROVISIONAL_LIST"})
            self._row(connection, case_id, pra_id, {"PRA"})
            if listing["status"] != "ISSUED":
                raise ValueError("Objections require an issued provisional list")
        return self._create(case_id, "ELIGIBILITY_OBJECTION", dict(payload) | {"status": "RECEIVED", "received_at": payload.get("received_at") or utc_now()}, actor,
                            process_id=listing["process_id"], pra_id=pra_id, parent_id=provisional_list_id, event_type="PRA_OBJECTION_RECEIVED")

    def decide_objection(self, case_id: str, objection_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        status = str(payload.get("status") or "").upper()
        if status not in {"ACCEPTED", "REJECTED", "PARTLY_ACCEPTED", "CLOSED"}:
            raise ValueError("A reasoned objection decision is required")
        if payload.get("professional_confirmed") is not True or not str(payload.get("decision_text") or "").strip():
            raise ValueError("Professional confirmation and decision text are required")
        return self._update(case_id, objection_id, dict(payload) | {"status": status, "decided_by": actor, "decided_at": utc_now()}, actor,
                            event_type="PRA_OBJECTION_DECIDED", freeze=True)

    # RFRP, matrix, issue package and bid-period controls -----------------
    def rfrp(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            version = connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='RFRP' AND process_id=?", (case_id, process_id)).fetchone()[0]
        return self._create(case_id, "RFRP", dict(payload) | {"status": "DRAFT", "version_number": version, "template_status": "RFRP_TEMPLATE_REQUIRED", "confidentiality_level": "RESTRICTED_PRA"}, actor, process_id=process_id)

    def evaluation_matrix(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, process_id, {"EOI_PROCESS"})
            version = connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='EVALUATION_MATRIX' AND process_id=?", (case_id, process_id)).fetchone()[0]
        matrix = self._create(case_id, "EVALUATION_MATRIX", dict(payload) | {"status": "DRAFT", "version_number": version, "template_status": "EVALUATION_MATRIX_TEMPLATE_REQUIRED", "confidentiality_level": "RESTRICTED_EVALUATION"}, actor, process_id=process_id)
        for sequence, criterion in enumerate(payload.get("criteria") or [], 1):
            self.matrix_criterion(case_id, matrix["id"], dict(criterion) | {"sequence": sequence}, actor)
        return self.get(case_id, matrix["id"])

    def matrix_criterion(self, case_id: str, matrix_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self._row(connection, case_id, matrix_id, {"EVALUATION_MATRIX"})
        scoring = str(payload.get("scoring_type") or "OTHER").upper()
        if scoring not in {"NUMERIC_FORMULA", "RANGE", "YES_NO", "MANUAL_PROFESSIONAL", "OTHER"}:
            raise ValueError("Unsupported evaluation scoring type")
        maximum = _decimal(payload.get("maximum_score"), "Maximum score")
        if maximum < 0:
            raise ValueError("Maximum score cannot be negative")
        return self.add_item(case_id, matrix_id, dict(payload) | {
            "item_type": "EVALUATION_CRITERION", "item_key": payload.get("criterion_id") or payload.get("item_key") or new_id(),
            "status": "DRAFT", "data": dict(payload) | {"scoring_type": scoring, "maximum_score": format(maximum, "f")},
        }, actor)

    def approve_process_document(self, case_id: str, record_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        record = self.get(case_id, record_id)
        if record["record_type"] not in {"RFRP", "EVALUATION_MATRIX"}:
            raise ValueError("Only RFRP or Evaluation Matrix can use this approval")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("CoC/RP confirmation is required")
        if record["record_type"] == "EVALUATION_MATRIX" and not record["items"]:
            raise ValueError("An Evaluation Matrix needs entered CoC-approved criteria")
        event = "RFRP_APPROVED" if record["record_type"] == "RFRP" else "EVALUATION_MATRIX_APPROVED"
        snapshot = {"data": record["data"], "criteria": record["items"]}
        return self._update(case_id, record_id, dict(payload) | {"status": "APPROVED", "approved_by": actor, "approved_at": utc_now(), "snapshot": snapshot}, actor,
                            event_type=event, freeze=True)

    def issue_process_document(self, case_id: str, record_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        """Advance immutable approved RFRP/Matrix content to issued lifecycle state."""
        record = self.get(case_id, record_id)
        if record["record_type"] not in {"RFRP", "EVALUATION_MATRIX"} or record["status"] not in {"APPROVED", "ISSUED"}:
            raise ValueError("Only an approved RFRP or Evaluation Matrix may be issued")
        if record["status"] == "ISSUED":
            return record | {"idempotent_replay": True}
        with self.store.transaction() as connection:
            data = record["data"] | {"issued_at": payload.get("issued_at") or utc_now(), "issued_by": actor}
            connection.execute(
                "UPDATE resolution_process_records SET status='ISSUED',data_json=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?",
                (_json(data), actor, utc_now(), record_id, case_id),
            )
        return self.get(case_id, record_id)

    def _eligible_on_final_list(self, case_id: str, process_id: str, pra_id: str) -> Dict[str, Any]:
        lists = [record for record in self.list(case_id, "FINAL_LIST") if record["process_id"] == process_id and record["status"] == "ISSUED"]
        for listing in reversed(lists):
            full = self.get(case_id, listing["id"])
            if any(item["item_key"] == pra_id and item["status"] == "ELIGIBLE" for item in full["items"]):
                return full
        raise ValueError("PRA is not ELIGIBLE on an issued final list")

    def issue_package(self, case_id: str, process_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        final_list = self._eligible_on_final_list(case_id, process_id, pra_id)
        rfrp = self.get(case_id, str(payload.get("rfrp_id") or ""))
        matrix = self.get(case_id, str(payload.get("evaluation_matrix_id") or ""))
        if rfrp["record_type"] != "RFRP" or matrix["record_type"] != "EVALUATION_MATRIX" or rfrp["process_id"] != process_id or matrix["process_id"] != process_id:
            raise ValueError("RFRP and Evaluation Matrix must belong to this EOI process")
        if rfrp["status"] not in {"APPROVED", "ISSUED"} or matrix["status"] not in {"APPROVED", "ISSUED"}:
            raise ValueError("Approved RFRP and Evaluation Matrix versions are required")
        undertaking = Phase4Core(self.store).get(case_id, str(payload.get("undertaking_id") or ""))
        if undertaking["record_type"] != "UNDERTAKING" or undertaking["status"] != "VERIFIED" or undertaking["data"].get("recipient_type") not in {"PRA", "PRA_CONSORTIUM_MEMBER"}:
            raise ValueError("A verified PRA confidentiality undertaking is required")
        im = Phase4Core(self.store).get(case_id, str(payload.get("im_version_id") or ""))
        if im["record_type"] != "IM_VERSION" or im["status"] not in {"FINAL", "ISSUED"}:
            raise ValueError("A finalized IM version is required")
        phase4 = Phase4Core(self.store)
        workspace_id = str(payload.get("vdr_workspace_id") or "")
        recipient_id = str(payload.get("vdr_recipient_id") or "")
        phase4.get(case_id, workspace_id)
        if not recipient_id:
            pra = self.get(case_id, pra_id)
            recipient = phase4.create(case_id, "vdr", "RECIPIENT", {"status": "ACTIVE", "record_key": pra_id, "data": {"pra_id": pra_id, "name": pra["data"].get("legal_name")}, "idempotency_key": f"phase6-vdr-recipient:{pra_id}"}, actor)
            recipient_id = recipient["id"]
        phase4.grant_vdr_access(case_id, workspace_id, recipient_id, {"undertaking_id": undertaking["id"], "folder_scope": payload.get("folder_scope") or ["IM", "RFRP", "EVALUATION_MATRIX"]}, actor)
        self._emit(case_id, "PRA_VDR_ACCESS_GRANTED", actor, f"{workspace_id}:{pra_id}")
        versions = {"final_list": final_list["version_number"], "rfrp": rfrp["version_number"], "evaluation_matrix": matrix["version_number"], "im": im["version_number"]}
        package = self._create(case_id, "ISSUE_PACKAGE", dict(payload) | {"status": "ISSUED", "issued_at": utc_now(), "versions": versions, "vdr_recipient_id": recipient_id, "confidentiality_level": "RESTRICTED_PRA", "snapshot": versions}, actor, process_id=process_id, pra_id=pra_id, event_type="PRA_ISSUE_PACKAGE_SENT")
        self._dispatch(case_id, package["id"], pra_id, "ISSUE_PACKAGE", payload, actor, versions)
        return self.get(case_id, package["id"])

    def _dispatch(self, case_id: str, record_id: str, pra_id: Optional[str], dispatch_type: str,
                  payload: Dict[str, Any], actor: str, snapshot: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        key = str(payload.get("dispatch_idempotency_key") or f"{dispatch_type}:{record_id}:{pra_id or 'all'}")
        with self.store.transaction() as connection:
            self._row(connection, case_id, record_id)
            if pra_id:
                pra = self._row(connection, case_id, pra_id, {"PRA"})
                recipient = _from_json(pra["data_json"], {}).get("email", "")
            else:
                recipient = str(payload.get("recipient") or "")
            self._document(connection, case_id, payload.get("proof_document_id"))
            existing = connection.execute("SELECT * FROM resolution_process_dispatches WHERE case_id=? AND idempotency_key=?", (case_id, key)).fetchone()
            if existing:
                return dict(existing) | {"idempotent_replay": True}
            dispatch_id, now = new_id(), utc_now()
            connection.execute(
                """INSERT INTO resolution_process_dispatches
                (id,case_id,record_id,pra_id,recipient,dispatch_type,dispatched_at,proof_document_id,version_snapshot_json,idempotency_key,created_by,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dispatch_id, case_id, record_id, pra_id, recipient, dispatch_type, payload.get("dispatched_at") or now,
                 payload.get("proof_document_id"), _json(snapshot or {}), key, actor, now),
            )
            return dict(connection.execute("SELECT * FROM resolution_process_dispatches WHERE id=?", (dispatch_id,)).fetchone())

    def pra_query(self, case_id: str, process_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        visibility = str(payload.get("visibility") or "PRIVATE_RESPONSE").upper()
        if visibility not in {"PRIVATE_RESPONSE", "SHARED_WITH_ALL_PRAS", "PROCESS_ADDENDUM_REQUIRED"}:
            raise ValueError("Unsupported query visibility")
        return self._create(case_id, "PRA_QUERY", dict(payload) | {"status": payload.get("status", "OPEN"), "visibility": visibility, "received_at": payload.get("received_at") or utc_now()}, actor, process_id=process_id, pra_id=pra_id, event_type="PRA_QUERY_RECEIVED")

    def respond_query(self, case_id: str, query_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        if not str(payload.get("response") or "").strip():
            raise ValueError("A reviewed response is required")
        result = self._update(case_id, query_id, dict(payload) | {"status": "ANSWERED", "response_date": payload.get("response_date") or date.today().isoformat(), "reviewed_by": actor}, actor,
                              event_type="PRA_QUERY_RESPONDED")
        return result

    def site_visit(self, case_id: str, process_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        if not payload.get("visit_date") or not str(payload.get("location") or "").strip():
            raise ValueError("Site-visit date and location are required")
        return self._create(case_id, "SITE_VISIT", dict(payload) | {"status": payload.get("status", "RECORDED")}, actor, process_id=process_id, pra_id=pra_id)

    def addendum(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        applies = str(payload.get("applies_to") or "OTHER").upper()
        if applies not in {"EOI", "RFRP", "EVALUATION_MATRIX", "IM", "VDR", "OTHER"}:
            raise ValueError("Unsupported addendum target")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("An approved addendum requires professional confirmation")
        with self.store.connect() as connection:
            version = connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='PROCESS_ADDENDUM' AND process_id=?", (case_id, process_id)).fetchone()[0]
        addendum = self._create(case_id, "PROCESS_ADDENDUM", dict(payload) | {"status": "ISSUED", "version_number": version, "applies_to": applies, "issued_at": utc_now()}, actor, process_id=process_id, event_type="PROCESS_ADDENDUM_ISSUED")
        for pra_id in payload.get("pra_ids") or []:
            self._dispatch(case_id, addendum["id"], pra_id, "PROCESS_ADDENDUM", payload | {"dispatch_idempotency_key": f"addendum:{addendum['id']}:{pra_id}"}, actor, {"addendum_version": version, "applies_to": applies})
        return self.get(case_id, addendum["id"])

    def deadline_revision(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        required = ("deadline_key", "new_date", "reason", "approval_source", "effective_date")
        if any(not str(payload.get(key) or "").strip() for key in required):
            raise ValueError("Deadline revision requires date, reason, approval source and effective date")
        with self.store.transaction() as connection:
            self._row(connection, case_id, process_id, {"EOI_PROCESS", "RFRP"})
            self._external(connection, "coc_meetings", case_id, payload.get("coc_meeting_id"), "CoC meeting")
            self._external(connection, "coc_resolutions", case_id, payload.get("coc_resolution_id"), "CoC resolution")
            if payload.get("addendum_record_id"):
                self._row(connection, case_id, payload["addendum_record_id"], {"PROCESS_ADDENDUM"})
            revision_id = new_id()
            connection.execute(
                """INSERT INTO process_deadline_revisions
                (id,case_id,process_record_id,deadline_key,old_date,new_date,reason,approval_source,coc_meeting_id,coc_resolution_id,effective_date,addendum_record_id,created_by,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (revision_id, case_id, process_id, payload["deadline_key"], payload.get("old_date"), payload["new_date"], payload["reason"],
                 payload["approval_source"], payload.get("coc_meeting_id"), payload.get("coc_resolution_id"), payload["effective_date"], payload.get("addendum_record_id"), actor, utc_now()),
            )
            return dict(connection.execute("SELECT * FROM process_deadline_revisions WHERE id=?", (revision_id,)).fetchone())

    # Resolution plans, reviews and evaluation ---------------------------
    def resolution_plan(self, case_id: str, process_id: str, pra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        self._eligible_on_final_list(case_id, process_id, pra_id)
        if not payload.get("document_id"):
            raise ValueError("Resolution Plan document is required")
        with self.store.transaction() as connection:
            self._document(connection, case_id, payload["document_id"])
            previous = connection.execute(
                "SELECT * FROM resolution_process_records WHERE case_id=? AND record_type='RESOLUTION_PLAN' AND process_id=? AND pra_id=? ORDER BY version_number DESC LIMIT 1",
                (case_id, process_id, pra_id),
            ).fetchone()
            version = int(previous["version_number"] + 1) if previous else 1
        status = str(payload.get("status") or "RECEIVED").upper()
        if status not in PLAN_STATUSES:
            raise ValueError("Unsupported Resolution Plan status")
        plan = self._create(case_id, "RESOLUTION_PLAN", dict(payload) | {
            "status": status, "version_number": version, "plan_version": version,
            "received_at": payload.get("received_at") or utc_now(), "supersedes_plan_version_id": previous["id"] if previous else None,
            "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN",
        }, actor, process_id=process_id, pra_id=pra_id, plan_id=None,
            event_type="RESOLUTION_PLAN_REVISED" if previous else "RESOLUTION_PLAN_RECEIVED")
        if plan.get("idempotent_replay"):
            return plan
        if previous:
            with self.store.transaction() as connection:
                connection.execute("UPDATE resolution_process_records SET status='SUPERSEDED',updated_by=?,updated_at=? WHERE id=?", (actor, utc_now(), previous["id"]))
        return self.get(case_id, plan["id"])

    def section30_review(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            plan = self._row(connection, case_id, plan_id, {"RESOLUTION_PLAN"})
        review = self._create(case_id, "SECTION30_REVIEW", {"status": "IN_PROGRESS", "confidentiality_level": "RESTRICTED_LEGAL"}, actor,
                              process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, parent_id=plan_id)
        for sequence, item in enumerate(payload.get("items") or [], 1):
            response = str(item.get("response") or "MORE_INFORMATION_REQUIRED").upper()
            if response not in SECTION30_RESPONSES:
                raise ValueError("Unsupported Section 30(2) checklist response")
            self.add_item(case_id, review["id"], dict(item) | {"item_type": "SECTION_30_2", "item_key": item.get("item_key") or item.get("category") or f"ITEM-{sequence}", "sequence": sequence, "status": response, "response": response}, actor)
        return self.get(case_id, review["id"])

    def finalize_section30(self, case_id: str, review_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        review = self.get(case_id, review_id)
        if review["record_type"] != "SECTION30_REVIEW":
            raise ValueError("Section 30(2) review not found")
        conclusion = str(payload.get("status") or "").upper()
        if conclusion not in {"COMPLIANT", "NON_COMPLIANT", "MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED"}:
            raise ValueError("A valid professional Section 30(2) conclusion is required")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("RP/legal professional confirmation is required")
        unresolved = [item for item in review["items"] if item["response"] in {"MORE_INFORMATION_REQUIRED", "LEGAL_REVIEW_REQUIRED"}]
        if conclusion == "COMPLIANT" and unresolved:
            raise ValueError("Unresolved Section 30(2) items prevent a COMPLIANT conclusion")
        result = self._update(case_id, review_id, dict(payload) | {"status": conclusion, "reviewed_by": actor, "reviewed_at": utc_now()}, actor,
                              event_type="SECTION_30_REVIEW_COMPLETED", freeze=True)
        with self.store.transaction() as connection:
            connection.execute("UPDATE resolution_process_records SET status=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?", (conclusion, actor, utc_now(), review["plan_id"], case_id))
        return result

    def final_eligibility_recheck(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        if plan["record_type"] != "RESOLUTION_PLAN":
            raise ValueError("Resolution Plan not found")
        return self.eligibility_review(case_id, str(plan["pra_id"]), dict(payload) | {"review_stage": payload.get("review_stage", "PLAN_SUBMISSION_RECHECK")}, actor,
                                       process_id=plan["process_id"])

    def plan_query(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        category = str(payload.get("category") or "OTHER").upper()
        if category not in {"RFRP_DEVIATION", "MISSING_INFORMATION", "SECTION_30", "SECTION_29A", "FINANCIAL", "IMPLEMENTATION", "SECURITY", "OTHER"}:
            raise ValueError("Unsupported plan-query category")
        return self._create(case_id, "PLAN_REVIEW_QUERY", dict(payload) | {"status": "OPEN", "category": category, "issued_at": payload.get("issued_at") or utc_now(), "confidentiality_level": "RESTRICTED_LEGAL"}, actor,
                            process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, parent_id=plan_id, event_type="PLAN_REVIEW_QUERY_RAISED")

    def respond_plan_query(self, case_id: str, query_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        return self._update(case_id, query_id, dict(payload) | {"status": "ANSWERED", "response_date": payload.get("response_date") or date.today().isoformat()}, actor,
                            event_type="PLAN_REVIEW_QUERY_RESPONDED")

    def evaluate_plan(self, case_id: str, plan_id: str, matrix_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        matrix = self.get(case_id, matrix_id)
        if plan["record_type"] != "RESOLUTION_PLAN" or matrix["record_type"] != "EVALUATION_MATRIX" or matrix["status"] != "APPROVED" or plan["process_id"] != matrix["process_id"]:
            raise ValueError("A compliant Plan and approved Matrix from the same process are required")
        if plan["status"] != "COMPLIANT":
            raise ValueError("Only a professionally COMPLIANT Plan may be evaluated")
        reviews = [row for row in self.list(case_id, "ELIGIBILITY_REVIEW", plan["pra_id"]) if row["data"].get("review_stage") in {"PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"}]
        if not reviews or reviews[-1]["status"] != "ELIGIBLE":
            raise ValueError("A final ELIGIBLE Section 29A recheck is required")
        supplied = {str(item.get("criterion_id")): item for item in payload.get("scores") or []}
        evaluation = self._create(case_id, "PLAN_EVALUATION", {"status": "DRAFT", "matrix_version": matrix["version_number"], "confidentiality_level": "RESTRICTED_EVALUATION"}, actor,
                                  process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, parent_id=matrix_id)
        total = Decimal("0")
        for sequence, criterion in enumerate(matrix["items"], 1):
            details = criterion["data"]
            score_input = supplied.get(criterion["item_key"])
            if not score_input:
                raise ValueError(f"Score required for criterion {criterion['item_key']}")
            maximum = _decimal(details.get("maximum_score"), "Maximum score")
            scoring_type = details.get("scoring_type")
            if scoring_type == "MANUAL_PROFESSIONAL":
                if not score_input.get("authorized_scorer") or not str(score_input.get("reason") or "").strip():
                    raise ValueError("Manual criterion requires an authorized scorer and reason")
                score = _decimal(score_input.get("score"), "Manual score")
            elif scoring_type == "YES_NO":
                score = maximum if bool(score_input.get("value")) else Decimal("0")
            else:
                value = _decimal(score_input.get("value", score_input.get("score")), "Criterion input")
                mode = str((details.get("formula") or {}).get("mode") or "DIRECT").upper()
                if mode == "PERCENT_OF_MAX":
                    score = maximum * value / Decimal("100")
                else:
                    score = value
            if score < 0 or score > maximum:
                raise ValueError("Criterion score must be within zero and maximum score")
            score = score.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            total += score
            self.add_item(case_id, evaluation["id"], {
                "item_type": "EVALUATION_SCORE", "item_key": criterion["item_key"], "sequence": sequence,
                "status": "SCORED", "data": {"score": format(score, ".2f"), "maximum_score": format(maximum, ".2f"), "input": score_input, "criterion_snapshot": details},
            }, actor)
        total = total.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
        return self._update(case_id, evaluation["id"], {"status": "LOCKED", "total_score": format(total, ".2f"), "rank": payload.get("rank"), "scored_by": actor, "scored_at": utc_now(), "snapshot": {"matrix_id": matrix_id, "matrix_version": matrix["version_number"], "plan_id": plan_id}}, actor,
                            event_type="PLAN_EVALUATION_COMPLETED", freeze=True)

    # Negotiation, CoC, SRA and NCLT -------------------------------------
    def negotiation_process(self, case_id: str, process_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        kind = str(payload.get("negotiation_type") or "OTHER").upper()
        if kind not in {"DIRECT_NEGOTIATION", "CHALLENGE_MECHANISM", "REVISED_PLAN_ROUND", "SEALED_REVISION", "OTHER"}:
            raise ValueError("Unsupported negotiation process type")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("CoC-approved negotiation rules require professional confirmation")
        return self._create(case_id, "NEGOTIATION_PROCESS", dict(payload) | {"status": "APPROVED", "negotiation_type": kind, "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor, process_id=process_id)

    def negotiation_round(self, case_id: str, negotiation_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        negotiation = self.get(case_id, negotiation_id)
        if negotiation["record_type"] != "NEGOTIATION_PROCESS":
            raise ValueError("Negotiation process not found")
        with self.store.connect() as connection:
            version = connection.execute("SELECT COALESCE(MAX(version_number),0)+1 FROM resolution_process_records WHERE case_id=? AND record_type='NEGOTIATION_ROUND' AND parent_record_id=?", (case_id, negotiation_id)).fetchone()[0]
        return self._create(case_id, "NEGOTIATION_ROUND", dict(payload) | {"status": payload.get("status", "OPEN"), "version_number": version, "round_number": version, "started_at": payload.get("started_at") or utc_now(), "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor,
                            process_id=negotiation["process_id"], parent_id=negotiation_id, event_type="NEGOTIATION_ROUND_OPENED")

    def negotiation_submission(self, case_id: str, round_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        round_record = self.get(case_id, round_id); plan = self.get(case_id, plan_id)
        if round_record["record_type"] != "NEGOTIATION_ROUND" or plan["record_type"] != "RESOLUTION_PLAN" or round_record["process_id"] != plan["process_id"]:
            raise ValueError("Negotiation round and Plan must belong to the same process")
        return self._create(case_id, "NEGOTIATION_SUBMISSION", dict(payload) | {"status": "RECEIVED", "submitted_at": payload.get("submitted_at") or utc_now(), "plan_version": plan["version_number"], "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor,
                            process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, parent_id=round_id)

    def close_negotiation_round(self, case_id: str, round_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        return self._update(case_id, round_id, dict(payload) | {"status": "CLOSED", "closed_at": payload.get("closed_at") or utc_now()}, actor,
                            event_type="NEGOTIATION_ROUND_CLOSED", freeze=True)

    def place_before_coc(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        if plan["record_type"] != "RESOLUTION_PLAN" or plan["status"] != "COMPLIANT":
            raise ValueError("Only a professionally COMPLIANT exact Plan version may be placed before CoC")
        meeting_id = str(payload.get("meeting_id") or "")
        agenda_version_id = str(payload.get("agenda_version_id") or "")
        if not meeting_id or not agenda_version_id:
            raise ValueError("Existing CoC meeting and agenda version are required")
        item_payload = {
            "agenda_type": "FOR_VOTING", "title": payload.get("title") or f"Consider Resolution Plan V{plan['version_number']}",
            "agenda_note": payload.get("agenda_note", ""), "proposed_resolution_text": payload.get("proposed_resolution_text", ""),
            "requires_resolution": True, "requires_voting": True,
        }
        agenda = CocMeetingCore(self.store).add_agenda_item(case_id, meeting_id, agenda_version_id, item_payload, actor)
        agenda_item = agenda["items"][-1]
        placement = self._create(case_id, "COC_PLAN_PLACEMENT", dict(payload) | {"status": "PLACED", "meeting_id": meeting_id, "agenda_version_id": agenda_version_id, "agenda_item_id": agenda_item["id"], "plan_version": plan["version_number"], "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor,
                                 process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, event_type="PLAN_PLACED_BEFORE_COC")
        self._link(case_id, placement["id"], actor, "COC_AGENDA_ITEM", external_entity_type="coc_agenda_version_items", external_entity_id=agenda_item["id"])
        return self.get(case_id, placement["id"])

    def link_plan_vote(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        result_id = str(payload.get("voting_result_id") or "")
        with self.store.connect() as connection:
            result = connection.execute("SELECT * FROM coc_voting_results WHERE id=? AND case_id=?", (result_id, case_id)).fetchone()
            if not result:
                raise ValueError("Existing CoC voting result does not belong to this case")
            if str(result["status"]).upper() != "FINAL":
                raise ValueError("Only a FINAL CoC voting result may be linked to a Resolution Plan")
            resolution = connection.execute(
                "SELECT agenda_item_id FROM coc_resolutions WHERE id=? AND case_id=?",
                (result["resolution_id"], case_id),
            ).fetchone()
            placements = connection.execute(
                "SELECT data_json FROM resolution_process_records WHERE case_id=? AND record_type='COC_PLAN_PLACEMENT' AND plan_id=?",
                (case_id, plan_id),
            ).fetchall()
            valid_agenda_items = {
                str(_from_json(row["data_json"], {}).get("agenda_item_id") or "")
                for row in placements
            }
            if not resolution or str(resolution["agenda_item_id"]) not in valid_agenda_items:
                raise ValueError("The finalized vote is not for this Plan's CoC agenda placement")
        vote = self._create(case_id, "PLAN_VOTE_LINK", dict(payload) | {"status": payload.get("outcome", result["result"]), "voting_result_id": result_id, "plan_version": plan["version_number"], "coc_snapshot": _from_json(result["snapshot_json"], {}), "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor,
                            process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, event_type="PLAN_VOTING_COMPLETED")
        return vote

    def successful_ra(self, case_id: str, plan_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        plan = self.get(case_id, plan_id)
        vote = self.get(case_id, str(payload.get("plan_vote_link_id") or ""))
        if vote["record_type"] != "PLAN_VOTE_LINK" or vote["plan_id"] != plan_id or str(vote["status"]).upper() not in {"APPROVED", "PASSED"}:
            raise ValueError("Successful RA selection requires an approved vote linked to the exact Plan version")
        if payload.get("professional_confirmed") is not True:
            raise ValueError("Professional confirmation is required for Successful RA selection")
        status = "PERFORMANCE_SECURITY_PENDING" if payload.get("performance_security_required", True) else "READY_FOR_NCLT"
        return self._create(case_id, "SUCCESSFUL_RA", dict(payload) | {"status": status, "selected_plan_version": plan["version_number"], "selection_date": payload.get("selection_date") or date.today().isoformat(), "confidentiality_level": "RESTRICTED_RESOLUTION_PLAN"}, actor,
                            process_id=plan["process_id"], pra_id=plan["pra_id"], plan_id=plan_id, parent_id=vote["id"], event_type="SUCCESSFUL_RA_SELECTED")

    def verify_performance_security(self, case_id: str, sra_id: str, deposit_id: str, actor: str) -> Dict[str, Any]:
        sra = self.get(case_id, sra_id); deposit = self.get(case_id, deposit_id)
        if sra["record_type"] != "SUCCESSFUL_RA" or deposit["record_type"] != "PROCESS_DEPOSIT" or deposit["pra_id"] != sra["pra_id"] or deposit["data"].get("deposit_type") != "PERFORMANCE_SECURITY" or deposit["status"] != "VERIFIED":
            raise ValueError("Verified performance security for the selected PRA is required")
        with self.store.transaction() as connection:
            connection.execute("UPDATE resolution_process_records SET status='READY_FOR_NCLT',data_json=json_set(data_json,'$.performance_security_id',?),updated_by=?,updated_at=? WHERE id=?", (deposit_id, actor, utc_now(), sra_id))
        return self.get(case_id, sra_id)

    def plan_approval_workspace(self, case_id: str, sra_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        idempotency_key = str(payload.get("idempotency_key") or "").strip()
        if idempotency_key:
            with self.store.connect() as connection:
                replay = connection.execute(
                    "SELECT id FROM resolution_process_records WHERE case_id=? AND idempotency_key=?",
                    (case_id, idempotency_key),
                ).fetchone()
            if replay:
                return self.get(case_id, replay["id"]) | {"idempotent_replay": True}
        sra = self.get(case_id, sra_id)
        if sra["record_type"] != "SUCCESSFUL_RA" or sra["status"] != "READY_FOR_NCLT":
            raise ValueError("Successful RA must be READY_FOR_NCLT")
        plan_id = str(sra["plan_id"])
        section30 = self.get(case_id, str(payload.get("section30_review_id") or ""))
        eligibility = self.get(case_id, str(payload.get("final_eligibility_review_id") or ""))
        if section30["record_type"] != "SECTION30_REVIEW" or section30["plan_id"] != plan_id or section30["status"] != "COMPLIANT":
            raise ValueError("Completed COMPLIANT Section 30(2) review is required")
        if eligibility["record_type"] != "ELIGIBILITY_REVIEW" or eligibility["pra_id"] != sra["pra_id"] or eligibility["status"] != "ELIGIBLE":
            raise ValueError("Final ELIGIBLE Section 29A review is required")
        application = self.store.create_module_record(case_id, "applications", {
            "application_type": "Resolution Plan Approval under Sections 30/31", "number": payload.get("application_number", ""),
            "filing_date": payload.get("filing_date"), "parties": payload.get("parties", ""),
            "relief_sought": payload.get("relief_sought", "Approval of the CoC-approved Resolution Plan"),
            "status": "filed" if payload.get("filing_date") else "draft",
        }, actor)
        workspace = self._create(case_id, "PLAN_APPROVAL_WORKSPACE", dict(payload) | {
            "status": "FILED" if payload.get("filing_date") else "DRAFT",
            "application_id": application["id"], "selected_plan_id": plan_id,
            "plan_approval_application_template_status": "PLAN_APPROVAL_APPLICATION_TEMPLATE_REQUIRED",
            "compliance_certificate_template_status": payload.get("compliance_certificate_template_status", "TEMPLATE_REQUIRED"),
            "confidentiality_level": "RESTRICTED_LEGAL",
        }, actor, process_id=sra["process_id"], pra_id=sra["pra_id"], plan_id=plan_id, parent_id=sra_id,
            event_type="PLAN_APPROVAL_APPLICATION_FILED" if payload.get("filing_date") else "PLAN_APPROVAL_APPLICATION_CREATED")
        self._link(case_id, workspace["id"], actor, "APPLICATION", external_entity_type="applications", external_entity_id=application["id"])
        if payload.get("hearing_at"):
            hearing = self.store.create_module_record(case_id, "hearings", {
                "tribunal": "NCLT", "bench": payload.get("bench", ""), "hearing_at": payload["hearing_at"],
                "purpose": payload.get("hearing_purpose", "Resolution Plan approval"), "status": "scheduled",
            }, actor)
            hearing_link = self._create(case_id, "HEARING_LINK", {"status": "SCHEDULED", "hearing_id": hearing["id"], "next_hearing_at": payload["hearing_at"], "confidentiality_level": "RESTRICTED_LEGAL"}, actor,
                                        process_id=sra["process_id"], pra_id=sra["pra_id"], plan_id=plan_id, parent_id=workspace["id"])
            self._link(case_id, hearing_link["id"], actor, "HEARING", external_entity_type="hearings", external_entity_id=hearing["id"])
        return self.get(case_id, workspace["id"])

    def record_plan_approval_order(self, case_id: str, workspace_id: str, payload: Dict[str, Any], actor: str) -> Dict[str, Any]:
        workspace = self.get(case_id, workspace_id)
        if workspace["record_type"] != "PLAN_APPROVAL_WORKSPACE":
            raise ValueError("Plan Approval workspace not found")
        outcome = str(payload.get("outcome") or "").upper()
        if outcome not in {"NCLT_APPROVED", "NCLT_REJECTED", "PENDING"}:
            raise ValueError("Unsupported Plan Approval outcome")
        if payload.get("document_id"):
            self._link(case_id, workspace_id, actor, "NCLT_ORDER", document_id=payload["document_id"])
        with self.store.transaction() as connection:
            connection.execute("UPDATE resolution_process_records SET status=?,data_json=json_set(data_json,'$.order_date',?,'$.order_document_id',?),updated_by=?,updated_at=? WHERE id=?", (outcome, payload.get("order_date"), payload.get("document_id"), actor, utc_now(), workspace_id))
        return self.get(case_id, workspace_id)

    # Summary and access --------------------------------------------------
    def summary(self, case_id: str) -> Dict[str, Any]:
        records = self.list(case_id)
        by_type: Dict[str, List[Dict[str, Any]]] = {}
        for record in records:
            by_type.setdefault(record["record_type"], []).append(record)
        latest = lambda kind: by_type.get(kind, [])[-1] if by_type.get(kind) else None
        submissions = by_type.get("EOI_SUBMISSION", [])
        reviews = by_type.get("ELIGIBILITY_REVIEW", [])
        plans = by_type.get("RESOLUTION_PLAN", [])
        queries = by_type.get("PRA_QUERY", [])
        plan_queries = by_type.get("PLAN_REVIEW_QUERY", [])
        final_list = latest("FINAL_LIST")
        final_count = 0
        if final_list:
            final_count = sum(item["status"] == "ELIGIBLE" for item in self.get(case_id, final_list["id"])["items"])
        hearings = by_type.get("HEARING_LINK", [])
        return {
            "EOI_process_status": latest("EOI_PROCESS")["status"] if latest("EOI_PROCESS") else "NOT_RECORDED",
            "EOI_due_date": (latest("EOI_PROCESS") or {"data": {}})["data"].get("EOI_due_date"),
            "EOIs_received": len(submissions), "EOIs_incomplete": sum(row["status"] == "INCOMPLETE" for row in submissions),
            "PRAs_under_review": sum(row["status"] in {"IN_PROGRESS", "REVIEW_REQUIRED", "LEGAL_REVIEW_REQUIRED", "MORE_INFORMATION_REQUIRED"} for row in reviews),
            "provisional_eligible_count": self._list_count(case_id, "PROVISIONAL_LIST", "ELIGIBLE"),
            "provisional_ineligible_count": self._list_count(case_id, "PROVISIONAL_LIST", "INELIGIBLE"),
            "objections_open": sum(row["status"] in {"RECEIVED", "UNDER_REVIEW", "MORE_INFORMATION_REQUIRED"} for row in by_type.get("ELIGIBILITY_OBJECTION", [])),
            "final_eligible_PRA_count": final_count,
            "RFRP_status": latest("RFRP")["status"] if latest("RFRP") else "NOT_RECORDED",
            "evaluation_matrix_status": latest("EVALUATION_MATRIX")["status"] if latest("EVALUATION_MATRIX") else "NOT_RECORDED",
            "issue_packages_sent": len(by_type.get("ISSUE_PACKAGE", [])),
            "PRA_queries_open": sum(row["status"] in {"OPEN", "UNDER_REVIEW"} for row in queries),
            "plans_received": len(plans),
            "plans_under_compliance_review": sum(row["status"] == "UNDER_COMPLIANCE_REVIEW" for row in plans),
            "compliant_plans": sum(row["status"] == "COMPLIANT" for row in plans),
            "non_compliant_plans": sum(row["status"] == "NON_COMPLIANT" for row in plans),
            "final_eligibility_pending": sum(row["data"].get("review_stage") in {"PLAN_SUBMISSION_RECHECK", "PRE_COC_VOTE_RECHECK"} and row["status"] != "ELIGIBLE" for row in reviews),
            "plan_queries_open": sum(row["status"] in {"OPEN", "UNDER_REVIEW"} for row in plan_queries),
            "plans_evaluated": len(by_type.get("PLAN_EVALUATION", [])),
            "negotiation_status": latest("NEGOTIATION_ROUND")["status"] if latest("NEGOTIATION_ROUND") else "NOT_RECORDED",
            "plans_before_CoC": len(by_type.get("COC_PLAN_PLACEMENT", [])),
            "plan_vote_status": latest("PLAN_VOTE_LINK")["status"] if latest("PLAN_VOTE_LINK") else "NOT_RECORDED",
            "successful_RA": (latest("SUCCESSFUL_RA") or {"pra_id": None})["pra_id"],
            "performance_security_status": next((row["status"] for row in reversed(by_type.get("PROCESS_DEPOSIT", [])) if row["data"].get("deposit_type") == "PERFORMANCE_SECURITY"), "NOT_RECORDED"),
            "plan_approval_application_status": latest("PLAN_APPROVAL_WORKSPACE")["status"] if latest("PLAN_APPROVAL_WORKSPACE") else "NOT_RECORDED",
            "next_plan_approval_hearing": min((row["data"].get("next_hearing_at") for row in hearings if row["data"].get("next_hearing_at")), default=None),
        }

    def _list_count(self, case_id: str, record_type: str, status: str) -> int:
        lists = self.list(case_id, record_type)
        if not lists:
            return 0
        return sum(item["status"] == status for item in self.get(case_id, lists[-1]["id"])["items"])

    @staticmethod
    def can_read_sensitive(role: str) -> bool:
        return str(role or "").lower() in {"admin", "administrator", "professional", "manager"}
