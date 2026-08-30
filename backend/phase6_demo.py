"""Synthetic, client-free demonstration of the Phase 6 stage separation."""
from __future__ import annotations

import json
from pathlib import Path
from tempfile import TemporaryDirectory

from database import CasefileDatabase
from phase6_core import Phase6Core


ACTOR = "admin"


def build_demo() -> dict:
    with TemporaryDirectory(prefix="casefile-phase6-") as directory:
        store = CasefileDatabase(Path(directory) / "phase6-demo.db")
        store.ensure_admin(ACTOR, "admin@example.com", "Demo Administrator", "not-used")
        case = store.create_case({"name": "Demo Industries Limited", "process_type": "CIRP"}, ACTOR)
        core = Phase6Core(store)

        process = core._create(case["id"], "EOI_PROCESS", {  # Synthetic fixture setup only.
            "status": "PUBLISHED", "EOI_due_date": "2027-01-15", "deposit_required": True,
            "deposit_amount": "1000000.00", "consortium_allowed": True,
            "template_status": "EOI_EDITABLE_TEMPLATE_REQUIRED",
        }, ACTOR)
        pras = {}
        for key, name, kind in (
            ("alpha", "Alpha Ltd", "COMPANY"), ("beta", "Beta Consortium", "CONSORTIUM"),
            ("gamma", "Gamma Ltd", "COMPANY"), ("delta", "Delta Ltd", "COMPANY"),
        ):
            pra = core.pra(case["id"], {"legal_name": name, "pra_type": kind, "email": f"{key}@example.com"}, ACTOR)
            core._create(case["id"], "EOI_SUBMISSION", {"status": "RECEIVED", "received_at": f"2027-01-1{len(pras)+1}T10:00:00Z"}, ACTOR, process_id=process["id"], pra_id=pra["id"])
            pras[key] = pra

        pra_statuses = {
            "alpha": "ELIGIBLE FINAL", "beta": "ELIGIBLE FINAL",
            "gamma": "INELIGIBLE", "delta": "OBJECTION UNDER REVIEW",
        }
        for key, label in pra_statuses.items():
            core._create(case["id"], "ELIGIBILITY_REVIEW", {"status": label.replace(" ", "_"), "review_stage": "FINAL_LIST"}, ACTOR, process_id=process["id"], pra_id=pras[key]["id"])

        rfrp = core._create(case["id"], "RFRP", {"status": "ISSUED", "version_number": 1, "template_status": "RFRP_TEMPLATE_REQUIRED"}, ACTOR, process_id=process["id"])
        matrix = core._create(case["id"], "EVALUATION_MATRIX", {"status": "APPROVED", "version_number": 1, "template_status": "EVALUATION_MATRIX_TEMPLATE_REQUIRED"}, ACTOR, process_id=process["id"])
        for pra in (pras["alpha"], pras["beta"]):
            core._create(case["id"], "ISSUE_PACKAGE", {"status": "ISSUED", "versions": {"rfrp": 1, "matrix": 1, "im": 2}}, ACTOR, process_id=process["id"], pra_id=pra["id"])
        for number, status in ((1, "ANSWERED"), (2, "ANSWERED"), (3, "OPEN")):
            core._create(case["id"], "PRA_QUERY", {"status": status, "query_number": f"Q-{number}"}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"])

        alpha_v1 = core._create(case["id"], "RESOLUTION_PLAN", {"status": "SUPERSEDED", "version_number": 1}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"])
        alpha_v2 = core._create(case["id"], "RESOLUTION_PLAN", {"status": "COMPLIANT", "version_number": 2, "supersedes_plan_version_id": alpha_v1["id"]}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"])
        beta_v1 = core._create(case["id"], "RESOLUTION_PLAN", {"status": "UNDER_COMPLIANCE_REVIEW", "version_number": 1}, ACTOR, process_id=process["id"], pra_id=pras["beta"]["id"])
        core._create(case["id"], "SECTION30_REVIEW", {"status": "COMPLIANT"}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"])
        core._create(case["id"], "ELIGIBILITY_REVIEW", {"status": "ELIGIBLE", "review_stage": "PRE_COC_VOTE_RECHECK"}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"])
        core._create(case["id"], "PLAN_EVALUATION", {"status": "LOCKED", "total_score": "82.50", "matrix_id": matrix["id"]}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"])
        core._create(case["id"], "SECTION30_REVIEW", {"status": "MORE_INFORMATION_REQUIRED"}, ACTOR, process_id=process["id"], pra_id=pras["beta"]["id"], plan_id=beta_v1["id"])
        core._create(case["id"], "NEGOTIATION_ROUND", {"status": "CLOSED", "version_number": 1}, ACTOR, process_id=process["id"])
        core._create(case["id"], "COC_PLAN_PLACEMENT", {"status": "PLACED", "plan_version": 2}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"])
        vote = core._create(case["id"], "PLAN_VOTE_LINK", {"status": "APPROVED", "plan_version": 2}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"])
        sra = core._create(case["id"], "SUCCESSFUL_RA", {"status": "READY_FOR_NCLT", "selected_plan_version": 2}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"], parent_id=vote["id"])
        core._create(case["id"], "PROCESS_DEPOSIT", {"status": "VERIFIED", "deposit_type": "PERFORMANCE_SECURITY"}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"])
        core._create(case["id"], "PLAN_APPROVAL_WORKSPACE", {"status": "FILED", "next_hearing_at": "2027-03-15", "template_status": "PLAN_APPROVAL_APPLICATION_TEMPLATE_REQUIRED"}, ACTOR, process_id=process["id"], pra_id=pras["alpha"]["id"], plan_id=alpha_v2["id"], parent_id=sra["id"])

        records = core.list(case["id"])
        result = {
            "case": case["name"],
            "EOI_PROCESS": {"status": process["status"], "EOIs_received": 4},
            "PRA_STATUS": {pras[key]["data"]["legal_name"]: status for key, status in pra_statuses.items()},
            "RFRP": "V1 ISSUED", "Evaluation_Matrix": "V1 APPROVED",
            "Issue_Packages": sum(row["record_type"] == "ISSUE_PACKAGE" for row in records),
            "Queries": {"received": 3, "answered": 2, "open": 1},
            "RESOLUTION_PLANS": {
                "Alpha": {"versions": ["V1 received", "V2 revised"], "Section_30": "COMPLIANT", "Final_29A": "ELIGIBLE", "Evaluation": "82.50"},
                "Beta": {"versions": ["V1 received"], "Section_30": "MORE INFORMATION REQUIRED"},
            },
            "NEGOTIATION": "Round 1 complete", "COC": "Alpha V2 placed before CoC",
            "Voting": "APPROVED", "Successful_RA": "Alpha Ltd",
            "Performance_Security": "VERIFIED", "NCLT_Plan_Approval": "FILED",
            "Next_Hearing": "2027-03-15",
            "separation_proof": "PRA eligibility != Plan compliance != Evaluation != CoC selection != NCLT approval",
        }
        assert result["Issue_Packages"] == 2 and rfrp["version_number"] == 1
        return result


if __name__ == "__main__":
    print(json.dumps(build_demo(), indent=2))
