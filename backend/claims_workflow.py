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
from claims_coc_core import to_paise
from workflow import EventEngine


CLAIM_STATUSES = {
    "RECEIVED", "UNDER_VERIFICATION", "INFORMATION_REQUIRED", "RESPONSE_RECEIVED",
    "ADMITTED", "PARTLY_ADMITTED", "NOT_ADMITTED", "REJECTED", "WITHDRAWN",
}
DECISION_STATUSES = {"ADMITTED", "PARTLY_ADMITTED", "NOT_ADMITTED", "REJECTED"}
CREDITOR_CATEGORIES = {
    "FINANCIAL_CREDITOR", "OPERATIONAL_CREDITOR", "WORKMAN_EMPLOYEE",
    "GOVERNMENT_AUTHORITY", "OTHER_CREDITOR", "CREDITOR_IN_CLASS",
}
FORM_TYPES = {"Form B", "Form C", "Form CA", "Form D", "Form E", "Form F", "Other"}
RECEIVED_VIA = {"Email", "Physical", "Portal", "Other"}
RELATED_PARTY = {"YES", "NO", "UNKNOWN"}
SECURED_STATUSES = {"SECURED", "UNSECURED", "PARTLY_SECURED", "NOT_APPLICABLE", "UNKNOWN"}
QUERY_STATUSES = {"DRAFT", "SENT", "PARTLY_RESPONDED", "RESPONDED", "CLOSED"}
CHECKLIST_STATUSES = {"RECEIVED", "MISSING", "NOT_APPLICABLE", "REVIEW_REQUIRED"}
SCRUTINY_STATUSES = {"NOT_STARTED", "IN_REVIEW", "COMPLETE", "DEFICIENCY_FOUND"}

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

    def _emit(self, case_id: str, event_type: str, actor_id: str, source_id: str,
              event_date: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return EventEngine(self.store).record_event(
            case_id, event_type, event_date or date.today().isoformat(), actor_id,
            source_type="claim", source_id=source_id, metadata=metadata or {},
            idempotency_key=f"claim:{event_type}:{source_id}:{(metadata or {}).get('revision', '')}",
        )

    @staticmethod
    def _deadline_state(connection, case_id: str, received_date: str) -> Dict[str, Any]:
        row = connection.execute(
            """SELECT COALESCE(cd.override_due_date,cd.calculated_due_date) AS due_date
            FROM case_deadlines cd JOIN case_workflow_steps cws ON cws.id=cd.case_workflow_step_id
            JOIN workflow_step_definitions wsd ON wsd.id=cws.step_definition_id
            WHERE cd.case_id=? AND wsd.step_code='CIRP-029' ORDER BY cd.created_at DESC LIMIT 1""",
            (case_id,),
        ).fetchone()
        if not row or not row["due_date"]:
            return {"late_flag": 0, "claim_deadline_date": None, "days_after_deadline": 0,
                    "late_review_status": "REVIEW_REQUIRED"}
        received, deadline = date.fromisoformat(received_date), date.fromisoformat(row["due_date"])
        days = max(0, (received - deadline).days)
        return {"late_flag": int(days > 0), "claim_deadline_date": deadline.isoformat(),
                "days_after_deadline": days,
                "late_review_status": "REVIEW_REQUIRED" if days else "NOT_APPLICABLE"}

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
        numbers = connection.execute("SELECT claim_number FROM claims WHERE case_id=?", (case_id,)).fetchall()
        highest = max((int(str(row["claim_number"]).split("-")[-1]) for row in numbers
                       if re.fullmatch(r"CLM-\d+", str(row["claim_number"] or ""))), default=0)
        return f"CLM-{highest + 1:04d}"

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
            clean["claim_reference"] = clean.get("claim_reference") or clean["claim_number"]
            clean.update(self._deadline_state(connection, case_id, clean["received_date"]))
            clean.update({
                "acknowledgement_status": "CONFIRMED", "acknowledged_at": now, "acknowledged_by": actor_id,
                "classification_status": "PENDING", "scrutiny_status": "NOT_STARTED",
                "verification_status": "NOT_STARTED",
                "principal_claimed_paise": to_paise(clean.get("principal_claimed")) if clean.get("principal_claimed") is not None else None,
                "interest_claimed_paise": to_paise(clean.get("interest_claimed")) if clean.get("interest_claimed") is not None else None,
                "other_claimed_paise": to_paise(clean.get("other_amount_claimed")) if clean.get("other_amount_claimed") is not None else None,
                "total_claimed_paise": to_paise(clean["claimed_amount"]), "total_admitted_paise": 0,
                "amount_not_admitted_paise": to_paise(clean["claimed_amount"]),
            })
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
            self.store.audit(connection, actor_id, "CLAIM_ACKNOWLEDGED", "claim", claim_id, case_id,
                             after={"claim_number": clean["claim_number"], "acknowledged_at": now},
                             title=f"Claim {clean['claim_number']} acknowledged")
        self._emit(case_id, "CLAIM_RECEIVED", actor_id, claim_id, clean["received_date"],
                   {"claim_number": clean["claim_number"], "late_flag": bool(clean["late_flag"])})
        self._emit(case_id, "CLAIM_ACKNOWLEDGED", actor_id, claim_id, clean["received_date"],
                   {"claim_number": clean["claim_number"]})
        if clean["late_flag"]:
            self._emit(case_id, "LATE_CLAIM_IDENTIFIED", actor_id, claim_id, clean["received_date"],
                       {"deadline_date": clean["claim_deadline_date"], "days_after_deadline": clean["days_after_deadline"]})
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
            result["related_party_reviews"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_related_party_reviews WHERE case_id=? AND claim_id=? ORDER BY determined_at DESC", (case_id, claim_id)).fetchall()]
            result["security_reviews"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_security_reviews WHERE case_id=? AND claim_id=? ORDER BY reviewed_at DESC", (case_id, claim_id)).fetchall()]
            result["decision_communications"] = [self.store.row(r) for r in connection.execute("SELECT * FROM claim_decision_communications WHERE case_id=? AND claim_id=? ORDER BY created_at DESC", (case_id, claim_id)).fetchall()]
            result["history"] = [self.store.row(r) for r in connection.execute("SELECT * FROM activity_events WHERE case_id=? AND entity_type='claim' AND entity_id=? ORDER BY occurred_at DESC", (case_id, claim_id)).fetchall()]
            return result

    def summary(self, case_id: str) -> Dict[str, Any]:
        claims = self.list(case_id)
        categories = {key: 0 for key in CREDITOR_CATEGORIES}
        statuses = {key: 0 for key in CLAIM_STATUSES}
        for claim in claims:
            categories[claim["creditor_category"]] = categories.get(claim["creditor_category"], 0) + 1
            statuses[claim["status"]] = statuses.get(claim["status"], 0) + 1
        total_claimed_paise = sum(int(c.get("total_claimed_paise") or to_paise(c.get("claimed_amount"))) for c in claims)
        total_admitted_paise = sum(int(c.get("total_admitted_paise") or to_paise(c.get("admitted_amount"))) for c in claims)
        return {
            "total_claims": len(claims),
            "total_claimed": float(Decimal(total_claimed_paise) / 100),
            "total_admitted": float(Decimal(total_admitted_paise) / 100),
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
        claimed_paise = to_paise(clean.get("claimed_amount", before["claimed_amount"]))
        admitted_paise = int(before.get("total_admitted_paise") or to_paise(before.get("admitted_amount")))
        not_admitted_paise = max(0, claimed_paise - admitted_paise)
        clean["amount_not_admitted"] = float(Decimal(not_admitted_paise) / 100)
        clean["total_claimed_paise"] = claimed_paise
        clean["amount_not_admitted_paise"] = not_admitted_paise
        for public_key, exact_key in (("principal_claimed", "principal_claimed_paise"),
                                      ("interest_claimed", "interest_claimed_paise"),
                                      ("other_amount_claimed", "other_claimed_paise")):
            value = clean.get(public_key, before.get(public_key))
            clean[exact_key] = to_paise(value) if value is not None else None
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
            now = utc_now()
            connection.execute("""UPDATE claims SET status='UNDER_VERIFICATION',verification_status='IN_REVIEW',
                               verification_started_at=COALESCE(verification_started_at,?),updated_by=?,updated_at=?
                               WHERE id=? AND case_id=?""", (now, actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_VERIFICATION_STARTED", "claim", claim_id, case_id, before={"status": claim["status"]}, after={"status": "UNDER_VERIFICATION"}, title=f"Verification started for {claim['claim_number']}")
        self._emit(case_id, "CLAIM_VERIFICATION_STARTED", actor_id, claim_id,
                   metadata={"revision": claim["revision"]})
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
        principal_paise, interest_paise, other_paise = to_paise(principal), to_paise(interest), to_paise(other)
        stated_paise = to_paise(stated)
        if stated_paise != principal_paise + interest_paise + other_paise:
            raise ValueError("Admitted total does not match admitted components")
        claimed_paise = int(claim.get("total_claimed_paise") or to_paise(claim["claimed_amount"]))
        claimed = float(Decimal(claimed_paise) / 100)
        reason, override = str(payload.get("reason") or "").strip(), str(payload.get("override_reason") or "").strip()
        normal = ((status == "ADMITTED" and stated_paise == claimed_paise)
                  or (status == "PARTLY_ADMITTED" and 0 < stated_paise < claimed_paise)
                  or (status in {"NOT_ADMITTED", "REJECTED"} and stated_paise == 0))
        if not normal and not override:
            raise ValueError("This decision conflicts with normal amount rules; provide an explicit override reason")
        if status in {"PARTLY_ADMITTED", "NOT_ADMITTED", "REJECTED"} and not reason:
            raise ValueError("A reason is required for partly admitted or rejected claims")
        decision_date = _date(payload.get("decision_date") or date.today().isoformat(), "Decision date", required=True)
        not_admitted_paise = max(0, claimed_paise - stated_paise)
        not_admitted = float(Decimal(not_admitted_paise) / 100)
        action = {"ADMITTED": "CLAIM_ADMITTED", "PARTLY_ADMITTED": "CLAIM_PARTLY_ADMITTED", "NOT_ADMITTED": "CLAIM_NOT_ADMITTED", "REJECTED": "CLAIM_REJECTED"}[status]
        if claim["decisions"]:
            action = "CLAIM_DECISION_REVISED"
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute(
                """INSERT INTO claim_decisions(id,case_id,claim_id,claim_revision,decision_date,decision_by,decision_status,
                principal_admitted,interest_admitted,other_amount_admitted,admitted_amount,amount_not_admitted,reason,override_reason,created_at,
                principal_admitted_paise,interest_admitted_paise,other_admitted_paise,total_admitted_paise,amount_not_admitted_paise)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (new_id(), case_id, claim_id, claim["revision"], decision_date, actor_id, status, principal, interest, other, stated, not_admitted, reason, override, now,
                 principal_paise, interest_paise, other_paise, stated_paise, not_admitted_paise),
            )
            connection.execute(
                """UPDATE claims SET status=?,principal_admitted=?,interest_admitted=?,other_amount_admitted=?,admitted_amount=?,
                amount_not_admitted=?,verification_notes=?,issues_identified=?,documents_checked=?,related_party_status=?,secured_status=?,
                decision_date=?,decision_by=?,decision_reason=?,override_reason=?,verification_status='COMPLETE',verification_completed_at=?,
                verification_completed_by=?,principal_admitted_paise=?,interest_admitted_paise=?,other_admitted_paise=?,total_admitted_paise=?,
                amount_not_admitted_paise=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (status, principal, interest, other, stated, not_admitted, payload.get("verification_notes", ""), payload.get("issues_identified", ""),
                 payload.get("documents_checked", ""), payload.get("related_party_status", claim["related_party_status"]), payload.get("secured_status", claim["secured_status"]),
                 decision_date, actor_id, reason, override, now, actor_id, principal_paise, interest_paise, other_paise,
                 stated_paise, not_admitted_paise, actor_id, now, claim_id, case_id),
            )
            self.store.audit(connection, actor_id, action, "claim", claim_id, case_id, before={"status": claim["status"], "admitted_amount": claim["admitted_amount"]}, after={"status": status, "admitted_amount": stated, "reason": reason, "override_reason": override}, title=f"Claim {claim['claim_number']} {status.replace('_', ' ').lower()}")
        self._emit(case_id, "CLAIM_VERIFICATION_COMPLETED", actor_id, claim_id, decision_date,
                   {"revision": claim["revision"]})
        domain_event = {"ADMITTED": "CLAIM_ADMITTED", "PARTLY_ADMITTED": "CLAIM_PARTLY_ADMITTED", "NOT_ADMITTED": "CLAIM_NOT_ADMITTED", "REJECTED": "CLAIM_NOT_ADMITTED"}[status]
        self._emit(case_id, domain_event, actor_id, claim_id, decision_date,
                   {"revision": claim["revision"], "admitted_paise": stated_paise})
        self._flag_coc_change(case_id, claim_id, actor_id, decision_date, "CLAIM_DECISION_CHANGED")
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
        revised_claimed_paise = to_paise(merged.get("claimed_amount"))
        current_admitted_paise = int(claim.get("total_admitted_paise") or to_paise(claim.get("admitted_amount")))
        revised_not_admitted_paise = max(0, revised_claimed_paise - current_admitted_paise)
        with self.store.transaction() as connection:
            for document_id in document_ids:
                if not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND linked_type='claim' AND linked_id=? AND archived_at IS NULL", (document_id, case_id, claim_id)).fetchone():
                    raise ValueError("A revision document is not linked to this claim")
            connection.execute(
                """UPDATE claims SET principal_claimed=?,interest_claimed=?,other_amount_claimed=?,claimed_amount=?,calculated_component_total=?,
                amount_not_admitted=?,source_notes=?,revision=?,status='RESPONSE_RECEIVED',principal_claimed_paise=?,interest_claimed_paise=?,
                other_claimed_paise=?,total_claimed_paise=?,amount_not_admitted_paise=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (merged.get("principal_claimed"), merged.get("interest_claimed"), merged.get("other_amount_claimed"), merged.get("claimed_amount"), calculated,
                 float(Decimal(revised_not_admitted_paise) / 100), merged.get("source_notes", ""), revision,
                 to_paise(merged.get("principal_claimed")) if merged.get("principal_claimed") is not None else None,
                 to_paise(merged.get("interest_claimed")) if merged.get("interest_claimed") is not None else None,
                 to_paise(merged.get("other_amount_claimed")) if merged.get("other_amount_claimed") is not None else None,
                 revised_claimed_paise, revised_not_admitted_paise,
                 actor_id, now, claim_id, case_id),
            )
            snapshot = {k: merged.get(k) for k in MODULE_FIELDS["claims"]}
            connection.execute("INSERT INTO claim_revisions(id,case_id,claim_id,revision,snapshot_json,reason,document_ids_json,created_by,created_at) VALUES (?,?,?,?,?,?,?,?,?)", (new_id(), case_id, claim_id, revision, _json(snapshot), reason, _json(document_ids), actor_id, now))
            self.store.audit(connection, actor_id, "CLAIM_REVISED", "claim", claim_id, case_id, before={"revision": claim["revision"], "claimed_amount": claim["claimed_amount"]}, after={"revision": revision, "claimed_amount": merged.get("claimed_amount"), "reason": reason}, title=f"Claim {claim['claim_number']} revised to version {revision}")
            # Retain the Phase-1 audit action for backward-compatible reports.
            self.store.audit(connection, actor_id, "CLAIM_UPDATED", "claim", claim_id, case_id, before={"revision": claim["revision"]}, after={"revision": revision}, title=f"Claim {claim['claim_number']} revision stored")
        self._emit(case_id, "CLAIM_REVISED", actor_id, claim_id,
                   metadata={"revision": revision, "reason": reason})
        self._flag_coc_change(case_id, claim_id, actor_id, None, "CLAIM_REVISED")
        return self.get(case_id, claim_id)

    def _flag_coc_change(self, case_id: str, claim_id: str, actor_id: str,
                         event_date: Optional[str], reason: str) -> None:
        with self.store.connect() as connection:
            constitution = connection.execute(
                "SELECT id FROM coc_constitutions WHERE case_id=? ORDER BY constitution_version DESC LIMIT 1",
                (case_id,),
            ).fetchone()
        if constitution:
            self._emit(case_id, "COC_REVIEW_REQUIRED", actor_id, f"{constitution['id']}:{claim_id}:{reason}",
                       event_date, {"constitution_id": constitution["id"], "claim_id": claim_id, "reason": reason})

    def confirm_classification(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        category = str(payload.get("creditor_category") or claim["creditor_category"]).upper()
        if category not in CREDITOR_CATEGORIES:
            raise ValueError("Unsupported creditor category")
        form_type = payload.get("form_type", claim["form_type"])
        if form_type not in FORM_TYPES:
            raise ValueError("Unsupported claim form/type")
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute(
                """UPDATE claims SET creditor_category=?,form_type=?,classification_status='CONFIRMED',
                classification_confirmed_at=?,classification_confirmed_by=?,updated_by=?,updated_at=?
                WHERE id=? AND case_id=?""",
                (category, form_type, now, actor_id, actor_id, now, claim_id, case_id),
            )
            self.store.audit(connection, actor_id, "CLAIM_CLASSIFICATION_CONFIRMED", "claim", claim_id, case_id,
                             before={"creditor_category": claim["creditor_category"], "form_type": claim["form_type"]},
                             after={"creditor_category": category, "form_type": form_type},
                             title=f"Classification confirmed for {claim['claim_number']}")
        self._emit(case_id, "CLAIM_CLASSIFICATION_CONFIRMED", actor_id, claim_id,
                   metadata={"revision": claim["revision"], "category": category})
        return self.get(case_id, claim_id)

    def update_scrutiny(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        status = str(payload.get("scrutiny_status") or "").upper()
        if status not in SCRUTINY_STATUSES:
            raise ValueError("Unsupported scrutiny status")
        with self.store.transaction() as connection:
            connection.execute("UPDATE claims SET scrutiny_status=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?",
                               (status, actor_id, utc_now(), claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_SCRUTINY_UPDATED", "claim", claim_id, case_id,
                             before={"scrutiny_status": claim.get("scrutiny_status")}, after={"scrutiny_status": status},
                             title=f"Scrutiny updated for {claim['claim_number']}")
        if status == "DEFICIENCY_FOUND":
            self._emit(case_id, "CLAIM_DEFICIENCY_IDENTIFIED", actor_id, claim_id,
                       metadata={"revision": claim["revision"]})
        return self.get(case_id, claim_id)

    def complete_verification(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        if claim["classification_status"] != "CONFIRMED":
            raise ValueError("Claim classification must be confirmed before verification is completed")
        if claim["scrutiny_status"] not in {"COMPLETE", "DEFICIENCY_FOUND"}:
            raise ValueError("Claim scrutiny must be completed or its deficiency recorded")
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute(
                """UPDATE claims SET verification_status='COMPLETE',verification_completed_at=?,verification_completed_by=?,
                verification_notes=?,issues_identified=?,documents_checked=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                (now, actor_id, payload.get("verification_notes", claim.get("verification_notes", "")),
                 payload.get("issues_identified", claim.get("issues_identified", "")),
                 payload.get("documents_checked", claim.get("documents_checked", "")), actor_id, now, claim_id, case_id),
            )
            self.store.audit(connection, actor_id, "CLAIM_VERIFICATION_COMPLETED", "claim", claim_id, case_id,
                             after={"verification_status": "COMPLETE", "revision": claim["revision"]},
                             title=f"Verification completed for {claim['claim_number']}")
        self._emit(case_id, "CLAIM_VERIFICATION_COMPLETED", actor_id, claim_id,
                   metadata={"revision": claim["revision"]})
        return self.get(case_id, claim_id)

    def confirm_related_party(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        status = str(payload.get("related_party_status") or "").upper()
        if status not in RELATED_PARTY:
            raise ValueError("Related-party status must be YES, NO or UNKNOWN")
        reason = str(payload.get("reason") or "").strip()
        if status != "UNKNOWN" and not reason:
            raise ValueError("Related-party determination reason is required")
        evidence = payload.get("evidence_document_id")
        now, review_id = utc_now(), new_id()
        with self.store.transaction() as connection:
            if evidence and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (evidence, case_id)
            ).fetchone():
                raise ValueError("Related-party evidence document does not belong to this case")
            connection.execute(
                """INSERT INTO claim_related_party_reviews
                (id,case_id,claim_id,claim_revision,status,reason,evidence_document_id,determined_by,determined_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (review_id, case_id, claim_id, claim["revision"], status, reason, evidence, actor_id, now, now),
            )
            connection.execute("UPDATE claims SET related_party_status=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?",
                               (status, actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "RELATED_PARTY_STATUS_CONFIRMED", "claim", claim_id, case_id,
                             before={"status": claim["related_party_status"]}, after={"status": status, "reason": reason},
                             title=f"Related-party status confirmed for {claim['claim_number']}")
        self._emit(case_id, "RELATED_PARTY_STATUS_CONFIRMED", actor_id, review_id,
                   metadata={"claim_id": claim_id, "revision": claim["revision"], "status": status})
        if claim["related_party_status"] != status:
            self._emit(case_id, "RELATED_PARTY_STATUS_CHANGED", actor_id, review_id,
                       metadata={"claim_id": claim_id, "from": claim["related_party_status"], "to": status})
            self._flag_coc_change(case_id, claim_id, actor_id, None, "RELATED_PARTY_STATUS_CHANGED")
        return self.get(case_id, claim_id)

    def record_security_review(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        claimed = str(payload.get("security_status_claimed") or claim["secured_status"] or "UNKNOWN").upper()
        verified = str(payload.get("security_status_verified") or "UNKNOWN").upper()
        if claimed not in SECURED_STATUSES or verified not in SECURED_STATUSES:
            raise ValueError("Unsupported security status")
        verification = str(payload.get("verification_status") or "REVIEW_REQUIRED").upper()
        if verification not in {"REVIEW_REQUIRED", "IN_REVIEW", "VERIFIED", "DISPUTED"}:
            raise ValueError("Unsupported security verification status")
        evidence = payload.get("evidence_document_id")
        now, review_id = utc_now(), new_id()
        with self.store.transaction() as connection:
            if evidence and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (evidence, case_id)
            ).fetchone():
                raise ValueError("Security evidence document does not belong to this case")
            connection.execute(
                """INSERT INTO claim_security_reviews
                (id,case_id,claim_id,claim_revision,status_claimed,status_verified,verification_status,
                 review_notes,evidence_document_id,reviewed_by,reviewed_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (review_id, case_id, claim_id, claim["revision"], claimed, verified, verification,
                 payload.get("review_notes", ""), evidence, actor_id, now, now),
            )
            if verification == "VERIFIED":
                connection.execute("UPDATE claims SET secured_status=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?",
                                   (verified, actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "CLAIM_SECURITY_REVIEWED", "claim", claim_id, case_id,
                             after={"review_id": review_id, "verification_status": verification,
                                    "status_claimed": claimed, "status_verified": verified},
                             title=f"Security review recorded for {claim['claim_number']}")
        if verification in {"REVIEW_REQUIRED", "DISPUTED"}:
            self._emit(case_id, "SECURITY_REVIEW_REQUIRED", actor_id, review_id,
                       metadata={"claim_id": claim_id, "status": verification})
        return self.get(case_id, claim_id)

    def record_late_review(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        status = str(payload.get("review_status") or "").upper()
        if status not in {"REVIEW_REQUIRED", "IN_REVIEW", "REVIEWED"}:
            raise ValueError("Unsupported late-claim review status")
        reason = str(payload.get("reason") or "").strip()
        if not reason:
            raise ValueError("Late-claim review reason/remarks are required")
        now = utc_now()
        with self.store.transaction() as connection:
            connection.execute("""UPDATE claims SET late_review_status=?,late_review_reason=?,late_reviewed_by=?,late_reviewed_at=?,
                               updated_by=?,updated_at=? WHERE id=? AND case_id=?""",
                               (status, reason, actor_id, now, actor_id, now, claim_id, case_id))
            self.store.audit(connection, actor_id, "LATE_CLAIM_REVIEWED", "claim", claim_id, case_id,
                             after={"review_status": status, "reason": reason},
                             title=f"Late-claim review updated for {claim['claim_number']}")
        return self.get(case_id, claim_id)

    def prepare_decision_communication(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        claim = self.get(case_id, claim_id)
        if not claim["decisions"]:
            raise ValueError("A confirmed Claim decision is required")
        decision = claim["decisions"][0]
        status = str(payload.get("status") or "DRAFT").upper()
        if status not in COMMUNICATION_STATUSES:
            raise ValueError("Unsupported Claim decision communication status")
        proof = payload.get("service_proof_document_id")
        now = utc_now()
        with self.store.transaction() as connection:
            existing = connection.execute("SELECT * FROM claim_decision_communications WHERE case_id=? AND decision_id=?",
                                          (case_id, decision["id"])).fetchone()
            if proof and not connection.execute("SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (proof, case_id)).fetchone():
                raise ValueError("Service proof document does not belong to this case")
            if status == "SENT" and not proof:
                raise ValueError("Service proof is required before marking the communication sent")
            communication_id = payload.get("communication_id")
            if communication_id and not connection.execute("SELECT 1 FROM communications WHERE id=? AND case_id=?", (communication_id, case_id)).fetchone():
                raise ValueError("Communication record does not belong to this case")
            if existing:
                connection.execute("""UPDATE claim_decision_communications SET status=?,communication_id=?,service_proof_document_id=?,
                                   approved_by=CASE WHEN ? IN ('APPROVED','SENT') THEN ? ELSE approved_by END,
                                   sent_at=CASE WHEN ?='SENT' THEN ? ELSE sent_at END,updated_at=? WHERE id=?""",
                                   (status, communication_id, proof, status, actor_id, status, now, now, existing["id"]))
                record_id = existing["id"]
            else:
                record_id = new_id()
                connection.execute("""INSERT INTO claim_decision_communications
                                   (id,case_id,claim_id,decision_id,status,communication_id,service_proof_document_id,
                                    prepared_by,approved_by,sent_at,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (record_id, case_id, claim_id, decision["id"], status, communication_id, proof, actor_id,
                                    actor_id if status in {"APPROVED", "SENT"} else None, now if status == "SENT" else None, now, now))
            self.store.audit(connection, actor_id, "CLAIM_DECISION_COMMUNICATION_UPDATED", "claim", claim_id, case_id,
                             after={"record_id": record_id, "status": status, "service_proof_document_id": proof},
                             title=f"Decision communication {status.lower()} for {claim['claim_number']}")
            return dict(connection.execute("SELECT * FROM claim_decision_communications WHERE id=?", (record_id,)).fetchone())

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
        self._emit(case_id, "CLAIM_QUERY_CREATED", actor_id, query_id, query_date,
                   {"claim_id": claim_id, "status": status})
        if status == "SENT":
            self._emit(case_id, "CLAIM_QUERY_SENT", actor_id, query_id, query_date,
                       {"claim_id": claim_id})
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
        self._emit(case_id, "CLAIM_QUERY_RESPONSE_RECEIVED", actor_id, response_id, response_date,
                   {"claim_id": claim_id, "query_id": query_id, "status": new_status})
        if new_status == "CLOSED":
            self._emit(case_id, "CLAIM_QUERY_CLOSED", actor_id, response_id, response_date,
                       {"claim_id": claim_id, "query_id": query_id})
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
