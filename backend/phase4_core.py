"""CIRP-057--076 operational control registers.

This module deliberately models controls, evidence and approvals rather than
attempting to replace accounting, tax, banking, valuation or VDR products.
All money is stored as integer paise and every record is case-isolated.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from typing import Any, Dict, Iterable, List, Optional
import sqlite3

from database import CasefileDatabase, _from_json, _json, new_id, utc_now
from workflow import EventEngine


CONFIDENTIAL_LEVELS = {"NORMAL", "CONFIDENTIAL_CIRP", "RESTRICTED_VALUATION", "RESTRICTED_RESOLUTION_PLAN"}
UNDERTAKING_TYPES = {"COC_MEMBER", "PRA", "PRA_CONSORTIUM_MEMBER", "ADVISOR", "OTHER"}
ASSET_MOVEMENTS = {"IN", "OUT", "TRANSFER", "RELOCATION", "TEMPORARY_REMOVAL", "RETURN", "DISPOSAL_PROPOSED"}


def _paise(value: Any, label: str = "Amount") -> int:
    try:
        return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except Exception as exc:
        raise ValueError(f"{label} must be a valid amount") from exc


def _amount(value: Any) -> str:
    return format(Decimal(int(value or 0)) / 100, ".2f")


class Phase4Core:
    """Case-isolated service for operations, valuation, IM and controlled VDR."""

    def __init__(self, store: CasefileDatabase):
        self.store = store

    def _emit(self, case_id: str, event_type: str, actor_id: str, source_id: str, metadata: Optional[Dict[str, Any]] = None) -> None:
        EventEngine(self.store).record_event(
            case_id, event_type, date.today().isoformat(), actor_id, source_type="phase4",
            source_id=source_id, metadata=metadata or {}, idempotency_key=f"phase4:{event_type}:{source_id}",
        )

    @staticmethod
    def _row(row: sqlite3.Row) -> Dict[str, Any]:
        result = dict(row)
        for field in ("data_json", "source_json", "snapshot_json"):
            result[field[:-5]] = _from_json(result.pop(field), {})
        result["amount"] = _amount(result["amount_paise"])
        result["tax_amount"] = _amount(result["tax_paise"])
        return result

    def _record_row(self, connection: sqlite3.Connection, case_id: str, record_id: str) -> sqlite3.Row:
        row = connection.execute("SELECT * FROM phase4_records WHERE id=? AND case_id=? AND archived_at IS NULL", (record_id, case_id)).fetchone()
        if not row:
            raise KeyError("Phase 4 record not found")
        return row

    @staticmethod
    def _document(connection: sqlite3.Connection, case_id: str, document_id: Optional[str]) -> None:
        if document_id and not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, case_id)).fetchone():
            raise ValueError("Linked document does not belong to this case")

    def create(self, case_id: str, domain: str, record_type: str, payload: Dict[str, Any], actor_id: str,
               event_type: Optional[str] = None) -> Dict[str, Any]:
        key = str(payload.get("idempotency_key") or "").strip()
        with self.store.transaction() as connection:
            self.store.ensure_case(connection, case_id)
            if key:
                replay = connection.execute("SELECT * FROM phase4_records WHERE case_id=? AND idempotency_key=?", (case_id, key)).fetchone()
                if replay:
                    return self._row(replay) | {"idempotent_replay": True}
            self._document(connection, case_id, payload.get("document_id"))
            level = str(payload.get("confidentiality_level") or "NORMAL").upper()
            if level not in CONFIDENTIAL_LEVELS:
                raise ValueError("Unsupported confidentiality classification")
            now, record_id = utc_now(), new_id()
            data = dict(payload.get("data") or {})
            # Preserve any named fields not reserved for record metadata in the structured payload.
            reserved = {"idempotency_key", "record_key", "parent_record_id", "status", "period_from", "period_to", "effective_date", "amount", "tax_amount", "document_id", "confidentiality_level", "source", "data", "snapshot", "version_number"}
            data.update({k: v for k, v in payload.items() if k not in reserved})
            connection.execute(
                """INSERT INTO phase4_records(id,case_id,domain,record_type,record_key,parent_record_id,status,period_from,period_to,effective_date,
                amount_paise,tax_paise,document_id,confidentiality_level,source_json,data_json,snapshot_json,version_number,idempotency_key,
                created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (record_id, case_id, domain, record_type, str(payload.get("record_key") or ""), payload.get("parent_record_id"),
                 str(payload.get("status") or "DRAFT").upper(), payload.get("period_from"), payload.get("period_to"), payload.get("effective_date"),
                 _paise(payload.get("amount"), "Amount"), _paise(payload.get("tax_amount"), "Tax amount"), payload.get("document_id"), level,
                 _json(payload.get("source") or {}), _json(data), _json(payload.get("snapshot") or {}), int(payload.get("version_number") or 1), key,
                 actor_id, actor_id, now, now),
            )
            self.store.audit(connection, actor_id, "PHASE4_RECORD_CREATED", "phase4_record", record_id, case_id,
                             after={"domain": domain, "record_type": record_type, "status": payload.get("status", "DRAFT")}, title=f"{domain} {record_type} created")
        result = self.get(case_id, record_id)
        if event_type:
            self._emit(case_id, event_type, actor_id, record_id)
        return result

    def get(self, case_id: str, record_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            result = self._row(self._record_row(connection, case_id, record_id))
            rows = connection.execute("SELECT * FROM phase4_record_items WHERE record_id=? AND case_id=? ORDER BY sequence,created_at", (record_id, case_id)).fetchall()
            result["items"] = [dict(row) | {"data": _from_json(row["data_json"], {}), "source": _from_json(row["source_json"], {}), "amount": _amount(row["amount_paise"])} for row in rows]
            result["links"] = [dict(row) for row in connection.execute("SELECT * FROM phase4_record_links WHERE record_id=? AND case_id=?", (record_id, case_id)).fetchall()]
            return result

    def list(self, case_id: str, domain: str, record_type: Optional[str] = None) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            sql = "SELECT * FROM phase4_records WHERE case_id=? AND domain=? AND archived_at IS NULL"
            args: List[Any] = [case_id, domain]
            if record_type:
                sql += " AND record_type=?"; args.append(record_type)
            rows = connection.execute(sql + " ORDER BY effective_date DESC,created_at DESC", tuple(args)).fetchall()
            return [self._row(row) for row in rows]

    def update(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str,
               event_type: Optional[str] = None) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            before = self._record_row(connection, case_id, record_id)
            if before["status"] in {"FINAL", "ISSUED", "APPROVED", "SUPERSEDED"} and payload.get("allow_final_edit") is not True:
                raise ValueError("Final/issued record is immutable; create a new version instead")
            self._document(connection, case_id, payload.get("document_id"))
            data = _from_json(before["data_json"], {})
            data.update(payload.get("data") or {})
            reserved = {"status", "period_from", "period_to", "effective_date", "amount", "tax_amount", "document_id", "confidentiality_level", "source", "data", "snapshot", "allow_final_edit"}
            data.update({k: v for k, v in payload.items() if k not in reserved})
            level = str(payload.get("confidentiality_level") or before["confidentiality_level"]).upper()
            if level not in CONFIDENTIAL_LEVELS: raise ValueError("Unsupported confidentiality classification")
            now = utc_now()
            connection.execute("""UPDATE phase4_records SET status=?,period_from=?,period_to=?,effective_date=?,amount_paise=?,tax_paise=?,document_id=?,
                confidentiality_level=?,source_json=?,data_json=?,snapshot_json=?,updated_by=?,updated_at=? WHERE id=?""",
                (str(payload.get("status") or before["status"]).upper(), payload.get("period_from", before["period_from"]), payload.get("period_to", before["period_to"]),
                 payload.get("effective_date", before["effective_date"]), _paise(payload.get("amount", _amount(before["amount_paise"]))), _paise(payload.get("tax_amount", _amount(before["tax_paise"]))),
                 payload.get("document_id", before["document_id"]), level, _json(payload.get("source", _from_json(before["source_json"], {}))), _json(data),
                 _json(payload.get("snapshot", _from_json(before["snapshot_json"], {}))), actor_id, now, record_id))
            self.store.audit(connection, actor_id, "PHASE4_RECORD_UPDATED", "phase4_record", record_id, case_id, before={"status": before["status"]}, after={"status": payload.get("status", before["status"])}, title="Phase 4 record updated")
        result = self.get(case_id, record_id)
        if event_type: self._emit(case_id, event_type, actor_id, record_id)
        return result

    def add_item(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            self._record_row(connection, case_id, record_id); self._document(connection, case_id, payload.get("document_id"))
            item_key = str(payload.get("item_key") or "").strip()
            if not item_key: raise ValueError("item_key is required")
            existing = connection.execute("SELECT * FROM phase4_record_items WHERE record_id=? AND item_key=?", (record_id, item_key)).fetchone()
            now = utc_now()
            if existing:
                connection.execute("UPDATE phase4_record_items SET status=?,amount_paise=?,document_id=?,data_json=?,source_json=?,updated_by=?,updated_at=? WHERE id=?",
                    (str(payload.get("status") or existing["status"]).upper(), _paise(payload.get("amount", _amount(existing["amount_paise"]))), payload.get("document_id", existing["document_id"]),
                     _json(payload.get("data") or _from_json(existing["data_json"], {})), _json(payload.get("source") or _from_json(existing["source_json"], {})), actor_id, now, existing["id"]))
                item_id = existing["id"]
            else:
                item_id = new_id(); sequence = int(payload.get("sequence") or 0)
                connection.execute("INSERT INTO phase4_record_items(id,case_id,record_id,item_key,sequence,status,amount_paise,document_id,data_json,source_json,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (item_id, case_id, record_id, item_key, sequence, str(payload.get("status") or "DRAFT").upper(), _paise(payload.get("amount")), payload.get("document_id"), _json(payload.get("data") or {}), _json(payload.get("source") or {}), actor_id, actor_id, now, now))
            self.store.audit(connection, actor_id, "PHASE4_ITEM_RECORDED", "phase4_record_item", item_id, case_id, after={"record_id": record_id, "item_key": item_key}, title="Phase 4 register item recorded")
        return self.get(case_id, record_id)

    def link_document(self, case_id: str, record_id: str, document_id: str, link_type: str, actor_id: str, remarks: str = "") -> Dict[str, Any]:
        with self.store.transaction() as connection:
            self._record_row(connection, case_id, record_id); self._document(connection, case_id, document_id)
            link_id = new_id(); now = utc_now()
            connection.execute("INSERT OR IGNORE INTO phase4_record_links(id,case_id,record_id,document_id,link_type,remarks,created_by,created_at) VALUES (?,?,?,?,?,?,?,?)", (link_id, case_id, record_id, document_id, link_type, remarks, actor_id, now))
            self.store.audit(connection, actor_id, "PHASE4_EVIDENCE_LINKED", "phase4_record", record_id, case_id, after={"document_id": document_id, "link_type": link_type}, title="Phase 4 evidence linked")
        return self.get(case_id, record_id)

    # Operations registers -------------------------------------------------
    def create_going_concern(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        if str(payload.get("operational_status") or "UNKNOWN").upper() not in {"OPERATING", "PARTIALLY_OPERATING", "NOT_OPERATING", "SUSPENDED", "UNKNOWN"}: raise ValueError("Unsupported operational status")
        data = dict(payload); income, expenditure = _paise(data.pop("estimated_income", 0)), _paise(data.pop("estimated_expenditure", 0))
        data.update({"estimated_income_paise": income, "estimated_expenditure_paise": expenditure, "estimated_net_cash_flow_paise": income - expenditure, "working_capital_required_paise": _paise(data.pop("working_capital_required", 0))})
        return self.create(case_id, "operations", "GOING_CONCERN_ASSESSMENT", {"effective_date": payload.get("assessment_date"), "status": payload.get("status", "DRAFT"), "data": data, "document_id": payload.get("document_id")}, actor_id, "GOING_CONCERN_ASSESSMENT_CREATED")

    def approve_going_concern(self, case_id: str, record_id: str, actor_id: str) -> Dict[str, Any]:
        result = self.update(case_id, record_id, {"status": "APPROVED", "data": {"approved_by": actor_id, "approved_at": utc_now()}}, actor_id, "GOING_CONCERN_ASSESSMENT_APPROVED")
        return result

    def create_cash_flow(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        data = dict(payload); values = {k: _paise(data.pop(k, 0)) for k in ("opening_cash", "opening_bank", "expected_receipts", "actual_receipts", "expected_payments", "actual_payments", "closing_cash", "closing_bank")}
        expected = values["opening_cash"] + values["opening_bank"] + values["expected_receipts"] - values["expected_payments"]
        actual = values["opening_cash"] + values["opening_bank"] + values["actual_receipts"] - values["actual_payments"]
        values.update({"expected_net_cash_paise": expected, "actual_net_cash_paise": actual, "variance_paise": actual - expected, "cash_shortfall_or_surplus_paise": actual})
        return self.create(case_id, "operations", "CASH_FLOW_PERIOD", {"period_from": payload.get("period_from"), "period_to": payload.get("period_to"), "status": payload.get("status", "DRAFT"), "data": data | values, "idempotency_key": payload.get("idempotency_key")}, actor_id, "CASH_FLOW_PERIOD_CREATED")

    def cash_flow_transaction(self, case_id: str, period_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        if str(payload.get("kind") or "").upper() not in {"EXPECTED_RECEIPT", "ACTUAL_RECEIPT", "EXPECTED_PAYMENT", "ACTUAL_PAYMENT"}: raise ValueError("Unsupported cash-flow transaction kind")
        return self.add_item(case_id, period_id, {"item_key": str(payload.get("idempotency_key") or new_id()), "status": payload.get("status", "RECORDED"), "amount": payload.get("amount"), "data": payload}, actor_id)

    def finalize_cash_flow(self, case_id: str, period_id: str, actor_id: str) -> Dict[str, Any]:
        return self.update(case_id, period_id, {"status": "FINAL"}, actor_id, "CASH_FLOW_PERIOD_FINALIZED")

    def record_bank_reconciliation(self, case_id: str, cash_flow_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.create(case_id, "operations", "BANK_RECONCILIATION", {"parent_record_id": cash_flow_id, "effective_date": payload.get("period_to"), "status": payload.get("status", "DRAFT"), "document_id": payload.get("statement_document"), "data": {**payload, "book_balance_paise": _paise(payload.get("book_balance")), "bank_statement_balance_paise": _paise(payload.get("bank_statement_balance")), "difference_paise": _paise(payload.get("book_balance")) - _paise(payload.get("bank_statement_balance"))}}, actor_id)

    def create_receivable(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        contact = payload.get("debtor_contact_id")
        with self.store.connect() as connection:
            if contact and not connection.execute("SELECT 1 FROM contacts WHERE id=? AND (case_id=? OR case_id IS NULL)", (contact, case_id)).fetchone(): raise ValueError("Debtor contact is unavailable for this case")
        opening = _paise(payload.get("opening_amount")); data = dict(payload) | {"opening_amount_paise": opening, "current_outstanding_paise": opening, "amount_collected_paise": 0}
        return self.create(case_id, "operations", "RECEIVABLE", {"record_key": str(contact or payload.get("invoice_reference") or ""), "status": payload.get("status", "OUTSTANDING"), "amount": _amount(opening), "data": data, "idempotency_key": payload.get("idempotency_key")}, actor_id, "RECEIVABLE_CREATED")

    def receivable_activity(self, case_id: str, receivable_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        kind = str(payload.get("activity_type") or "FOLLOW_UP").upper(); event = "RECEIVABLE_COLLECTION_RECEIVED" if kind == "COLLECTION" else "RECEIVABLE_FOLLOWUP_RECORDED"
        updated = self.add_item(case_id, receivable_id, {"item_key": str(payload.get("idempotency_key") or new_id()), "status": kind, "amount": payload.get("amount"), "document_id": payload.get("document_id"), "data": payload}, actor_id)
        if kind == "COLLECTION":
            collected = sum(_paise(item["amount"]) for item in updated["items"] if item["status"] == "COLLECTION")
            opening = int(updated["data"].get("opening_amount_paise", updated["amount_paise"])); self.update(case_id, receivable_id, {"amount": _amount(max(0, opening-collected)), "data": {"amount_collected_paise": collected, "current_outstanding_paise": max(0, opening-collected), "status": "COLLECTED" if collected >= opening else "PARTLY_COLLECTED"}}, actor_id)
        self._emit(case_id, event, actor_id, receivable_id); return self.get(case_id, receivable_id)

    def asset_movement(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        movement = str(payload.get("movement_type") or "").upper()
        if movement not in ASSET_MOVEMENTS: raise ValueError("Unsupported asset movement type")
        with self.store.connect() as connection:
            if not connection.execute("SELECT 1 FROM assets WHERE id=? AND case_id=? AND archived_at IS NULL", (payload.get("asset_id"), case_id)).fetchone(): raise ValueError("Asset does not belong to this case")
        return self.create(case_id, "operations", "ASSET_MOVEMENT", {"record_key": str(payload["asset_id"]), "effective_date": payload.get("movement_date"), "status": payload.get("status", "RECORDED"), "document_id": payload.get("supporting_document_id"), "data": payload}, actor_id, "ASSET_MOVEMENT_RECORDED")

    def create_compliance(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.create(case_id, "operations", "STATUTORY_COMPLIANCE", {"record_key": str(payload.get("compliance_type") or ""), "effective_date": payload.get("due_date"), "status": payload.get("status", "REVIEW_REQUIRED"), "document_id": payload.get("acknowledgement_document_id"), "amount": payload.get("amount_due"), "data": payload, "idempotency_key": payload.get("idempotency_key")}, actor_id, "STATUTORY_COMPLIANCE_DUE")

    def file_compliance(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.update(case_id, record_id, {"status": "FILED", "document_id": payload.get("acknowledgement_document_id"), "data": payload}, actor_id, "STATUTORY_COMPLIANCE_FILED")

    def create_moratorium_review(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.create(case_id, "operations", "MORATORIUM_REVIEW", {"record_key": str(payload.get("application_id") or payload.get("case_reference") or ""), "status": payload.get("moratorium_review_status", "NOT_REVIEWED"), "document_id": payload.get("source_document_id"), "data": payload}, actor_id, "MORATORIUM_REVIEW_REQUIRED")

    def asset_protection_incident(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        """Record a potential protection issue; no disposal or legal conclusion is inferred."""
        return self.create(case_id, "operations", "ASSET_PROTECTION_INCIDENT", {
            "record_key": str(payload.get("asset_id") or payload.get("reference") or ""),
            "effective_date": payload.get("incident_date"), "status": payload.get("status", "OPEN"),
            "document_id": payload.get("evidence_document_id"), "data": payload,
            "idempotency_key": payload.get("idempotency_key"),
        }, actor_id, "ASSET_PROTECTION_INCIDENT_RECORDED")

    # Management / Section 19 ------------------------------------------------
    def create_requisition(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.create(case_id, "cooperation", "MANAGEMENT_REQUISITION", {"record_key": str(payload.get("reference") or ""), "effective_date": payload.get("requested_date"), "status": payload.get("status", "SENT"), "document_id": payload.get("request_document_id"), "data": payload, "idempotency_key": payload.get("idempotency_key")}, actor_id, "MANAGEMENT_REQUISITION_SENT")

    def requisition_item(self, case_id: str, requisition_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        result = self.add_item(case_id, requisition_id, {"item_key": str(payload.get("item_key") or payload.get("description") or new_id()), "status": payload.get("status", "REQUESTED"), "document_id": payload.get("document_id"), "data": payload}, actor_id)
        status = str(payload.get("status") or "REQUESTED").upper()
        event = "MANAGEMENT_RESPONSE_RECEIVED" if status in {"RECEIVED", "PARTLY_RECEIVED"} else "MANAGEMENT_DEFICIENCY_IDENTIFIED" if status in {"NO_RESPONSE", "DISPUTED", "REVIEW_REQUIRED"} else None
        if event: self._emit(case_id, event, actor_id, requisition_id)
        return result

    def requisition_action(self, case_id: str, requisition_id: str, action: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        action = action.upper(); events = {"REMINDER": "MANAGEMENT_REMINDER_SENT", "FINAL_DEMAND": "MANAGEMENT_FINAL_DEMAND_SENT"}
        if action not in events: raise ValueError("Unsupported requisition action")
        result = self.add_item(case_id, requisition_id, {"item_key": f"{action}:{payload.get('idempotency_key') or new_id()}", "status": action, "document_id": payload.get("document_id"), "data": payload}, actor_id)
        self._emit(case_id, events[action], actor_id, requisition_id); return result

    def cooperation_summary(self, case_id: str) -> Dict[str, int]:
        items = [item for req in self.list(case_id, "cooperation", "MANAGEMENT_REQUISITION") for item in self.get(case_id, req["id"])["items"]]
        statuses = [item["status"] for item in items]
        return {"total_requested_items": len([s for s in statuses if not s.startswith(("REMINDER", "FINAL_DEMAND"))]), "received": statuses.count("RECEIVED"), "partly_received": statuses.count("PARTLY_RECEIVED"), "no_response": statuses.count("NO_RESPONSE"), "outstanding": sum(s in {"REQUESTED", "PARTLY_RECEIVED", "NO_RESPONSE", "REVIEW_REQUIRED", "DISPUTED"} for s in statuses), "overdue": 0, "critical_outstanding": statuses.count("NO_RESPONSE")}

    def create_section19(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        if payload.get("professional_confirmed") is not True: raise ValueError("Professional confirmation is required before Section 19 escalation")
        requests = self.list(case_id, "cooperation", "MANAGEMENT_REQUISITION")
        chronology = [{"requisition_id": r["id"], "status": r["status"], "date": r.get("effective_date")} for r in requests]
        result = self.create(case_id, "section19", "SECTION_19_APPLICATION", {"status": "DRAFT", "data": payload | {"chronology": chronology, "no_ai_generated_facts": True}, "snapshot": {"requisitions": chronology}}, actor_id, "NON_COOPERATION_REVIEW_REQUIRED")
        return result

    def finalize_section19(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        if payload.get("professional_confirmed") is not True: raise ValueError("Professional approval is required to finalize a Section 19 draft")
        return self.update(case_id, record_id, {"status": "FINAL", "document_id": payload.get("document_id"), "data": payload}, actor_id, "SECTION_19_APPLICATION_APPROVED")

    def section19_annexure(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        return self.add_item(case_id, record_id, {"item_key": str(payload.get("item_key") or new_id()), "status": payload.get("status", "REVIEW_REQUIRED"), "document_id": payload.get("document_id"), "data": payload}, actor_id)

    def file_section19(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        if payload.get("professional_confirmed") is not True:
            raise ValueError("Professional confirmation is required to record a Section 19 filing")
        return self.update(case_id, record_id, {"status": "FILED", "document_id": payload.get("filed_document_id"), "data": payload}, actor_id, "SECTION_19_APPLICATION_FILED")

    # Finance / approval ------------------------------------------------------
    def interim_finance(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        data = dict(payload) | {"amount_requested_paise": _paise(payload.get("amount_requested")), "drawdown_amount_paise": _paise(payload.get("drawdown_amount"))}
        return self.create(case_id, "finance", "INTERIM_FINANCE", {"status": payload.get("status", "DRAFT"), "amount": payload.get("amount_requested"), "document_id": payload.get("agreement_document_id"), "data": data}, actor_id, "INTERIM_FINANCE_PROPOSED")

    def section28_review(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        suggestion = str(payload.get("system_suggestion") or "REVIEW_REQUIRED").upper()
        if suggestion not in {"APPROVAL_REQUIRED", "APPROVAL_NOT_REQUIRED", "REVIEW_REQUIRED"}: raise ValueError("Unsupported Section 28 suggestion")
        return self.create(case_id, "finance", "SECTION_28_REVIEW", {"status": payload.get("status", "DRAFT"), "amount": payload.get("amount"), "document_id": payload.get("supporting_document_id"), "data": payload | {"system_suggestion": suggestion}}, actor_id, "SECTION_28_REVIEW_CREATED")

    def decide_section28(self, case_id: str, record_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        decision = str(payload.get("professional_decision") or "").upper()
        if decision not in {"APPROVAL_REQUIRED", "APPROVAL_NOT_REQUIRED", "REVIEW_REQUIRED"}:
            raise ValueError("A professional Section 28 decision is required")
        return self.update(case_id, record_id, {"status": decision, "data": payload | {"decided_by": actor_id, "decided_at": utc_now()}}, actor_id, "SECTION_28_APPROVAL_REQUIRED" if decision == "APPROVAL_REQUIRED" else None)

    # Valuation ---------------------------------------------------------------
    def valuation_record(self, case_id: str, record_type: str, payload: Dict[str, Any], actor_id: str, event: Optional[str] = None) -> Dict[str, Any]:
        return self.create(case_id, "valuation", record_type, {"record_key": str(payload.get("asset_class") or payload.get("contact_id") or ""), "parent_record_id": payload.get("parent_record_id"), "status": payload.get("status", "DRAFT"), "effective_date": payload.get("date") or payload.get("appointment_date") or payload.get("received_date"), "document_id": payload.get("document_id"), "confidentiality_level": payload.get("confidentiality_level", "CONFIDENTIAL_CIRP"), "amount": payload.get("amount") or payload.get("professional_fee"), "tax_amount": payload.get("tax_amount") or payload.get("gst"), "data": payload, "idempotency_key": payload.get("idempotency_key")}, actor_id, event)

    def verify_valuer_declaration(self, case_id: str, declaration_id: str, actor_id: str, status: str = "VERIFIED") -> Dict[str, Any]:
        if status not in {"VERIFIED", "REJECTED"}: raise ValueError("Declaration must be verified or rejected")
        return self.update(case_id, declaration_id, {"status": status, "data": {"reviewed_by": actor_id, "reviewed_at": utc_now()}}, actor_id, "VALUER_DECLARATION_RECEIVED")

    def activate_assignment(self, case_id: str, assignment_id: str, actor_id: str) -> Dict[str, Any]:
        assignment = self.get(case_id, assignment_id); data = assignment["data"]
        if not data.get("appointment_id") or not data.get("declaration_id"):
            raise ValueError("Appointment acceptance and verified declaration are required before activation")
        appointment = self.get(case_id, str(data["appointment_id"]))
        declaration = self.get(case_id, str(data["declaration_id"]))
        if appointment["record_type"] != "APPOINTMENT" or appointment["status"] not in {"ISSUED", "ACKNOWLEDGED"} or declaration["record_type"] != "DECLARATION" or declaration["status"] != "VERIFIED":
            raise ValueError("An issued appointment and verified declaration are required before activation")
        return self.update(case_id, assignment_id, {"status": "ACTIVE"}, actor_id, "VALUATION_DATA_PACK_READY")

    # IM / confidentiality / VDR --------------------------------------------
    IM_SECTIONS = ("CASE_DETAILS", "CONFIDENTIALITY", "CORPORATE_OVERVIEW", "CORPORATE_DETAILS", "KMP", "LOCATIONS", "OWNERSHIP", "CIRP_CHRONOLOGY", "ASSETS_LIABILITIES", "LAND_BUILDING", "PLANT_MACHINERY", "FINANCIAL_STATEMENTS", "CLAIMS_CREDITORS", "RELATED_PARTIES", "GUARANTEES", "LITIGATION", "EMPLOYEES", "GOING_CONCERN", "BUSINESS_PERFORMANCE", "CONTRACTS", "STATUTORY", "VALUATION", "ANNEXURE_INDEX")

    def initialize_im(self, case_id: str, actor_id: str) -> Dict[str, Any]:
        workspace = self.create(case_id, "im", "IM_WORKSPACE", {"record_key": "CURRENT", "status": "DRAFT", "data": {"template_status": "IM_DOCX_TEMPLATE_CONVERSION_REQUIRED"}, "idempotency_key": "im-workspace-current"}, actor_id, "IM_DATA_COLLECTION_STARTED")
        with self.store.connect() as connection:
            case = connection.execute("SELECT * FROM cases WHERE id=?", (case_id,)).fetchone(); claims = connection.execute("SELECT COUNT(*) FROM claims WHERE case_id=? AND archived_at IS NULL", (case_id,)).fetchone()[0]
        for seq, section in enumerate(self.IM_SECTIONS, 1):
            source = {"case_id": case_id, "available": section in {"CASE_DETAILS", "CIRP_CHRONOLOGY"} or (section == "CLAIMS_CREDITORS" and claims > 0)}
            self.add_item(case_id, workspace["id"], {"item_key": section, "sequence": seq, "status": "CONFIRMED" if source["available"] else "REVIEW_REQUIRED", "source": source, "data": {"section": section, "owner": "RP/Staff"}}, actor_id)
        return self.get(case_id, workspace["id"])

    def im_version(self, case_id: str, workspace_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        workspace = self.get(case_id, workspace_id); version = 1 + len(self.list(case_id, "im", "IM_VERSION"))
        snapshot = {"workspace_id": workspace_id, "checklist": workspace["items"], "sources": workspace["links"]}
        return self.create(case_id, "im", "IM_VERSION", {"parent_record_id": workspace_id, "status": payload.get("status", "DRAFT"), "version_number": version, "document_id": payload.get("document_id"), "confidentiality_level": "CONFIDENTIAL_CIRP", "data": payload | {"version_label": payload.get("version_label", f"IM DRAFT V{version}"), "template_status": "IM_DOCX_TEMPLATE_CONVERSION_REQUIRED"}, "snapshot": snapshot}, actor_id, "IM_FINALIZED" if payload.get("status") == "FINAL" else None)

    def undertaking(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        recipient_type = str(payload.get("recipient_type") or "").upper()
        if recipient_type not in UNDERTAKING_TYPES: raise ValueError("Unsupported confidentiality recipient type")
        return self.create(case_id, "confidentiality", "UNDERTAKING", {"record_key": str(payload.get("recipient_email") or payload.get("recipient_name") or ""), "status": payload.get("status", "REQUIRED"), "effective_date": payload.get("issued_date"), "document_id": payload.get("signed_document_id"), "confidentiality_level": "CONFIDENTIAL_CIRP", "data": payload | {"recipient_type": recipient_type}}, actor_id, "CONFIDENTIALITY_UNDERTAKING_ISSUED")

    def verify_undertaking(self, case_id: str, undertaking_id: str, actor_id: str, status: str = "VERIFIED") -> Dict[str, Any]:
        if status not in {"VERIFIED", "REJECTED"}: raise ValueError("Undertaking must be verified or rejected")
        return self.update(case_id, undertaking_id, {"status": status, "data": {"verified_by": actor_id, "verified_at": utc_now()}}, actor_id, "CONFIDENTIALITY_UNDERTAKING_VERIFIED")

    def grant_vdr_access(self, case_id: str, workspace_id: str, recipient_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            self._record_row(connection, case_id, workspace_id); recipient = self._record_row(connection, case_id, recipient_id)
            undertaking_id = payload.get("undertaking_id"); override = str(payload.get("override_reason") or "").strip()
            if undertaking_id:
                undertaking = self._record_row(connection, case_id, undertaking_id)
                verified = undertaking["record_type"] == "UNDERTAKING" and undertaking["status"] == "VERIFIED"
            else: verified = False
            if not verified and not override: raise ValueError("A verified confidentiality undertaking is required before VDR access")
            now, grant_id = utc_now(), new_id()
            connection.execute("INSERT INTO vdr_access_grants(id,case_id,workspace_record_id,recipient_record_id,undertaking_record_id,folder_scope_json,status,override_reason,valid_from,valid_to,granted_by,granted_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(workspace_record_id,recipient_record_id) DO UPDATE SET undertaking_record_id=excluded.undertaking_record_id,folder_scope_json=excluded.folder_scope_json,status='GRANTED',override_reason=excluded.override_reason,valid_from=excluded.valid_from,valid_to=excluded.valid_to,granted_by=excluded.granted_by,granted_at=excluded.granted_at,revoked_by=NULL,revoked_at=NULL", (grant_id, case_id, workspace_id, recipient_id, undertaking_id, _json(payload.get("folder_scope") or []), "GRANTED", override, payload.get("valid_from"), payload.get("valid_to"), actor_id, now))
            connection.execute("INSERT INTO vdr_access_logs(id,case_id,workspace_record_id,recipient_record_id,action,metadata_json,actor_id,occurred_at) VALUES (?,?,?,?,?,?,?,?)", (new_id(), case_id, workspace_id, recipient_id, "ACCESS_GRANTED", _json({"override_reason": override, "recipient": recipient["id"]}), actor_id, now))
            self.store.audit(connection, actor_id, "VDR_ACCESS_GRANTED", "vdr_access_grant", grant_id, case_id, after={"recipient_id": recipient_id}, title="VDR access granted")
        self._emit(case_id, "VDR_ACCESS_GRANTED", actor_id, f"{workspace_id}:{recipient_id}")
        return {"workspace_id": workspace_id, "recipient_id": recipient_id, "status": "GRANTED"}

    def can_access_vdr(self, case_id: str, workspace_id: str, recipient_id: str, document_id: Optional[str] = None) -> bool:
        with self.store.connect() as connection:
            grant = connection.execute("SELECT * FROM vdr_access_grants WHERE case_id=? AND workspace_record_id=? AND recipient_record_id=? AND status='GRANTED' AND revoked_at IS NULL", (case_id, workspace_id, recipient_id)).fetchone()
            if not grant: return False
            if document_id:
                doc = connection.execute("SELECT confidentiality_classification FROM documents WHERE id=? AND case_id=?", (document_id, case_id)).fetchone()
                if not doc: return False
            return True

    def log_vdr_access(self, case_id: str, workspace_id: str, recipient_id: str, action: str, actor_id: str, document_id: Optional[str] = None) -> None:
        if not self.can_access_vdr(case_id, workspace_id, recipient_id, document_id): raise PermissionError("VDR access is not authorised")
        with self.store.transaction() as connection:
            connection.execute("INSERT INTO vdr_access_logs(id,case_id,workspace_record_id,recipient_record_id,document_id,action,metadata_json,actor_id,occurred_at) VALUES (?,?,?,?,?,?,?,?,?)", (new_id(), case_id, workspace_id, recipient_id, document_id, action, "{}", actor_id, utc_now()))

    def revoke_vdr_access(self, case_id: str, workspace_id: str, recipient_id: str, actor_id: str, reason: str) -> None:
        if not str(reason or "").strip():
            raise ValueError("A reason is required to revoke VDR access")
        with self.store.transaction() as connection:
            now = utc_now()
            cursor = connection.execute("UPDATE vdr_access_grants SET status='REVOKED',revoked_by=?,revoked_at=? WHERE case_id=? AND workspace_record_id=? AND recipient_record_id=? AND status='GRANTED'", (actor_id, now, case_id, workspace_id, recipient_id))
            if not cursor.rowcount: raise KeyError("Active VDR grant not found")
            connection.execute("INSERT INTO vdr_access_logs(id,case_id,workspace_record_id,recipient_record_id,action,metadata_json,actor_id,occurred_at) VALUES (?,?,?,?,?,?,?,?)", (new_id(), case_id, workspace_id, recipient_id, "ACCESS_REVOKED", _json({"reason": reason}), actor_id, now))
        self._emit(case_id, "VDR_ACCESS_REVOKED", actor_id, f"{workspace_id}:{recipient_id}")

    def publish_vdr_document(self, case_id: str, workspace_id: str, document_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        level = str(payload.get("confidentiality_level") or "CONFIDENTIAL_CIRP").upper()
        if level not in CONFIDENTIAL_LEVELS - {"NORMAL"}:
            raise ValueError("VDR documents must use a restricted confidentiality classification")
        with self.store.transaction() as connection:
            self._record_row(connection, case_id, workspace_id); self._document(connection, case_id, document_id)
            connection.execute("UPDATE documents SET confidentiality_classification=? WHERE id=? AND case_id=?", (level, document_id, case_id))
        return self.create(case_id, "vdr", "DOCUMENT_LINK", {"parent_record_id": workspace_id, "record_key": document_id, "status": "PUBLISHED", "document_id": document_id, "confidentiality_level": level, "data": payload}, actor_id, "VDR_DOCUMENT_PUBLISHED")

    def cost_allocation_snapshot(self, case_id: str, cost_statement_id: str, actor_id: str, allocation_date: Optional[str] = None) -> Dict[str, Any]:
        with self.store.transaction() as connection:
            statement = connection.execute("SELECT * FROM coc_cost_statements WHERE id=? AND case_id=?", (cost_statement_id, case_id)).fetchone()
            if not statement: raise KeyError("CIRP cost statement not found")
            existing = connection.execute("SELECT * FROM coc_cost_allocation_snapshots WHERE cost_statement_id=?", (cost_statement_id,)).fetchone()
            if existing: return dict(existing) | {"idempotent_replay": True}
            meeting = connection.execute("SELECT * FROM coc_meetings WHERE id=? AND case_id=?", (statement["meeting_id"], case_id)).fetchone()
            constitution_id = meeting["coc_constitution_id"] if meeting else None
            members = connection.execute("SELECT * FROM coc_constitution_members WHERE case_id=? AND constitution_id=? ORDER BY creditor_name", (case_id, constitution_id)).fetchall()
            if not members: raise ValueError("Confirmed CoC Constitution members are required for cost allocation")
            total = int(statement["total_paise"]); shares = [(row, int(Decimal(str(row["display_voting_percentage"])) * 10000)) for row in members]
            rows, allocated = [], 0
            for row, units in shares:
                amount = (total * units) // 1_000_000; allocated += amount; rows.append((row, units, amount))
            for row, units, amount in sorted(rows, key=lambda x: (-x[1], x[0]["creditor_name"]))[:total-allocated]:
                i = next(i for i, item in enumerate(rows) if item[0]["id"] == row["id"]); rows[i] = (row, units, amount + 1)
            snapshot_id, now = new_id(), utc_now(); snapshot = {"constitution_id": constitution_id, "total_paise": total, "members": [{"creditor_name": r[0]["creditor_name"], "share_units": r[1], "allocated_paise": r[2]} for r in rows]}
            connection.execute("INSERT INTO coc_cost_allocation_snapshots(id,case_id,cost_statement_id,coc_constitution_id,meeting_id,allocation_date,total_paise,snapshot_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (snapshot_id, case_id, cost_statement_id, constitution_id, statement["meeting_id"], allocation_date or date.today().isoformat(), total, _json(snapshot), actor_id, now))
            for row, units, amount in rows: connection.execute("INSERT INTO coc_cost_allocation_rows(id,allocation_snapshot_id,case_id,creditor_name,claim_id,voting_share_units,allocated_paise,created_at) VALUES (?,?,?,?,?,?,?,?)", (new_id(), snapshot_id, case_id, row["creditor_name"], row["claim_id"], units, amount, now))
            self.store.audit(connection, actor_id, "CIRP_COST_ALLOCATION_SNAPSHOTTED", "coc_cost_allocation_snapshot", snapshot_id, case_id, after=snapshot, title="CIRP cost allocation snapshot created")
        return {"id": snapshot_id, **snapshot}

    def workflow_summary(self, case_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            records = connection.execute("SELECT domain,record_type,status,amount_paise,data_json FROM phase4_records WHERE case_id=? AND archived_at IS NULL", (case_id,)).fetchall()
        by = {(row["domain"], row["record_type"]): dict(row) for row in records}
        receivables = [row for row in records if row["record_type"] == "RECEIVABLE"]
        outstanding = sum(int(_from_json(r["data_json"], {}).get("current_outstanding_paise", r["amount_paise"])) for r in receivables)
        im = [r for r in records if r["record_type"] == "IM_WORKSPACE"]
        with self.store.connect() as connection:
            vdr_count = connection.execute("SELECT COUNT(*) FROM vdr_access_grants WHERE case_id=? AND status='GRANTED' AND revoked_at IS NULL", (case_id,)).fetchone()[0]
        return {"going_concern_status": by.get(("operations", "GOING_CONCERN_ASSESSMENT"), {}).get("status", "NOT_RECORDED"), "current_cash_flow_period": by.get(("operations", "CASH_FLOW_PERIOD"), {}).get("status", "NOT_RECORDED"), "outstanding_receivables_amount": _amount(outstanding), "critical_receivables_count": sum(r["status"] in {"LEGAL_ACTION", "UNTRACEABLE", "WRITTEN_OFF_REVIEW"} for r in receivables), "open_asset_protection_incidents": sum(r["record_type"] == "ASSET_PROTECTION_INCIDENT" and r["status"] not in {"CLOSED", "RESOLVED"} for r in records), "statutory_compliances_overdue": sum(r["record_type"] == "STATUTORY_COMPLIANCE" and r["status"] == "OVERDUE" for r in records), "management_items_outstanding": self.cooperation_summary(case_id)["outstanding"], "valuation_requirement_status": by.get(("valuation", "REQUIREMENT"), {}).get("status", "NOT_RECORDED"), "valuers_appointed_count": sum(r["record_type"] == "APPOINTMENT" and r["status"] in {"ISSUED", "ACKNOWLEDGED"} for r in records), "valuation_reports_received": sum(r["record_type"] == "REPORT" for r in records), "im_current_version": max((r["status"] for r in records if r["record_type"] == "IM_VERSION"), default="NOT_RECORDED"), "confidentiality_pending_count": sum(r["record_type"] == "UNDERTAKING" and r["status"] != "VERIFIED" for r in records), "vdr_authorized_recipients": vdr_count}
