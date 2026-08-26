"""Case-scoped, deterministic Claims Phase 1 workflow.

The existing ``claims`` table remains canonical.  This service adds validation,
history, query, checklist, document-link and List of Creditors operations without
introducing a parallel AI or claim model.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional
import json
import re

from docx import Document

from database import CasefileDatabase, MODULE_FIELDS, new_id, utc_now


CLAIM_STATUSES = {
    "RECEIVED", "UNDER_VERIFICATION", "INFORMATION_REQUIRED", "RESPONSE_RECEIVED",
    "ADMITTED", "PARTLY_ADMITTED", "REJECTED", "WITHDRAWN",
}
DECISION_STATUSES = {"ADMITTED", "PARTLY_ADMITTED", "REJECTED"}
CREDITOR_CATEGORIES = {
    "FINANCIAL_CREDITOR", "OPERATIONAL_CREDITOR", "WORKMAN_EMPLOYEE",
    "GOVERNMENT_AUTHORITY", "OTHER_CREDITOR",
}
FORM_TYPES = {"Form B", "Form C", "Form CA", "Form D", "Form E", "Form F", "Other"}
RECEIVED_VIA = {"Email", "Physical", "Portal", "Other"}
RELATED_PARTY = {"YES", "NO", "UNKNOWN"}
SECURED_STATUSES = {"SECURED", "UNSECURED", "PARTLY_SECURED", "NOT_APPLICABLE", "UNKNOWN"}
QUERY_STATUSES = {"DRAFT", "SENT", "PARTLY_RESPONDED", "RESPONDED", "CLOSED"}
CHECKLIST_STATUSES = {"RECEIVED", "MISSING", "NOT_APPLICABLE"}

QUERY_TEMPLATES = [
    {"key": "interest-calculation", "subject": "Interest calculation required", "text": "Please provide the detailed interest calculation, including rate, period and basis."},
    {"key": "ledger", "subject": "Ledger required", "text": "Please provide the complete ledger account supporting the claim."},
    {"key": "invoices", "subject": "Supporting invoices required", "text": "Please provide copies of the invoices relied upon for the claim."},
    {"key": "agreement", "subject": "Agreement required", "text": "Please provide the agreement or contract supporting the debt."},
    {"key": "bank-statement", "subject": "Bank statement required", "text": "Please provide the relevant bank statement entries supporting payment or disbursement."},
    {"key": "security", "subject": "Security or charge documents required", "text": "Please provide documents evidencing the security interest or registered charge."},
    {"key": "authorization", "subject": "Authorization required", "text": "Please provide valid authorization for the person signing or submitting the claim."},
    {"key": "incomplete-form", "subject": "Claim form incomplete", "text": "Please provide a complete and duly signed claim form with all applicable particulars."},
    {"key": "amount-clarification", "subject": "Clarification of amount required", "text": "Please clarify the components and calculation of the total amount claimed."},
]

DEFAULT_CHECKLIST = [
    "Claim Form", "Invoices", "Ledger", "Agreement", "Bank Statement",
    "Interest Calculation", "Security Documents", "Other Supporting Documents",
]

EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _money(value: Any, *, optional: bool = False) -> Optional[float]:
    if value in (None, ""):
        return None if optional else 0.0
    try:
        amount = Decimal(str(value)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise ValueError("Amounts must be valid numbers") from exc
    if amount < 0:
        raise ValueError("Negative claim amounts are not supported")
    return float(amount)


def _date(value: Any, label: str, *, required: bool = False) -> Optional[str]:
    if value in (None, ""):
        if required:
            raise ValueError(f"{label} is required")
        return None
    try:
        return date.fromisoformat(str(value)[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid date") from exc


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


class ClaimsWorkflow:
    def __init__(self, store: CasefileDatabase, data_dir: Path):
        self.store = store
        self.data_dir = Path(data_dir)

    @staticmethod
    def intake_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
        clean = dict(payload)
        required = {
            "received_date": "Date claim received", "creditor_name": "Creditor name",
            "creditor_category": "Creditor category", "form_type": "Claim form/type",
            "claimed_amount": "Total amount claimed", "email": "Email/contact",
        }
        for key, label in required.items():
            if clean.get(key) in (None, ""):
                raise ValueError(f"{label} is required")
        clean["received_date"] = _date(clean["received_date"], "Date claim received", required=True)
        if clean["creditor_category"] not in CREDITOR_CATEGORIES:
            raise ValueError("Unsupported creditor category")
        if clean["form_type"] not in FORM_TYPES:
            raise ValueError("Unsupported claim form/type")
        clean["received_via"] = clean.get("received_via") or "Email"
        if clean["received_via"] not in RECEIVED_VIA:
            raise ValueError("Unsupported receipt method")
        clean["related_party_status"] = clean.get("related_party_status") or "UNKNOWN"
        if clean["related_party_status"] not in RELATED_PARTY:
            raise ValueError("Unsupported related-party status")
        clean["secured_status"] = clean.get("secured_status") or "UNKNOWN"
        if clean["secured_status"] not in SECURED_STATUSES:
            raise ValueError("Unsupported security status")
        email = str(clean.get("email") or "").strip()
        if "@" in email and not EMAIL_RE.match(email):
            raise ValueError("Email address is invalid")
        clean["email"] = email
        clean["sender_email"] = str(clean.get("sender_email") or email).strip()
        if clean["sender_email"] and not EMAIL_RE.match(clean["sender_email"]):
            raise ValueError("Sender email address is invalid")
        for key in ("principal_claimed", "interest_claimed", "other_amount_claimed"):
            clean[key] = _money(clean.get(key), optional=True)
        clean["claimed_amount"] = _money(clean["claimed_amount"])
        components = [clean[key] for key in ("principal_claimed", "interest_claimed", "other_amount_claimed")]
        clean["calculated_component_total"] = round(sum(value or 0 for value in components), 2) if any(value is not None for value in components) else None
        clean["currency"] = str(clean.get("currency") or "INR").upper()
        clean["status"] = "RECEIVED"
        clean["admitted_amount"] = 0.0
        clean["amount_not_admitted"] = clean["claimed_amount"]
        for key in ("security_value", "interest_rate"):
            clean[key] = _money(clean.get(key), optional=True)
        for key in ("date_debt_incurred", "due_date", "default_date"):
            clean[key] = _date(clean.get(key), key.replace("_", " "))
        return clean

    def _next_number(self, connection, case_id: str) -> str:
        count = connection.execute("SELECT COUNT(*) FROM claims WHERE case_id=?", (case_id,)).fetchone()[0]
        return f"CLM-{count + 1:04d}"

    def _contact_id(self, connection, case_id: str, payload: Dict[str, Any], actor_id: str) -> str:
        email, name = payload.get("email", ""), payload["creditor_name"].strip()
        row = connection.execute(
            """SELECT c.id FROM contacts c JOIN case_contacts cc ON cc.contact_id=c.id
            WHERE cc.case_id=? AND cc.archived_at IS NULL AND c.archived_at IS NULL
            AND ((? <> '' AND lower(c.email)=lower(?)) OR lower(c.name)=lower(?)) LIMIT 1""",
            (case_id, email, email, name),
        ).fetchone()
        if row:
            return row["id"]
        now, contact_id = utc_now(), new_id()
        connection.execute(
            """INSERT INTO contacts(id,kind,name,organization,email,phone,address,identifiers_json,notes,created_by,updated_by,created_at,updated_at)
            VALUES (?, 'organization', ?, ?, ?, ?, ?, '{}', '', ?, ?, ?, ?)""",
            (contact_id, name, name, email, payload.get("phone", ""), payload.get("address", ""), actor_id, actor_id, now, now),
        )
        connection.execute(
            """INSERT INTO case_contacts(id,case_id,contact_id,role,category,communication_preference,notes,created_by,updated_by,created_at,updated_at)
            VALUES (?, ?, ?, 'Creditor', ?, 'Email', '', ?, ?, ?, ?)""",
            (new_id(), case_id, contact_id, payload["creditor_category"], actor_id, actor_id, now, now),
        )
        return contact_id

    def create(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        clean = self.intake_payload(payload)
        with self.store.transaction() as connection:
            self.store.ensure_case(connection, case_id)
            key = str(clean.get("idempotency_key") or "").strip()
            if key:
                duplicate = connection.execute("SELECT id FROM claims WHERE case_id=? AND idempotency_key=?", (case_id, key)).fetchone()
                if duplicate:
                    result = self.get(case_id, duplicate["id"])
                    result["duplicate_submission"] = True
                    return result
            contact_id = self._contact_id(connection, case_id, clean, actor_id)
            claim_id, now = new_id(), utc_now()
            clean["claimant_contact_id"] = contact_id
            clean["claim_number"] = self._next_number(connection, case_id)
            allowed = self.store._module_values("claims", clean)
            base = {"id": claim_id, "case_id": case_id, **allowed, "created_by": actor_id, "updated_by": actor_id, "created_at": now, "updated_at": now}
            connection.execute(
                f"INSERT INTO claims ({', '.join(base)}) VALUES ({', '.join('?' for _ in base)})", tuple(base.values())
            )
            snapshot = {**clean, "id": claim_id, "revision": 1}
            connection.execute(
                "INSERT INTO claim_revisions(id,case_id,claim_id,revision,snapshot_json,reason,document_ids_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                (new_id(), case_id, claim_id, 1, _json(snapshot), "Original claim received", "[]", actor_id, now),
            )
            for position, label in enumerate(DEFAULT_CHECKLIST, 1):
                connection.execute(
                    """INSERT INTO claim_checklist_items(id,case_id,claim_id,label,status,position,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?, 'MISSING', ?,?,?,?,?)""",
                    (new_id(), case_id, claim_id, label, position, actor_id, actor_id, now, now),
                )
            self.store._create_automated_task(connection, case_id, f"Verify claim {clean['claim_number']} - {clean['creditor_name']}", "Claims", clean["received_date"], "claims", claim_id, actor_id)
            self.store.audit(connection, actor_id, "CLAIM_CREATED", "claim", claim_id, case_id, after=snapshot, title=f"Claim {clean['claim_number']} received")
        result = self.get(case_id, claim_id)
        result["duplicate_candidates"] = self.find_duplicates(case_id, result)
        return result

    def find_duplicates(self, case_id: str, claim: Dict[str, Any]) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            rows = connection.execute(
                """SELECT id,claim_number,creditor_name,received_date,claimed_amount FROM claims
                WHERE case_id=? AND id<>? AND archived_at IS NULL AND lower(creditor_name)=lower(?)
                AND claimed_amount=? AND received_date=?""",
                (case_id, claim["id"], claim.get("creditor_name", ""), claim.get("claimed_amount", 0), claim.get("received_date")),
            ).fetchall()
            return [dict(row) for row in rows]

    def list(self, case_id: str, filters: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
        filters = filters or {}
        clauses, params = ["c.case_id=?", "c.archived_at IS NULL"], [case_id]
        for key in ("status", "creditor_category"):
            if filters.get(key):
                clauses.append(f"c.{key}=?"); params.append(filters[key])
        if filters.get("date_from"):
            clauses.append("c.received_date>=?"); params.append(filters["date_from"])
        if filters.get("date_to"):
            clauses.append("c.received_date<=?"); params.append(filters["date_to"])
        term = str(filters.get("search") or filters.get("creditor_name") or "").strip()
        if term:
            clauses.append("(c.creditor_name LIKE ? OR c.claim_number LIKE ? OR c.email LIKE ?)")
            params.extend([f"%{term}%"] * 3)
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                f"""SELECT c.*,
                (SELECT COUNT(*) FROM documents d WHERE d.case_id=c.case_id AND d.linked_type='claim' AND d.linked_id=c.id AND d.archived_at IS NULL) AS document_count,
                (SELECT title FROM activity_events a WHERE a.case_id=c.case_id AND a.entity_type='claim' AND a.entity_id=c.id ORDER BY occurred_at DESC LIMIT 1) AS last_action
                FROM claims c WHERE {' AND '.join(clauses)} ORDER BY c.received_date DESC,c.created_at DESC""", params
            ).fetchall()
            return [self.store.row(row) for row in rows]

    def get(self, case_id: str, claim_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            row = connection.execute("SELECT * FROM claims WHERE id=? AND case_id=? AND archived_at IS NULL", (claim_id, case_id)).fetchone()
            if not row:
                raise KeyError("Claim not found")
            result = self.store.row(row)
            result["total_mismatch"] = result.get("calculated_component_total") is not None and abs(float(result["calculated_component_total"]) - float(result["claimed_amount"])) >= 0.01
            result["documents"] = [self.store.row(r) for r in connection.execute("SELECT * FROM documents WHERE case_id=? AND linked_type='claim' AND linked_id=? AND archived_at IS NULL ORDER BY created_at DESC", (case_id, claim_id)).fetchall()]
            result["checklist"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_checklist_items WHERE case_id=? AND claim_id=? ORDER BY position,created_at", (case_id, claim_id)).fetchall()]
            result["queries"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_queries WHERE case_id=? AND claim_id=? ORDER BY created_at DESC", (case_id, claim_id)).fetchall()]
            for query in result["queries"]:
                query["responses"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_query_responses WHERE case_id=? AND claim_id=? AND query_id=? ORDER BY created_at", (case_id, claim_id, query["id"])).fetchall()]
            result["decisions"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_decisions WHERE case_id=? AND claim_id=? ORDER BY created_at DESC", (case_id, claim_id)).fetchall()]
            result["revisions"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_revisions WHERE case_id=? AND claim_id=? ORDER BY revision DESC", (case_id, claim_id)).fetchall()]
            result["history"] = [self.store.row(r) for r in connection.execute("SELECT * FROM activity_events WHERE case_id=? AND entity_type='claim' AND entity_id=? ORDER BY occurred_at DESC", (case_id, claim_id)).fetchall()]
            return result

    def summary(self, case_id: str) -> Dict[str, Any]:
        claims = self.list(case_id)
        categories = {key: 0 for key in CREDITOR_CATEGORIES}
        statuses = {key: 0 for key in CLAIM_STATUSES}
        for claim in claims:
            categories[claim["creditor_category"]] = categories.get(claim["creditor_category"], 0) + 1
            statuses[claim["status"]] = statuses.get(claim["status"], 0) + 1
        return {
            "total_claims": len(claims),
            "total_claimed": round(sum(float(c.get("claimed_amount") or 0) for c in claims), 2),
            "total_admitted": round(sum(float(c.get("admitted_amount") or 0) for c in claims), 2),
            "categories": categories, "statuses": statuses,
        }

    def update(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        before = self.get(case_id, claim_id)
        protected = {"id", "case_id", "claim_number", "created_by", "created_at", "revision", "status", "admitted_amount", "amount_not_admitted", "decision_by", "decision_date"}
        clean = {k: v for k, v in payload.items() if k not in protected}
        if "email" in clean and clean["email"] and not EMAIL_RE.match(str(clean["email"])):
            raise ValueError("Email address is invalid")
        if "creditor_category" in clean and clean["creditor_category"] not in CREDITOR_CATEGORIES:
            raise ValueError("Unsupported creditor category")
        if "form_type" in clean and clean["form_type"] not in FORM_TYPES:
            raise ValueError("Unsupported claim form/type")
        if "related_party_status" in clean and clean["related_party_status"] not in RELATED_PARTY:
            raise ValueError("Unsupported related-party status")
        if "secured_status" in clean and clean["secured_status"] not in SECURED_STATUSES:
            raise ValueError("Unsupported security status")
        for key in ("received_date", "date_debt_incurred", "due_date", "default_date"):
            if key in clean:
                clean[key] = _date(clean[key], key.replace("_", " "), required=key == "received_date")
        for key in ("principal_claimed", "interest_claimed", "other_amount_claimed", "claimed_amount", "security_value", "interest_rate"):
            if key in clean:
                clean[key] = _money(clean[key], optional=key != "claimed_amount")
        components = [clean.get(k, before.get(k)) for k in ("principal_claimed", "interest_claimed", "other_amount_claimed")]
        if any(v is not None for v in components):
            clean["calculated_component_total"] = round(sum(v or 0 for v in components), 2)
        clean["amount_not_admitted"] = round(float(clean.get("claimed_amount", before["claimed_amount"])) - float(before.get("admitted_amount") or 0), 2)
        values = self.store._module_values("claims", clean)
        if not values:
            return before
        values.update({"updated_by": actor_id, "updated_at": utc_now()})
        with self.store.transaction() as connection:
            connection.execute(f"UPDATE claims SET {', '.join(f'{k}=?' for k in values)} WHERE id=? AND case_id=?", (*values.values(), claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_UPDATED", "claim", claim_id, case_id, before=before, after=clean, title=f"Claim {before['claim_number']} updated")
        return self.get(case_id, claim_id)

    def start_verification(self, case_id: str, claim_id: str, actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        if claim["status"] in DECISION_STATUSES or claim["status"] == "WITHDRAWN":
            raise ValueError("A decided or withdrawn claim cannot start verification without a revision")
        with self.store.transaction() as connection:
            connection.execute("UPDATE claims SET status='UNDER_VERIFICATION',updated_by=?,updated_at=? WHERE id=? AND case_id=?", (actor_id, utc_now(), claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_VERIFICATION_STARTED", "claim", claim_id, case_id, before={"status": claim["status"]}, after={"status": "UNDER_VERIFICATION"}, title=f"Verification started for {claim['claim_number']}")
        return self.get(case_id, claim_id)

    def decide(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        status = str(payload.get("decision_status") or "").upper()
        if status not in DECISION_STATUSES:
            raise ValueError("Decision must be Admitted, Partly Admitted or Rejected")
        principal = _money(payload.get("principal_admitted"))
        interest = _money(payload.get("interest_admitted"))
        other = _money(payload.get("other_amount_admitted"))
        stated = _money(payload.get("admitted_amount", principal + interest + other))
        if abs(stated - (principal + interest + other)) >= 0.01:
            raise ValueError("Admitted total does not match admitted components")
        claimed = float(claim["claimed_amount"])
        reason, override = str(payload.get("reason") or "").strip(), str(payload.get("override_reason") or "").strip()
        normal = ((status == "ADMITTED" and stated == claimed) or (status == "PARTLY_ADMITTED" and 0 < stated < claimed) or (status == "REJECTED" and stated == 0))
        if not normal and not override:
            raise ValueError("This decision conflicts with normal amount rules; provide an explicit override reason")
        if status in {"PARTLY_ADMITTED", "REJECTED"} and not reason:
            raise ValueError("A reason is required for partly admitted or rejected claims")
        decision_date = _date(payload.get("decision_date") or date.today().isoformat(), "Decision date", required=True)
        not_admitted = round(claimed - stated, 2)
        action = {"ADMITTED": "CLAIM_ADMITTED", "PARTLY_ADMITTED": "CLAIM_PARTLY_ADMITTED", "REJECTED": "CLAIM_REJECTED"}[status]
        if claim["decisions"]:
            action = "CLAIM_DECISION_REVISED"
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO claim_decisions(id,case_id,claim_id,claim_revision,decision_date,decision_by,decision_status,
                principal_admitted,interest_admitted,other_amount_admitted,admitted_amount,amount_not_admitted,reason,override_reason,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), case_id, claim_id, claim["revision"], decision_date, actor_id, status, principal, interest, other, stated, not_admitted, reason, override, now),
            )
            connection.execute(
                """UPDATE claims SET status=?,principal_admitted=?,interest_admitted=?,other_amount_admitted=?,admitted_amount=?,
                amount_not_admitted=?,verification_notes=?,issues_identified=?,documents_checked=?,related_party_status=?,secured_status=?,
                decision_date=?,decision_by=?,decision_reason=?,override_reason=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (status, principal, interest, other, stated, not_admitted, payload.get("verification_notes", ""), payload.get("issues_identified", ""),
                 payload.get("documents_checked", ""), payload.get("related_party_status", claim["related_party_status"]), payload.get("secured_status", claim["secured_status"]),
                 decision_date, actor_id, reason, override, actor_id, now, claim_id, case_id),
            )
            self.store.audit(connection, actor_id, action, "claim", claim_id, case_id, before={"status": claim["status"], "admitted_amount": claim["admitted_amount"]}, after={"status": status, "admitted_amount": stated, "reason": reason, "override_reason": override}, title=f"Claim {claim['claim_number']} {status.replace('_', ' ').lower()}")
        return self.get(case_id, claim_id)

    def revise(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("Revision reason is required")
        changes = dict(payload.get("changes") or {})
        permitted = {"principal_claimed", "interest_claimed", "other_amount_claimed", "claimed_amount", "source_notes"}
        changes = {k: v for k, v in changes.items() if k in permitted}
        for key in permitted & changes.keys():
            if key != "source_notes": changes[key] = _money(changes[key], optional=key != "claimed_amount")
        merged = {**claim, **changes}
        components = [merged.get(k) for k in ("principal_claimed", "interest_claimed", "other_amount_claimed")]
        calculated = round(sum(v or 0 for v in components), 2) if any(v is not None for v in components) else None
        revision, now = int(claim["revision"]) + 1, utc_now()
        document_ids = list(payload.get("document_ids") or [])
        with self.store.transaction() as connection:
            for document_id in document_ids:
                if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND linked_type='claim' AND linked_id=? AND archived_at IS NULL", (document_id, case_id, claim_id)).fetchone():
                    raise ValueError("A revision document is not linked to this claim")
            connection.execute(
                """UPDATE claims SET principal_claimed=?,interest_claimed=?,other_amount_claimed=?,claimed_amount=?,calculated_component_total=?,
                amount_not_admitted=?,source_notes=?,revision=?,status='RESPONSE_RECEIVED',updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (merged.get("principal_claimed"), merged.get("interest_claimed"), merged.get("other_amount_claimed"), merged.get("claimed_amount"), calculated,
                 round(float(merged.get("claimed_amount") or 0) - float(claim.get("admitted_amount") or 0), 2), merged.get("source_notes", ""), revision, actor_id, now, claim_id, case_id),
            )
            snapshot = {k: merged.get(k) for k in MODULE_FIELDS["claims"]}
            connection.execute("INSERT INTO claim_revisions(id,case_id,claim_id,revision,snapshot_json,reason,document_ids_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (new_id(), case_id, claim_id, revision, _json(snapshot), reason, _json(document_ids), actor_id, now))
            self.store.audit(connection, actor_id, "CLAIM_UPDATED", "claim", claim_id, case_id, before={"revision": claim["revision"], "claimed_amount": claim["claimed_amount"]}, after={"revision": revision, "claimed_amount": merged.get("claimed_amount"), "reason": reason}, title=f"Claim {claim['claim_number']} revised to version {revision}")
        return self.get(case_id, claim_id)

    def set_checklist(self, case_id: str, claim_id: str, item_id: Optional[str], payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        self.get(case_id, claim_id)
        status = str(payload.get("status") or "MISSING").upper()
        if status not in CHECKLIST_STATUSES:
            raise ValueError("Checklist status must be Received, Missing or N.A.")
        now = utc_now()
        with self.store.transaction() as connection:
            if item_id:
                exists = connection.execute("SELECT * FROM claim_checklist_items WHERE id=? AND case_id=? AND claim_id=?", (item_id, case_id, claim_id)).fetchone()
                if not exists: raise KeyError("Checklist item not found")
                connection.execute("UPDATE claim_checklist_items SET label=?,status=?,document_id=?,notes=?,updated_by=?,updated_at=? WHERE id=?", (payload.get("label", exists["label"]), status, payload.get("document_id"), payload.get("notes", ""), actor_id, now, item_id))
            else:
                item_id = new_id()
                position = connection.execute("SELECT COALESCE(MAX(position),0)+1 FROM claim_checklist_items WHERE case_id=? AND claim_id=?", (case_id, claim_id)).fetchone()[0]
                connection.execute("INSERT INTO claim_checklist_items(id,case_id,claim_id,label,status,document_id,notes,position,created_by,updated_by,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (item_id, case_id, claim_id, str(payload.get("label") or "").strip(), status, payload.get("document_id"), payload.get("notes", ""), position, actor_id, actor_id, now, now))
        return next(item for item in self.get(case_id, claim_id)["checklist"] if item["id"] == item_id)

    def create_query(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        subject, text = str(payload.get("subject") or "").strip(), str(payload.get("query_text") or "").strip()
        if not subject or not text: raise ValueError("Query subject and text are required")
        status = str(payload.get("status") or "DRAFT").upper()
        if status not in QUERY_STATUSES: raise ValueError("Unsupported query status")
        query_id, now = new_id(), utc_now()
        query_date = _date(payload.get("query_date") or date.today().isoformat(), "Query date", required=True)
        due = _date(payload.get("response_due_date"), "Response due date")
        with self.store.transaction() as connection:
            connection.execute("""INSERT INTO claim_queries(id,case_id,claim_id,query_date,subject,query_text,information_requested,sent_to,response_due_date,status,created_by,updated_by,created_at,updated_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""", (query_id, case_id, claim_id, query_date, subject, text, payload.get("information_requested", ""), payload.get("sent_to", claim.get("email", "")), due, status, actor_id, actor_id, now, now))
            if status != "DRAFT":
                connection.execute("UPDATE claims SET status='INFORMATION_REQUIRED',updated_by=?,updated_at=? WHERE id=? AND case_id=?", (actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_QUERY_CREATED", "claim", claim_id, case_id, after={"query_id": query_id, "subject": subject, "status": status}, title=f"Query raised for {claim['claim_number']}: {subject}")
        return next(q for q in self.get(case_id, claim_id)["queries"] if q["id"] == query_id)

    def record_response(self, case_id: str, claim_id: str, query_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        response_date = _date(payload.get("response_received_date"), "Response received date", required=True)
        document_ids = list(payload.get("document_ids") or [])
        response_id, now = new_id(), utc_now()
        with self.store.transaction() as connection:
            query = connection.execute("SELECT * FROM claim_queries WHERE id=? AND case_id=? AND claim_id=?", (query_id, case_id, claim_id)).fetchone()
            if not query: raise KeyError("Claim query not found")
            for document_id in document_ids:
                if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND linked_type='claim' AND linked_id=? AND archived_at IS NULL", (document_id, case_id, claim_id)).fetchone():
                    raise ValueError("A response document is not linked to this claim")
            connection.execute("INSERT INTO claim_query_responses(id,case_id,claim_id,query_id,response_received_date,response_notes,source_reference,document_ids_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?,?)", (response_id, case_id, claim_id, query_id, response_date, payload.get("response_notes", ""), payload.get("source_reference", ""), _json(document_ids), actor_id, now))
            new_status = str(payload.get("query_status") or "RESPONDED").upper()
            if new_status not in {"PARTLY_RESPONDED", "RESPONDED", "CLOSED"}: raise ValueError("Unsupported response status")
            connection.execute("UPDATE claim_queries SET status=?,updated_by=?,updated_at=? WHERE id=?", (new_status, actor_id, now, query_id))
            connection.execute("UPDATE claims SET status='RESPONSE_RECEIVED',updated_by=?,updated_at=? WHERE id=? AND case_id=?", (actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_QUERY_RESPONSE_RECORDED", "claim", claim_id, case_id, after={"query_id": query_id, "response_id": response_id, "document_ids": document_ids}, title=f"Query response received for {claim['claim_number']}")
        return next(r for q in self.get(case_id, claim_id)["queries"] if q["id"] == query_id for r in q["responses"] if r["id"] == response_id)

    def link_document(self, case_id: str, claim_id: str, document: Dict[str, Any], document_type: str, actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute("UPDATE documents SET linked_type='claim',linked_id=?,category=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?", (claim_id, document_type, actor_id, now, document["id"], case_id))
            connection.execute("INSERT INTO claim_documents(id,case_id,claim_id,name,required,received,document_id,notes,created_at,updated_at) VALUES (?,?,?,?,0,1,?,'',?,?)", (new_id(), case_id, claim_id, document_type, document["id"], now, now))
            self.store.audit(connection, actor_id, "CLAIM_DOCUMENT_ADDED", "claim", claim_id, case_id, after={"document_id": document["id"], "document_type": document_type}, title=f"Document added to {claim['claim_number']}: {document['name']}")
        return self.store.get_module_record(case_id, "documents", document["id"]) or {}

    def creditors(self, case_id: str) -> List[Dict[str, Any]]:
        return [{
            "claim_id": c["id"], "claim_number": c["claim_number"], "creditor": c["creditor_name"],
            "category": c["creditor_category"], "received_date": c["received_date"], "decision_date": c.get("decision_date"),
            "amount_claimed": c["claimed_amount"], "amount_admitted": c["admitted_amount"],
            "amount_not_admitted": c["amount_not_admitted"], "security_status": c["secured_status"], "decision_status": c["status"],
            "related_party_status": c["related_party_status"],
        } for c in self.list(case_id)]

    def export_creditors_docx(self, case_id: str, output: Path) -> Path:
        case = self.store.get_case(case_id)
        if not case: raise KeyError("Case not found")
        rows = self.creditors(case_id)
        document = Document()
        document.add_heading("List of Creditors", level=1)
        document.add_paragraph(case["name"])
        document.add_paragraph(f"Generated from the confirmed Claims Register on {date.today().isoformat()}.")
        table = document.add_table(rows=1, cols=8)
        table.style = "Table Grid"
        headings = ["Claim No.", "Creditor", "Category", "Claimed (INR)", "Admitted (INR)", "Not Admitted (INR)", "Security", "Decision"]
        for cell, heading in zip(table.rows[0].cells, headings): cell.text = heading
        for row in rows:
            cells = table.add_row().cells
            values = [row["claim_number"], row["creditor"], row["category"].replace("_", " ").title(), f"{row['amount_claimed']:,.2f}", f"{row['amount_admitted']:,.2f}", f"{row['amount_not_admitted']:,.2f}", row["security_status"].replace("_", " ").title(), row["decision_status"].replace("_", " ").title()]
            for cell, value in zip(cells, values): cell.text = str(value)
        output.parent.mkdir(parents=True, exist_ok=True)
        document.save(output)
        return output
