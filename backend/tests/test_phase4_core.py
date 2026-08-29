"""CIRP-057--076 operational control regression tests."""

import pytest

from phase4_core import Phase4Core
from workflow import WorkflowService
from test_claims_coc_phase2 import ACTOR, build_standard_coc


def _core(tmp_path):
    store, case, *_ = build_standard_coc(tmp_path)
    return store, case, Phase4Core(store)


def test_operations_registers_are_case_isolated_and_money_is_exact(tmp_path):
    _, case, core = _core(tmp_path)
    going = core.create_going_concern(case["id"], {
        "assessment_date": "2026-08-29", "operational_status": "OPERATING",
        "estimated_income": "100.10", "estimated_expenditure": "25.25",
    }, ACTOR)
    assert going["data"]["estimated_net_cash_flow_paise"] == 7485
    cash = core.create_cash_flow(case["id"], {
        "period_from": "2026-08-01", "period_to": "2026-08-31",
        "opening_cash": "10", "opening_bank": "20", "actual_receipts": "5.55", "actual_payments": "4.40",
    }, ACTOR)
    assert cash["data"]["actual_net_cash_paise"] == 3115
    receivable = core.create_receivable(case["id"], {"invoice_reference": "INV-1", "opening_amount": "10.05"}, ACTOR)
    receivable = core.receivable_activity(case["id"], receivable["id"], {"activity_type": "COLLECTION", "amount": "0.10", "idempotency_key": "paid-1"}, ACTOR)
    assert receivable["data"]["current_outstanding_paise"] == 995
    assert core.workflow_summary(case["id"])["outstanding_receivables_amount"] == "9.95"


def test_section19_requires_confirmation_and_uses_recorded_chronology(tmp_path):
    _, case, core = _core(tmp_path)
    request = core.create_requisition(case["id"], {"reference": "REQ-1", "requested_date": "2026-08-01"}, ACTOR)
    core.requisition_item(case["id"], request["id"], {"item_key": "books", "status": "NO_RESPONSE"}, ACTOR)
    with pytest.raises(ValueError, match="Professional confirmation"):
        core.create_section19(case["id"], {}, ACTOR)
    application = core.create_section19(case["id"], {"professional_confirmed": True}, ACTOR)
    assert application["data"]["chronology"][0]["requisition_id"] == request["id"]
    with pytest.raises(ValueError, match="Professional confirmation"):
        core.file_section19(case["id"], application["id"], {}, ACTOR)


def test_valuer_declaration_gate_im_and_vdr_access(tmp_path):
    _, case, core = _core(tmp_path)
    appointment = core.valuation_record(case["id"], "APPOINTMENT", {"asset_class": "LAND_BUILDING", "status": "ISSUED"}, ACTOR)
    declaration = core.valuation_record(case["id"], "DECLARATION", {"parent_record_id": appointment["id"], "status": "RECEIVED"}, ACTOR)
    assignment = core.valuation_record(case["id"], "ASSIGNMENT", {"appointment_id": appointment["id"], "declaration_id": declaration["id"]}, ACTOR)
    with pytest.raises(ValueError, match="verified declaration"):
        core.activate_assignment(case["id"], assignment["id"], ACTOR)
    core.verify_valuer_declaration(case["id"], declaration["id"], ACTOR)
    assert core.activate_assignment(case["id"], assignment["id"], ACTOR)["status"] == "ACTIVE"
    im = core.initialize_im(case["id"], ACTOR)
    assert len(im["items"]) == len(core.IM_SECTIONS)
    workspace = core.create(case["id"], "vdr", "WORKSPACE", {"status": "DRAFT"}, ACTOR)
    recipient = core.create(case["id"], "vdr", "RECIPIENT", {"data": {"name": "Test PRA"}}, ACTOR)
    undertaking = core.undertaking(case["id"], {"recipient_type": "PRA", "recipient_name": "Test PRA"}, ACTOR)
    with pytest.raises(ValueError, match="verified confidentiality"):
        core.grant_vdr_access(case["id"], workspace["id"], recipient["id"], {"undertaking_id": undertaking["id"]}, ACTOR)
    core.verify_undertaking(case["id"], undertaking["id"], ACTOR)
    core.grant_vdr_access(case["id"], workspace["id"], recipient["id"], {"undertaking_id": undertaking["id"], "folder_scope": ["IM"]}, ACTOR)
    assert core.can_access_vdr(case["id"], workspace["id"], recipient["id"])
    core.revoke_vdr_access(case["id"], workspace["id"], recipient["id"], ACTOR, "Test complete")
    assert not core.can_access_vdr(case["id"], workspace["id"], recipient["id"])


def test_workflow_seed_contains_through_cirp_076(tmp_path):
    store, case, _ = _core(tmp_path)
    definitions = WorkflowService(store).definitions()
    assert {"CIRP-057", "CIRP-068", "CIRP-076"}.issubset({item["step_code"] for item in definitions})
    assert WorkflowService(store).summary(case["id"])["vdr_authorized_recipients"] == 0
