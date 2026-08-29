"""Deterministic synthetic demonstration for CIRP-077 through CIRP-084.

This is intentionally executable only against a temporary database.  It does
not import office-reference or client data.
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from database import CasefileDatabase
from transaction_audit_core import TransactionAuditCore


ACTOR = "demo-professional"


def run_demo() -> dict:
    with tempfile.TemporaryDirectory(prefix="casefile-phase5-demo-") as directory:
        store = CasefileDatabase(Path(directory) / "demo.db")
        store.ensure_admin(ACTOR, "demo@example.test", "Demo Professional", "unused")
        case = store.create_case({"name": "Demo Manufacturing Limited", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
        core = TransactionAuditCore(store)
        auditor = store.create_contact({"name": "ABC & Co.", "email": "abc@example.test"}, ACTOR, case["id"])
        engagement = core.engagement(case["id"], {"auditor_contact_id": auditor["id"], "status": "ACTIVE", "idempotency_key": "demo-engagement"}, ACTOR)
        core.scope(case["id"], engagement["id"], {"review_type": "PREFERENTIAL", "period_from": "2024-08-01", "period_to": "2026-08-01", "status": "CONFIRMED", "professional_confirmed": True}, ACTOR)
        for position, status in enumerate(["RECEIVED"] * 10 + ["PARTLY_RECEIVED"] * 3 + ["NOT_AVAILABLE", "FOLLOW_UP_REQUIRED"], 1):
            requirement = core.document_requirement(case["id"], engagement["id"], {"category": "SYNTHETIC", "description": f"Synthetic requirement {position}"}, ACTOR)
            core.requirement_status(case["id"], requirement["id"], {"status": status}, ACTOR)
        for name, status in (("Bank A", "COMPLETE"), ("Bank B", "PARTIAL"), ("Bank C", "MISSING")):
            core.bank_coverage(case["id"], engagement["id"], {"bank_name": name, "coverage_status": status}, ACTOR)
        for position, status in enumerate(("CONFIRMED", "CONFIRMED", "CONFIRMED", "REVIEW_REQUIRED"), 1):
            contact = store.create_contact({"name": f"Related Party {position}"}, ACTOR, case["id"])
            relation = core.related_party(case["id"], {"contact_id": contact["id"], "relationship_type": "DIRECTOR", "relationship_description": "Synthetic demo relation"}, ACTOR)
            core.confirm_related_party(case["id"], relation["id"], status, ACTOR)
        preference = core.workstream(case["id"], engagement["id"], {"review_type": "PREFERENTIAL", "status": "FINDINGS_RECORDED"}, ACTOR)
        no_finding = core.workstream(case["id"], engagement["id"], {"review_type": "UNDERVALUE"}, ACTOR)
        core.complete_workstream(case["id"], no_finding["id"], {"status": "REVIEW_COMPLETE_NO_FINDING", "professional_confirmed": True}, ACTOR)
        for review_type, status in (("EXTORTIONATE", "DATA_PENDING"), ("FRAUDULENT_WRONGFUL", "IN_PROGRESS"), ("RELATED_PARTY", "IN_PROGRESS")):
            core.workstream(case["id"], engagement["id"], {"review_type": review_type, "status": status}, ACTOR)
        ta1 = core.finding(case["id"], engagement["id"], {"finding_number": "TA-001", "workstream_id": preference["id"], "auditor_classification": "PREFERENTIAL", "status": "DRAFT", "title": "Potential preferential transaction", "finding_amount": "1000000"}, ACTOR)
        ta1 = core.finalize_finding(case["id"], ta1["id"], ACTOR)
        core.review_finding(case["id"], ta1["id"], "RP", {"status": "ACCEPTED_FOR_LEGAL_REVIEW"}, ACTOR)
        core.review_finding(case["id"], ta1["id"], "LEGAL", {"status": "PENDING"}, ACTOR)
        ta2 = core.finding(case["id"], engagement["id"], {"finding_number": "TA-002", "auditor_classification": "SUSPICIOUS_UNCLASSIFIED", "status": "INFORMATION_REQUIRED", "title": "Suspicious unclassified observation", "finding_amount": "500000"}, ACTOR)
        combined = []
        for number in ("TA-003", "TA-004"):
            finding = core.finding(case["id"], engagement["id"], {"finding_number": number, "auditor_classification": "PREFERENTIAL", "title": f"Combined application {number}"}, ACTOR)
            finding = core.finalize_finding(case["id"], finding["id"], ACTOR)
            core.review_finding(case["id"], finding["id"], "RP", {"status": "ACCEPTED_FOR_LEGAL_REVIEW"}, ACTOR)
            core.review_finding(case["id"], finding["id"], "LEGAL", {"status": "RECOMMEND_FILE"}, ACTOR)
            combined.append(finding)
        decision = core.avoidance_decision(case["id"], engagement["id"], {"finding_ids": [item["id"] for item in combined], "decision": "FILE_APPLICATION", "professional_confirmed": True, "rationale": "Synthetic combined filing"}, ACTOR)
        filing = core.avoidance_application(case["id"], decision["id"], {"status": "FILED", "ia_number": "IA-DEMO-001", "filed_date": "2026-09-01", "next_hearing_date": "2026-10-15"}, ACTOR)
        result = {"case": case["name"], "auditor": auditor["name"], "engagement_status": engagement["status"], "availability": core.availability_summary(case["id"], engagement["id"]), "bank_coverage": {item["bank_name"]: item["coverage_status"] for item in core.list(case["id"], "transaction_audit_bank_coverages", engagement["id"])}, "related_party_statuses": [item["confirmed_status"] for item in core.list(case["id"], "case_related_parties")], "ta_001": {"auditor_status": ta1["status"], "avoidance_decision": "NONE"}, "ta_002": {"classification": ta2["auditor_classification"], "status": ta2["status"]}, "combined_application": {"status": filing["status"], "ia_number": filing["ia_number"], "next_hearing_date": filing["next_hearing_date"], "finding_count": 2}}
        assert result["availability"] == {"requested": 15, "received": 10, "partly_received": 3, "not_available": 1, "follow_up_required": 1, "reviewed": 0}
        assert result["bank_coverage"] == {"Bank A": "COMPLETE", "Bank B": "PARTIAL", "Bank C": "MISSING"}
        assert result["ta_001"]["avoidance_decision"] == "NONE" and result["ta_002"]["classification"] == "SUSPICIOUS_UNCLASSIFIED"
        return result


if __name__ == "__main__":
    print(json.dumps(run_demo(), indent=2))
