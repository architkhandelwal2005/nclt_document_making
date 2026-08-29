"""CIRP-057--076 operational control regression tests."""

import json

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


def _quotation_payload(requirement_id, **overrides):
    payload = {
        "valuation_requirement_id": requirement_id,
        "corporate_debtor_name": "ABC Limited", "nclt_bench": "Mumbai Bench",
        "cirp_commencement_date": "2026-08-01", "admission_order_date": "2026-08-01",
        "order_received_date": "2026-08-03", "professional_name": "Test RP",
        "ibbi_registration_number": "IBBI/TEST/001", "professional_role": "RP",
        "recipient_name": "Valuer A", "recipient_email": "valuer.a@example.test",
        "process_email": "cirp.abc@example.test", "quotation_due_date": "2026-08-10",
    }
    return payload | overrides


def test_valuer_quotation_template_is_parameterised_not_reference_case_text(tmp_path):
    _, case, core = _core(tmp_path)
    requirement = core.valuation_record(case["id"], "REQUIREMENT", {"status": "CONFIRMED", "asset_classes": ["LAND_BUILDING"]}, ACTOR)
    preview = core.quotation_preview(case["id"], _quotation_payload(requirement["id"]), ACTOR)
    body = preview["data"]["body"]
    for value in ("ABC Limited", "Mumbai Bench", "1 August 2026", "3 August 2026", "Test RP", "Land & Building"):
        assert value in body
    for reference_only in ("KESHAV PROTEINS", "13th April 2026", "21st April 2026", "Securities & Financial Assets and Plant & Machinery"):
        assert reference_only not in body


def test_valuer_quotation_renders_multiple_confirmed_asset_classes(tmp_path):
    _, case, core = _core(tmp_path)
    requirement = core.valuation_record(case["id"], "REQUIREMENT", {"status": "CONFIRMED", "asset_classes": ["PLANT_MACHINERY", "SECURITIES_FINANCIAL_ASSETS"]}, ACTOR)
    preview = core.quotation_preview(case["id"], _quotation_payload(requirement["id"]), ACTOR)
    assert "Plant & Machinery and Securities & Financial Assets" in preview["data"]["body"]


def test_valuer_quotation_issue_is_idempotent_and_records_event(tmp_path):
    store, case, core = _core(tmp_path)
    requirement = core.valuation_record(case["id"], "REQUIREMENT", {"status": "CONFIRMED", "asset_classes": ["LAND_BUILDING"]}, ACTOR)
    preview = core.quotation_preview(case["id"], _quotation_payload(requirement["id"], idempotency_key="quote-a"), ACTOR)
    issued = core.issue_quotation_invitation(case["id"], preview["id"], ACTOR)
    replay = core.issue_quotation_invitation(case["id"], preview["id"], ACTOR)
    assert issued["status"] == "ISSUED" and replay["idempotent_replay"] is True
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM communications WHERE case_id=? AND linked_id=?", (case["id"], preview["id"])).fetchone()[0] == 1
        event = connection.execute("SELECT metadata_json FROM case_events WHERE case_id=? AND event_type='VALUER_QUOTATION_INVITED'", (case["id"],)).fetchone()
        assert event and json.loads(event["metadata_json"])["workflow_step_code"] == "CIRP-068"


def test_valuer_quotation_rejects_a_requirement_from_another_case(tmp_path):
    store, case, core = _core(tmp_path)
    other = store.create_case({"name": "Other Limited", "process_type": "CIRP", "commencement_date": "2026-08-01"}, ACTOR)
    requirement = core.valuation_record(other["id"], "REQUIREMENT", {"status": "CONFIRMED", "asset_classes": ["LAND_BUILDING"]}, ACTOR)
    with pytest.raises(KeyError):
        core.quotation_preview(case["id"], _quotation_payload(requirement["id"]), ACTOR)
