"""Deterministic local demo for CIRP-057--076 backend controls.

Run from ``backend``: ``venv\\Scripts\\python.exe demo_phase4.py``.
It writes only a disposable SQLite database under ``.pytest_tmp`` unless a
different path is passed with ``--database``.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from database import CasefileDatabase
from phase4_core import Phase4Core
from workflow import WorkflowService


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", default="../.pytest_tmp/phase4-demo.db")
    args = parser.parse_args()
    path = Path(args.database); path.parent.mkdir(parents=True, exist_ok=True); path.unlink(missing_ok=True)
    store = CasefileDatabase(path)
    actor = "phase4-demo-admin"
    store.ensure_admin(actor, "phase4-demo@example.test", "Phase 4 Demo", "not-used")
    case = store.create_case({"name": "Phase 4 Demo Corporate Debtor", "process_type": "CIRP", "commencement_date": "2026-08-29"}, actor)
    core = Phase4Core(store)

    core.create_going_concern(case["id"], {"assessment_date": "2026-08-29", "operational_status": "OPERATING", "estimated_income": "250000.00", "estimated_expenditure": "175000.00"}, actor)
    core.create_cash_flow(case["id"], {"period_from": "2026-08-29", "period_to": "2026-09-30", "opening_cash": "10000", "opening_bank": "50000", "expected_receipts": "100000", "expected_payments": "80000"}, actor)
    receivable = core.create_receivable(case["id"], {"invoice_reference": "DEMO-INV-001", "opening_amount": "35000.00"}, actor)
    core.receivable_activity(case["id"], receivable["id"], {"activity_type": "FOLLOW_UP", "idempotency_key": "demo-followup"}, actor)
    req = core.create_requisition(case["id"], {"reference": "DEMO-REQ-001", "requested_date": "2026-08-29"}, actor)
    core.requisition_item(case["id"], req["id"], {"item_key": "books", "status": "NO_RESPONSE"}, actor)
    application = core.create_section19(case["id"], {"professional_confirmed": True}, actor)

    appointment = core.valuation_record(case["id"], "APPOINTMENT", {"asset_class": "LAND_BUILDING", "status": "ISSUED"}, actor)
    declaration = core.valuation_record(case["id"], "DECLARATION", {"parent_record_id": appointment["id"], "status": "RECEIVED"}, actor)
    core.verify_valuer_declaration(case["id"], declaration["id"], actor)
    assignment = core.valuation_record(case["id"], "ASSIGNMENT", {"appointment_id": appointment["id"], "declaration_id": declaration["id"]}, actor)
    core.activate_assignment(case["id"], assignment["id"], actor)
    im = core.initialize_im(case["id"], actor)
    workspace = core.create(case["id"], "vdr", "WORKSPACE", {"status": "DRAFT"}, actor)
    recipient = core.create(case["id"], "vdr", "RECIPIENT", {"data": {"name": "Demo PRA"}}, actor)
    undertaking = core.undertaking(case["id"], {"recipient_type": "PRA", "recipient_name": "Demo PRA"}, actor)
    core.verify_undertaking(case["id"], undertaking["id"], actor)
    core.grant_vdr_access(case["id"], workspace["id"], recipient["id"], {"undertaking_id": undertaking["id"], "folder_scope": ["IM"]}, actor)

    print(json.dumps({"case_id": case["id"], "section19_draft": application["id"], "im_workspace": im["id"], "workflow_step_count": len(WorkflowService(store).definitions()), "summary": core.workflow_summary(case["id"])}, indent=2))


if __name__ == "__main__":
    main()
