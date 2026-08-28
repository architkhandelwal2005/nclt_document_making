"""Claims-to-CoC backend core for CIRP workflow steps 034 and 039-045.

The canonical claim remains ``claims``.  This module derives versioned List of
Creditors (LOC), eligibility, voting and constitution records from confirmed
claim decisions; it never accepts a free-standing creditor amount.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal, InvalidOperation, ROUND_DOWN, ROUND_HALF_UP, getcontext
from typing import Any, Dict, Iterable, List, Optional
import hashlib
import json
import sqlite3

from database import CasefileDatabase, new_id, utc_now
from workflow import EventEngine


getcontext().prec = 38
ELIGIBILITY_STATUSES = {"ELIGIBLE", "EXCLUDED", "REVIEW_REQUIRED"}
RELATED_PARTY_STATUSES = {"YES", "NO", "UNKNOWN"}
COMMUNICATION_STATUSES = {"DRAFT", "PREPARED", "APPROVED", "SENT"}


def to_paise(value: Any) -> int:
    """Convert rupees to exact integer paise using commercial half-up rounding."""
    try:
        amount = Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError("Amount must be a valid number") from exc
    if amount < 0:
        raise ValueError("Negative amounts are not supported")
    return int(amount * 100)


def from_paise(value: Any) -> str:
    return format(Decimal(int(value or 0)) / Decimal(100), ".2f")


def _iso_date(value: Any, label: str) -> str:
    try:
        return date.fromisoformat(str(value or "")[:10]).isoformat()
    except ValueError as exc:
        raise ValueError(f"{label} must be a valid ISO date") from exc


def _fingerprint(rows: Iterable[Dict[str, Any]]) -> str:
    payload = json.dumps(list(rows), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class ClaimsCocCore:
    """Case-isolated, auditable derivation service for LOC and CoC constitution."""

    def __init__(self, store: CasefileDatabase):
        self.store = store

    def _emit(self, case_id: str, event_type: str, actor_id: str, source_id: str,
              event_date: Optional[str] = None, metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        return EventEngine(self.store).record_event(
            case_id, event_type, event_date or date.today().isoformat(), actor_id,
            source_type="domain", source_id=source_id, metadata=metadata or {},
            idempotency_key=f"domain:{event_type}:{source_id}",
        )

    @staticmethod
    def _money_fields(row: Dict[str, Any]) -> Dict[str, Any]:
        for key in list(row):
            if key.endswith("_paise"):
                row[key[:-6]] = from_paise(row[key])
        return row

    def current_loc(self, case_id: str) -> Dict[str, Any]:
        """Return the dynamic List of Creditors derived from current claim decisions."""
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                """SELECT c.id AS claim_id,c.claim_number,c.revision AS claim_revision,
                c.creditor_name,c.creditor_category,c.form_type,c.received_date,c.claimed_amount,
                c.admitted_amount,c.amount_not_admitted,c.total_claimed_paise,c.total_admitted_paise,
                c.amount_not_admitted_paise,c.secured_status,c.related_party_status,c.status AS decision_status,
                c.decision_date,
                (SELECT cd.id FROM claim_decisions cd WHERE cd.case_id=c.case_id AND cd.claim_id=c.id
                 ORDER BY cd.created_at DESC LIMIT 1) AS decision_id
                FROM claims c WHERE c.case_id=? AND c.archived_at IS NULL
                ORDER BY c.claim_number,c.created_at""",
                (case_id,),
            ).fetchall()
        result: List[Dict[str, Any]] = []
        totals = {"claimed": 0, "admitted": 0, "not_admitted": 0}
        for source in rows:
            row = dict(source)
            # Backfill exact mirrors for records created before schema version 11.
            claimed = row["total_claimed_paise"] or to_paise(row["claimed_amount"])
            admitted = row["total_admitted_paise"] or to_paise(row["admitted_amount"])
            not_admitted = row["amount_not_admitted_paise"] or max(0, claimed - admitted)
            row.update({"claimed_paise": claimed, "admitted_paise": admitted, "not_admitted_paise": not_admitted})
            for key in ("claimed", "admitted", "not_admitted"):
                totals[key] += row[f"{key}_paise"]
            result.append(self._money_fields(row))
        return {
            "case_id": case_id,
            "dynamic": True,
            "rows": result,
            "totals_paise": totals,
            "totals": {key: from_paise(value) for key, value in totals.items()},
        }

    def create_loc_snapshot(self, case_id: str, actor_id: str, as_on_date: Optional[str] = None) -> Dict[str, Any]:
        current = self.current_loc(case_id)
        if not current["rows"]:
            raise ValueError("At least one Claim is required to create a List of Creditors snapshot")
        source = [{key: row.get(key) for key in (
            "claim_id", "claim_revision", "decision_id", "claimed_paise", "admitted_paise",
            "not_admitted_paise", "decision_status", "related_party_status",
        )} for row in current["rows"]]
        snapshot_id, now = new_id(), utc_now()
        as_on = _iso_date(as_on_date or date.today().isoformat(), "As-on date")
        fingerprint = _fingerprint([{"as_on_date": as_on}, *source])
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM list_of_creditors_snapshots WHERE case_id=? AND source_fingerprint=?",
                (case_id, fingerprint),
            ).fetchone()
            if existing:
                result = self.get_loc_snapshot(case_id, existing["id"])
                result["idempotent_replay"] = True
                return result
            version = int(connection.execute(
                "SELECT COALESCE(MAX(version_number),0)+1 FROM list_of_creditors_snapshots WHERE case_id=?",
                (case_id,),
            ).fetchone()[0])
            totals = current["totals_paise"]
            connection.execute(
                """INSERT INTO list_of_creditors_snapshots
                (id,case_id,version_number,as_on_date,status,total_claimed_paise,total_admitted_paise,
                 total_not_admitted_paise,row_count,source_fingerprint,created_by,created_at)
                VALUES (?,?,?,?, 'FORMAL', ?,?,?,?,?,?,?)""",
                (snapshot_id, case_id, version, as_on, totals["claimed"], totals["admitted"],
                 totals["not_admitted"], len(current["rows"]), fingerprint, actor_id, now),
            )
            for position, row in enumerate(current["rows"], 1):
                connection.execute(
                    """INSERT INTO list_of_creditors_snapshot_rows
                    (id,snapshot_id,case_id,position,claim_id,claim_number,claim_revision,decision_id,
                     creditor_name,creditor_category,form_type,received_date,claimed_paise,admitted_paise,
                     not_admitted_paise,security_status,related_party_status,decision_status,decision_date)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id(), snapshot_id, case_id, position, row["claim_id"], row["claim_number"],
                     row["claim_revision"], row.get("decision_id"), row["creditor_name"],
                     row["creditor_category"], row["form_type"], row.get("received_date"),
                     row["claimed_paise"], row["admitted_paise"], row["not_admitted_paise"],
                     row["secured_status"], row["related_party_status"], row["decision_status"],
                     row.get("decision_date")),
                )
            self.store.audit(connection, actor_id, "LIST_OF_CREDITORS_SNAPSHOT_CREATED",
                             "list_of_creditors_snapshot", snapshot_id, case_id,
                             after={"version": version, "as_on_date": as_on, "row_count": len(current["rows"]),
                                    "totals_paise": totals},
                             title=f"List of Creditors Version {version} created")
        self._emit(case_id, "LIST_OF_CREDITORS_SNAPSHOT_CREATED", actor_id, snapshot_id, as_on,
                   {"version_number": version})
        return self.get_loc_snapshot(case_id, snapshot_id)

    def list_loc_snapshots(self, case_id: str) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                "SELECT * FROM list_of_creditors_snapshots WHERE case_id=? ORDER BY version_number DESC",
                (case_id,),
            ).fetchall()
        return [self._money_fields(dict(row)) for row in rows]

    def get_loc_snapshot(self, case_id: str, snapshot_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM list_of_creditors_snapshots WHERE id=? AND case_id=?", (snapshot_id, case_id)
            ).fetchone()
            if not row:
                raise KeyError("List of Creditors snapshot not found")
            result = self._money_fields(dict(row))
            result["rows"] = [self._money_fields(dict(item)) for item in connection.execute(
                "SELECT * FROM list_of_creditors_snapshot_rows WHERE snapshot_id=? AND case_id=? ORDER BY position",
                (snapshot_id, case_id),
            ).fetchall()]
            return result

    def publish_loc(self, case_id: str, snapshot_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        filing_date = _iso_date(payload.get("filing_date") or date.today().isoformat(), "Filing date")
        mechanism = str(payload.get("mechanism") or "").strip()
        status = str(payload.get("status") or "RECORDED").upper()
        evidence = payload.get("evidence_document_id")
        if not mechanism:
            raise ValueError("Filing/display mechanism is required")
        with self.store.transaction() as connection:
            row = connection.execute(
                "SELECT * FROM list_of_creditors_snapshots WHERE id=? AND case_id=?", (snapshot_id, case_id)
            ).fetchone()
            if not row:
                raise KeyError("List of Creditors snapshot not found")
            if evidence and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (evidence, case_id)
            ).fetchone():
                raise ValueError("Filing evidence document does not belong to this case")
            connection.execute(
                """UPDATE list_of_creditors_snapshots SET filing_status=?,filing_date=?,filing_mechanism=?,
                filing_evidence_document_id=? WHERE id=?""",
                (status, filing_date, mechanism, evidence, snapshot_id),
            )
            self.store.audit(connection, actor_id, "LIST_OF_CREDITORS_PUBLISHED", "list_of_creditors_snapshot",
                             snapshot_id, case_id, after={"status": status, "date": filing_date,
                             "mechanism": mechanism, "evidence_document_id": evidence},
                             title=f"List of Creditors Version {row['version_number']} filing/display recorded")
        self._emit(case_id, "LIST_OF_CREDITORS_PUBLISHED", actor_id, snapshot_id, filing_date)
        return self.get_loc_snapshot(case_id, snapshot_id)

    def coc_candidates(self, case_id: str) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                """SELECT c.id AS claim_id,c.claim_number,c.creditor_name,c.claimant_contact_id,c.revision AS claim_revision,
                COALESCE((SELECT r.status FROM claim_related_party_reviews r
                          WHERE r.case_id=c.case_id AND r.claim_id=c.id ORDER BY r.determined_at DESC LIMIT 1),'UNKNOWN') AS related_party_status,
                (SELECT r.id FROM claim_related_party_reviews r
                 WHERE r.case_id=c.case_id AND r.claim_id=c.id ORDER BY r.determined_at DESC LIMIT 1) AS related_party_review_id,
                c.total_admitted_paise,c.admitted_amount,
                cd.id AS decision_id,cd.decision_status,cd.created_at AS decision_created_at
                FROM claims c JOIN claim_decisions cd ON cd.id=(
                    SELECT x.id FROM claim_decisions x WHERE x.case_id=c.case_id AND x.claim_id=c.id
                    ORDER BY x.created_at DESC LIMIT 1)
                WHERE c.case_id=? AND c.archived_at IS NULL AND c.creditor_category='FINANCIAL_CREDITOR'
                  AND cd.decision_status IN ('ADMITTED','PARTLY_ADMITTED')
                ORDER BY c.claim_number""",
                (case_id,),
            ).fetchall()
            result = []
            for source in rows:
                row = dict(source)
                admitted = row["total_admitted_paise"] or to_paise(row["admitted_amount"])
                review = connection.execute(
                    """SELECT * FROM coc_eligibility_reviews WHERE case_id=? AND claim_id=? AND decision_id=?
                    ORDER BY decided_at DESC LIMIT 1""", (case_id, row["claim_id"], row["decision_id"])
                ).fetchone()
                row.update({
                    "admitted_debt_paise": admitted,
                    "admitted_debt": from_paise(admitted),
                    "eligibility_review_id": review["id"] if review else None,
                    "eligibility_status": review["eligibility_status"] if review else "REVIEW_REQUIRED",
                    "eligibility_reason": review["reason"] if review else "Eligibility requires professional confirmation",
                })
                result.append(row)
            return result

    def confirm_eligibility(self, case_id: str, claim_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        candidates = {row["claim_id"]: row for row in self.coc_candidates(case_id)}
        candidate = candidates.get(claim_id)
        if not candidate:
            raise KeyError("Eligible-review candidate not found for this case")
        status = str(payload.get("eligibility_status") or "").upper()
        if status not in ELIGIBILITY_STATUSES:
            raise ValueError("Eligibility must be ELIGIBLE, EXCLUDED or REVIEW_REQUIRED")
        related = str(candidate["related_party_status"] or "UNKNOWN").upper()
        requested_related = str(payload.get("related_party_status") or related).upper()
        if requested_related != related:
            raise ValueError("Confirm related-party status through the dedicated review before CoC eligibility")
        if related not in RELATED_PARTY_STATUSES:
            raise ValueError("Related-party status must be YES, NO or UNKNOWN")
        reason = str(payload.get("reason") or "").strip()
        if status != "ELIGIBLE" and not reason:
            raise ValueError("A reason is required unless the Claim is confirmed eligible")
        if status == "ELIGIBLE" and related == "UNKNOWN":
            raise ValueError("RELATED_PARTY_REVIEW_REQUIRED")
        evidence = payload.get("evidence_document_id")
        now = utc_now()
        with self.store.transaction() as connection:
            if evidence and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (evidence, case_id)
            ).fetchone():
                raise ValueError("Eligibility evidence document does not belong to this case")
            existing = connection.execute(
                """SELECT * FROM coc_eligibility_reviews WHERE case_id=? AND claim_id=? AND decision_id=?
                ORDER BY decided_at DESC LIMIT 1""", (case_id, claim_id, candidate["decision_id"])
            ).fetchone()
            if existing and existing["eligibility_status"] == status and existing["related_party_status"] == related and existing["reason"] == reason:
                return dict(existing)
            review_id = new_id()
            debt = candidate["admitted_debt_paise"] if status == "ELIGIBLE" else 0
            connection.execute(
                """INSERT INTO coc_eligibility_reviews
                (id,case_id,claim_id,claim_revision,decision_id,related_party_status,eligibility_status,
                 eligible_debt_paise,reason,evidence_document_id,decided_by,decided_at,created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (review_id, case_id, claim_id, candidate["claim_revision"], candidate["decision_id"],
                 related, status, debt, reason, evidence, actor_id, now, now),
            )
            connection.execute(
                "UPDATE claims SET related_party_status=?,updated_by=?,updated_at=? WHERE id=? AND case_id=?",
                (related, actor_id, now, claim_id, case_id),
            )
            self.store.audit(connection, actor_id, "COC_ELIGIBILITY_CONFIRMED", "claim", claim_id, case_id,
                             after={"eligibility_review_id": review_id, "status": status,
                                    "related_party_status": related, "eligible_debt_paise": debt},
                             title=f"CoC eligibility confirmed for {candidate['claim_number']}")
        self._emit(case_id, "COC_ELIGIBILITY_CONFIRMED", actor_id, review_id,
                   metadata={"claim_id": claim_id, "status": status})
        with self.store.connect() as connection:
            return dict(connection.execute("SELECT * FROM coc_eligibility_reviews WHERE id=?", (review_id,)).fetchone())

    @staticmethod
    def _allocate_percentages(rows: List[Dict[str, Any]], precision: int) -> List[Dict[str, Any]]:
        total = sum(int(row["eligible_debt_paise"]) for row in rows)
        if total <= 0:
            raise ValueError("Total admitted eligible financial debt must be greater than zero")
        scale = 10 ** precision
        target_units = 100 * scale
        allocated = 0
        working = []
        for row in rows:
            raw = (Decimal(int(row["eligible_debt_paise"])) * Decimal(100)) / Decimal(total)
            units_exact = raw * scale
            units = int(units_exact.to_integral_value(rounding=ROUND_DOWN))
            allocated += units
            working.append({**row, "raw": raw, "units": units, "remainder": units_exact - units})
        remainder_units = target_units - allocated
        for row in sorted(working, key=lambda item: (-item["remainder"], item["claim_id"]))[:remainder_units]:
            row["units"] += 1
        for row in working:
            row["raw_percentage"] = format(row.pop("raw"), ".16f")
            row["display_percentage"] = format(Decimal(row.pop("units")) / scale, f".{precision}f")
            row.pop("remainder")
        return working

    def calculate_voting(self, case_id: str, actor_id: str, precision: int = 4) -> Dict[str, Any]:
        if precision < 0 or precision > 8:
            raise ValueError("Display precision must be between 0 and 8 decimal places")
        candidates = self.coc_candidates(case_id)
        unresolved = [row["claim_number"] for row in candidates if row["eligibility_status"] == "REVIEW_REQUIRED"]
        if unresolved:
            raise ValueError(f"COC_ELIGIBILITY_REVIEW_REQUIRED: {', '.join(unresolved)}")
        eligible = [row for row in candidates if row["eligibility_status"] == "ELIGIBLE"]
        if any(row["related_party_status"] == "UNKNOWN" for row in eligible):
            raise ValueError("RELATED_PARTY_REVIEW_REQUIRED")
        with self.store.connect() as connection:
            source = []
            for row in eligible:
                review = connection.execute("SELECT * FROM coc_eligibility_reviews WHERE id=?", (row["eligibility_review_id"],)).fetchone()
                source.append({**dict(review), "creditor_name": row["creditor_name"]})
        allocated = self._allocate_percentages(source, precision)
        fingerprint = _fingerprint([{"review": row["id"], "debt": row["eligible_debt_paise"], "precision": precision} for row in allocated])
        now, calculation_id = utc_now(), new_id()
        with self.store.transaction() as connection:
            existing = connection.execute(
                "SELECT id FROM coc_voting_calculations WHERE case_id=? AND source_fingerprint=?", (case_id, fingerprint)
            ).fetchone()
            if existing:
                result = self.get_voting_calculation(case_id, existing["id"])
                result["idempotent_replay"] = True
                return result
            version = int(connection.execute(
                "SELECT COALESCE(MAX(calculation_version),0)+1 FROM coc_voting_calculations WHERE case_id=?", (case_id,)
            ).fetchone()[0])
            total = sum(int(row["eligible_debt_paise"]) for row in allocated)
            connection.execute(
                """INSERT INTO coc_voting_calculations
                (id,case_id,calculation_version,total_eligible_debt_paise,display_precision,source_fingerprint,
                 status,calculated_by,calculated_at) VALUES (?,?,?,?,?,?,'CALCULATED',?,?)""",
                (calculation_id, case_id, version, total, precision, fingerprint, actor_id, now),
            )
            for row in allocated:
                claim = connection.execute("SELECT creditor_name FROM claims WHERE id=? AND case_id=?", (row["claim_id"], case_id)).fetchone()
                connection.execute(
                    """INSERT INTO coc_voting_calculation_rows
                    (id,calculation_id,case_id,claim_id,decision_id,eligibility_review_id,creditor_name,
                     admitted_debt_paise,raw_percentage,display_percentage) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                    (new_id(), calculation_id, case_id, row["claim_id"], row["decision_id"], row["id"],
                     claim["creditor_name"], row["eligible_debt_paise"], row["raw_percentage"],
                     row["display_percentage"]),
                )
            self.store.audit(connection, actor_id, "COC_VOTING_SHARE_CALCULATED", "coc_voting_calculation",
                             calculation_id, case_id, after={"version": version, "total_debt_paise": total,
                             "members": len(allocated)}, title=f"CoC voting calculation Version {version} created")
        self._emit(case_id, "COC_VOTING_SHARE_CALCULATED", actor_id, calculation_id,
                   metadata={"calculation_version": version})
        return self.get_voting_calculation(case_id, calculation_id)

    def get_voting_calculation(self, case_id: str, calculation_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM coc_voting_calculations WHERE id=? AND case_id=?", (calculation_id, case_id)
            ).fetchone()
            if not row:
                raise KeyError("Voting calculation not found")
            result = self._money_fields(dict(row))
            result["rows"] = [self._money_fields(dict(item)) for item in connection.execute(
                "SELECT * FROM coc_voting_calculation_rows WHERE calculation_id=? AND case_id=? ORDER BY creditor_name",
                (calculation_id, case_id),
            ).fetchall()]
            result["display_total"] = format(sum(Decimal(item["display_percentage"]) for item in result["rows"]),
                                               f".{result['display_precision']}f")
            return result

    def constitution_preview(self, case_id: str, calculation_id: Optional[str] = None) -> Dict[str, Any]:
        candidates = self.coc_candidates(case_id)
        current_loc = self.current_loc(case_id)
        errors = []
        if not candidates:
            errors.append("NO_ADMITTED_FINANCIAL_CREDITOR")
        if any(row["related_party_status"] == "UNKNOWN" for row in candidates):
            errors.append("RELATED_PARTY_REVIEW_REQUIRED")
        if any(row["eligibility_status"] == "REVIEW_REQUIRED" for row in candidates):
            errors.append("COC_ELIGIBILITY_REVIEW_REQUIRED")
        with self.store.connect() as connection:
            undecided_fc = int(connection.execute(
                """SELECT COUNT(*) FROM claims c WHERE c.case_id=? AND c.archived_at IS NULL
                AND c.creditor_category='FINANCIAL_CREDITOR' AND NOT EXISTS (
                    SELECT 1 FROM claim_decisions d WHERE d.case_id=c.case_id AND d.claim_id=c.id
                    AND d.decision_status IN ('ADMITTED','PARTLY_ADMITTED','NOT_ADMITTED','REJECTED'))""",
                (case_id,),
            ).fetchone()[0])
            snapshot = connection.execute(
                "SELECT * FROM list_of_creditors_snapshots WHERE case_id=? ORDER BY version_number DESC LIMIT 1", (case_id,)
            ).fetchone()
            calculation = connection.execute(
                """SELECT * FROM coc_voting_calculations WHERE case_id=? AND (? IS NULL OR id=?)
                ORDER BY calculation_version DESC LIMIT 1""", (case_id, calculation_id, calculation_id)
            ).fetchone()
            snapshot_rows = connection.execute(
                "SELECT claim_id,claim_revision,decision_id FROM list_of_creditors_snapshot_rows WHERE snapshot_id=?",
                (snapshot["id"],),
            ).fetchall() if snapshot else []
            calculation_rows = connection.execute(
                "SELECT claim_id,decision_id,eligibility_review_id,display_percentage FROM coc_voting_calculation_rows WHERE calculation_id=?",
                (calculation["id"],),
            ).fetchall() if calculation else []
        if undecided_fc:
            errors.append("FINANCIAL_CREDITOR_CLAIMS_UNDECIDED")
        if not snapshot:
            errors.append("LIST_OF_CREDITORS_SNAPSHOT_REQUIRED")
        else:
            current_sources = {(row["claim_id"], int(row["claim_revision"]), row.get("decision_id")) for row in current_loc["rows"]}
            snapshot_sources = {(row["claim_id"], int(row["claim_revision"]), row["decision_id"]) for row in snapshot_rows}
            if current_sources != snapshot_sources:
                errors.append("LIST_OF_CREDITORS_SNAPSHOT_STALE")
        if not calculation:
            errors.append("VOTING_CALCULATION_REQUIRED")
        elif int(calculation["total_eligible_debt_paise"]) <= 0:
            errors.append("ADMITTED_ELIGIBLE_DEBT_REQUIRED")
        else:
            current_voting_sources = {(row["claim_id"], row["decision_id"], row["eligibility_review_id"])
                                      for row in candidates if row["eligibility_status"] == "ELIGIBLE"}
            stored_voting_sources = {(row["claim_id"], row["decision_id"], row["eligibility_review_id"])
                                     for row in calculation_rows}
            if current_voting_sources != stored_voting_sources:
                errors.append("VOTING_CALCULATION_STALE")
            display_total = sum(Decimal(row["display_percentage"]) for row in calculation_rows)
            if display_total != Decimal("100"):
                errors.append("VOTING_TOTAL_INVALID")
        return {
            "case_id": case_id, "valid": not errors, "errors": errors,
            "loc_snapshot_id": snapshot["id"] if snapshot else None,
            "voting_calculation_id": calculation["id"] if calculation else None,
            "candidate_count": len(candidates),
        }

    def confirm_constitution(self, case_id: str, payload: Dict[str, Any], actor_id: str,
                             reconstitution: bool = False) -> Dict[str, Any]:
        constitution_date = _iso_date(payload.get("constitution_date"), "Constitution date")
        preview = self.constitution_preview(case_id, payload.get("voting_calculation_id"))
        if not preview["valid"]:
            raise ValueError("; ".join(preview["errors"]))
        now, constitution_id = utc_now(), new_id()
        with self.store.transaction() as connection:
            latest = connection.execute(
                "SELECT * FROM coc_constitutions WHERE case_id=? ORDER BY constitution_version DESC LIMIT 1", (case_id,)
            ).fetchone()
            if latest and not reconstitution:
                raise ValueError("A CoC already exists; use the reconstitution endpoint")
            if reconstitution and not latest:
                raise ValueError("No existing CoC is available to reconstitute")
            if reconstitution and not latest["review_required"]:
                raise ValueError("COC_REVIEW_REQUIRED event is required before reconstitution")
            version = int(latest["constitution_version"] + 1) if latest else 1
            calculation_id = preview["voting_calculation_id"]
            calculation = connection.execute(
                "SELECT * FROM coc_voting_calculations WHERE id=? AND case_id=?", (calculation_id, case_id)
            ).fetchone()
            rows = connection.execute(
                "SELECT * FROM coc_voting_calculation_rows WHERE calculation_id=? AND case_id=? ORDER BY creditor_name",
                (calculation_id, case_id),
            ).fetchall()
            connection.execute(
                """INSERT INTO coc_constitutions
                (id,case_id,constitution_version,constitution_date,constitution_type,status,parent_constitution_id,
                 loc_snapshot_id,voting_calculation_id,confirmed_by,confirmed_at,created_at)
                VALUES (?,?,?,?,?,'CONFIRMED',?,?,?,?,?,?)""",
                (constitution_id, case_id, version, constitution_date, "RECONSTITUTION" if latest else "INITIAL",
                 latest["id"] if latest else None, preview["loc_snapshot_id"], calculation_id,
                 actor_id, now, now),
            )
            connection.execute(
                "UPDATE coc_voting_calculations SET constitution_id=?,status='CONFIRMED' WHERE id=?",
                (constitution_id, calculation_id),
            )
            # Reuse the operational membership table while retiring any manual
            # or prior current rows; the new active rows are claim-derived.
            connection.execute("UPDATE coc_members SET valid_to=? WHERE case_id=? AND valid_to IS NULL", (constitution_date, case_id))
            for row in rows:
                eligibility = connection.execute("SELECT * FROM coc_eligibility_reviews WHERE id=?", (row["eligibility_review_id"],)).fetchone()
                claim = connection.execute("SELECT * FROM claims WHERE id=? AND case_id=?", (row["claim_id"], case_id)).fetchone()
                connection.execute(
                    """INSERT INTO coc_constitution_members
                    (id,constitution_id,case_id,claim_id,decision_id,eligibility_review_id,creditor_name,
                     admitted_debt_paise,related_party_status,eligibility_status,raw_voting_percentage,
                     display_voting_percentage) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (new_id(), constitution_id, case_id, row["claim_id"], row["decision_id"],
                     row["eligibility_review_id"], row["creditor_name"], row["admitted_debt_paise"],
                     eligibility["related_party_status"], eligibility["eligibility_status"],
                     row["raw_percentage"], row["display_percentage"]),
                )
                connection.execute(
                    """INSERT INTO coc_members
                    (id,case_id,claim_id,contact_id,admitted_debt,voting_share,valid_from,valid_to,
                     authorized_representative,created_by,updated_by,created_at,updated_at)
                    VALUES (?,?,?,?,?,?,?,NULL,?,?,?,?,?)""",
                    (new_id(), case_id, row["claim_id"], claim["claimant_contact_id"],
                     float(Decimal(row["admitted_debt_paise"]) / 100), float(row["display_percentage"]),
                     constitution_date, claim["authorized_representative"], actor_id, actor_id, now, now),
                )
            action = "COC_RECONSTITUTED" if latest else "COC_CONSTITUTED"
            self.store.audit(connection, actor_id, action, "coc_constitution", constitution_id, case_id,
                             after={"constitution_version": version, "constitution_date": constitution_date,
                                    "members": len(rows), "loc_snapshot_id": preview["loc_snapshot_id"],
                                    "voting_calculation_id": calculation_id},
                             title=f"CoC Constitution Version {version} confirmed")
        event_type = "COC_RECONSTITUTED" if reconstitution else "COC_CONSTITUTED"
        self._emit(case_id, event_type, actor_id, constitution_id, constitution_date,
                   {"constitution_version": version})
        return self.get_constitution(case_id, constitution_id)

    def get_constitution(self, case_id: str, constitution_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute(
                "SELECT * FROM coc_constitutions WHERE id=? AND case_id=?", (constitution_id, case_id)
            ).fetchone()
            if not row:
                raise KeyError("CoC Constitution not found")
            result = dict(row)
            result["members"] = [self._money_fields(dict(item)) for item in connection.execute(
                "SELECT * FROM coc_constitution_members WHERE constitution_id=? AND case_id=? ORDER BY creditor_name",
                (constitution_id, case_id),
            ).fetchall()]
            return result

    def list_constitutions(self, case_id: str) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            rows = connection.execute(
                "SELECT id FROM coc_constitutions WHERE case_id=? ORDER BY constitution_version DESC", (case_id,)
            ).fetchall()
        return [self.get_constitution(case_id, row["id"]) for row in rows]

    def link_constitution_report(self, case_id: str, constitution_id: str, document_id: str,
                                 status: str, actor_id: str) -> Dict[str, Any]:
        report_status = str(status or "DRAFT").upper()
        if report_status not in {"DRAFT", "FINAL"}:
            raise ValueError("Constitution Report status must be DRAFT or FINAL")
        with self.store.transaction() as connection:
            constitution = connection.execute(
                "SELECT * FROM coc_constitutions WHERE id=? AND case_id=?", (constitution_id, case_id)
            ).fetchone()
            document = connection.execute(
                "SELECT * FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (document_id, case_id)
            ).fetchone()
            if not constitution or not document:
                raise KeyError("Constitution or report document not found")
            connection.execute(
                "UPDATE coc_constitutions SET report_status=?,report_document_id=? WHERE id=?",
                (report_status, document_id, constitution_id),
            )
            self.store.audit(connection, actor_id, f"COC_CONSTITUTION_REPORT_{'FINALIZED' if report_status == 'FINAL' else 'GENERATED'}",
                             "coc_constitution", constitution_id, case_id,
                             after={"document_id": document_id, "status": report_status},
                             title=f"CoC Constitution Report {report_status.lower()}")
        event_type = "COC_CONSTITUTION_REPORT_FINALIZED" if report_status == "FINAL" else "COC_CONSTITUTION_REPORT_GENERATED"
        self._emit(case_id, event_type, actor_id, document_id,
                   metadata={"constitution_id": constitution_id})
        return self.get_constitution(case_id, constitution_id)

    def upsert_creditor_class(self, case_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        name = str(payload.get("class_name") or "").strip()
        if not name:
            raise ValueError("Class name is required")
        claim_ids = list(dict.fromkeys(payload.get("claim_ids") or []))
        now = utc_now()
        with self.store.transaction() as connection:
            self.store.ensure_case(connection, case_id)
            for claim_id in claim_ids:
                claim = connection.execute("SELECT 1 FROM claims WHERE id=? AND case_id=? AND archived_at IS NULL", (claim_id, case_id)).fetchone()
                if not claim:
                    raise ValueError("Every class member Claim must belong to this case")
            existing = connection.execute("SELECT * FROM creditor_classes WHERE case_id=? AND class_name=?", (case_id, name)).fetchone()
            class_id = existing["id"] if existing else new_id()
            if existing:
                connection.execute("UPDATE creditor_classes SET class_type=?,status=?,ar_required=?,notes=?,updated_at=? WHERE id=?",
                                   (payload.get("class_type", existing["class_type"]), payload.get("status", existing["status"]),
                                    int(bool(payload.get("ar_required", existing["ar_required"]))), payload.get("notes", existing["notes"]), now, class_id))
                connection.execute("DELETE FROM creditor_class_claims WHERE class_id=?", (class_id,))
            else:
                connection.execute("""INSERT INTO creditor_classes(id,case_id,class_name,class_type,status,ar_required,notes,created_by,created_at,updated_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?)""", (class_id, case_id, name, payload.get("class_type", "OTHER"),
                                   payload.get("status", "REVIEW_REQUIRED"), int(bool(payload.get("ar_required"))), payload.get("notes", ""), actor_id, now, now))
            for claim_id in claim_ids:
                connection.execute("INSERT INTO creditor_class_claims(class_id,claim_id,case_id) VALUES (?,?,?)", (class_id, claim_id, case_id))
        if claim_ids:
            self._emit(case_id, "CREDITORS_IN_CLASS_CONFIRMED", actor_id, class_id,
                       metadata={"creditor_count": len(claim_ids), "class_name": name})
        if bool(payload.get("ar_required")):
            self._emit(case_id, "AR_REQUIRED", actor_id, class_id,
                       metadata={"class_name": name})
        return self.get_creditor_class(case_id, class_id)

    def get_creditor_class(self, case_id: str, class_id: str) -> Dict[str, Any]:
        with self.store.connect() as connection:
            row = connection.execute("SELECT * FROM creditor_classes WHERE id=? AND case_id=?", (class_id, case_id)).fetchone()
            if not row:
                raise KeyError("Creditor class not found")
            result = dict(row)
            result["claim_ids"] = [item["claim_id"] for item in connection.execute(
                "SELECT claim_id FROM creditor_class_claims WHERE class_id=? AND case_id=? ORDER BY claim_id", (class_id, case_id)
            ).fetchall()]
            result["creditor_count"] = len(result["claim_ids"])
            result["ar_process"] = self.get_ar_process(connection, case_id, class_id)
            return result

    def list_creditor_classes(self, case_id: str) -> List[Dict[str, Any]]:
        with self.store.connect() as connection:
            self.store.ensure_case(connection, case_id)
            ids = [row["id"] for row in connection.execute("SELECT id FROM creditor_classes WHERE case_id=? ORDER BY class_name", (case_id,)).fetchall()]
        return [self.get_creditor_class(case_id, class_id) for class_id in ids]

    @staticmethod
    def get_ar_process(connection: sqlite3.Connection, case_id: str, class_id: str) -> Optional[Dict[str, Any]]:
        row = connection.execute("SELECT * FROM authorised_representative_processes WHERE case_id=? AND class_id=?", (case_id, class_id)).fetchone()
        return dict(row) if row else None

    def upsert_ar_process(self, case_id: str, class_id: str, payload: Dict[str, Any], actor_id: str) -> Dict[str, Any]:
        now = utc_now()
        with self.store.transaction() as connection:
            class_row = connection.execute("SELECT * FROM creditor_classes WHERE id=? AND case_id=?", (class_id, case_id)).fetchone()
            if not class_row:
                raise KeyError("Creditor class not found")
            existing = connection.execute("SELECT * FROM authorised_representative_processes WHERE case_id=? AND class_id=?", (case_id, class_id)).fetchone()
            process_id = existing["id"] if existing else new_id()
            values = {
                "requirement_status": payload.get("requirement_status", "REVIEW_REQUIRED"),
                "candidate_name": payload.get("candidate_name", ""), "selected_ar": payload.get("selected_ar", ""),
                "status": payload.get("status", "NOT_STARTED"), "filing_requirement": payload.get("filing_requirement", ""),
                "evidence_document_id": payload.get("evidence_document_id"),
            }
            if values["evidence_document_id"] and not connection.execute(
                "SELECT 1 FROM documents WHERE id=? AND case_id=? AND archived_at IS NULL", (values["evidence_document_id"], case_id)
            ).fetchone():
                raise ValueError("AR evidence document does not belong to this case")
            if existing:
                connection.execute("""UPDATE authorised_representative_processes SET requirement_status=?,candidate_name=?,selected_ar=?,status=?,
                                   filing_requirement=?,evidence_document_id=?,updated_by=?,updated_at=? WHERE id=?""",
                                   (*values.values(), actor_id, now, process_id))
            else:
                connection.execute("""INSERT INTO authorised_representative_processes
                                   (id,case_id,class_id,requirement_status,candidate_name,selected_ar,status,filing_requirement,
                                    evidence_document_id,created_by,updated_by,created_at,updated_at)
                                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                                   (process_id, case_id, class_id, *values.values(), actor_id, actor_id, now, now))
            return dict(connection.execute("SELECT * FROM authorised_representative_processes WHERE id=?", (process_id,)).fetchone())

    def workflow_summary(self, case_id: str) -> Dict[str, Any]:
        current = self.current_loc(case_id)
        with self.store.connect() as connection:
            counts = {row["status"]: row["count"] for row in connection.execute(
                "SELECT status,COUNT(*) AS count FROM claims WHERE case_id=? AND archived_at IS NULL GROUP BY status", (case_id,)
            ).fetchall()}
            loc = connection.execute("SELECT version_number FROM list_of_creditors_snapshots WHERE case_id=? ORDER BY version_number DESC LIMIT 1", (case_id,)).fetchone()
            coc = connection.execute("SELECT constitution_version,status FROM coc_constitutions WHERE case_id=? ORDER BY constitution_version DESC LIMIT 1", (case_id,)).fetchone()
            members = connection.execute("SELECT COUNT(*) FROM coc_members WHERE case_id=? AND valid_to IS NULL AND archived_at IS NULL", (case_id,)).fetchone()[0]
        return {
            "claims_received": sum(counts.values()),
            "claims_under_verification": counts.get("UNDER_VERIFICATION", 0),
            "claims_information_required": counts.get("INFORMATION_REQUIRED", 0),
            "claims_admitted": counts.get("ADMITTED", 0),
            "claims_partly_admitted": counts.get("PARTLY_ADMITTED", 0),
            "claims_not_admitted": counts.get("NOT_ADMITTED", 0) + counts.get("REJECTED", 0),
            "total_amount_claimed": current["totals"]["claimed"],
            "total_amount_admitted": current["totals"]["admitted"],
            "loc_current_version": loc["version_number"] if loc else None,
            "coc_status": coc["status"] if coc else "NOT_CONSTITUTED",
            "coc_member_count": int(members),
            "coc_constitution_version": coc["constitution_version"] if coc else None,
        }
