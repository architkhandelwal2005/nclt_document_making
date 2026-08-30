"""Focused synthetic controls for CIRP-085--105."""
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

import server
from coc_meeting_core import CocMeetingCore
from database import CasefileDatabase
from phase4_core import Phase4Core
from phase6_core import Phase6Core
from workflow import WorkflowService
from test_claims_coc_phase2 import ACTOR, build_standard_coc


def _base(tmp_path, name="Demo Industries Limited"):
    store = CasefileDatabase(tmp_path / f"{name.replace(' ', '-')}.db")
    store.ensure_admin(ACTOR, "admin@example.test", "Admin", "unused")
    case = store.create_case({"name": name, "process_type": "CIRP", "commencement_date": "2026-09-01"}, ACTOR)
    return store, case, Phase6Core(store)


def _document(store, case_id, name, classification="RESTRICTED_PRA"):
    return store.create_module_record(case_id, "documents", {
        "name": name, "category": "Synthetic Phase 6", "status": "final",
        "source_type": "test", "storage_path": f"test/{name}",
        "confidentiality_classification": classification,
    }, ACTOR)


def _published_process(store, case, core):
    process = core.eoi_process(case["id"], {
        "EOI_due_date": "2026-10-10", "submission_modes": ["EMAIL", "PHYSICAL"],
        "deposit_required": True, "deposit_type": "EOI_DEPOSIT", "deposit_amount": "1000000",
        "deposit_currency": "INR", "consortium_allowed": True, "late_submission_policy": "RP/CoC review",
    }, ACTOR)
    core.eligibility_criterion(case["id"], process["id"], {
        "criterion_id": "NET-WORTH", "title": "Minimum net worth", "criterion_type": "NUMERIC",
        "operator": ">=", "threshold": "50000000", "currency": "INR", "applies_to": "PRA",
        "supporting_document_requirement": "Audited or approved current financial evidence",
    }, ACTOR)
    core.approve_eoi_process(case["id"], process["id"], {"professional_confirmed": True}, ACTOR)
    final = _document(store, case["id"], "Synthetic EOI.pdf")
    proof = _document(store, case["id"], "Publication proof.pdf")
    return core.publish_eoi_process(case["id"], process["id"], {
        "document_id": final["id"], "publication_proof_document_id": proof["id"], "publication_date": "2026-09-25",
    }, ACTOR)


def _eligible_pra(case, core, process, name="PRA Alpha Ltd", key="alpha"):
    pra = core.pra(case["id"], {"pra_type": "COMPANY", "legal_name": name, "email": f"{key}@example.test", "idempotency_key": f"pra-{key}"}, ACTOR)
    submission = core.eoi_submission(case["id"], process["id"], pra["id"], {
        "submission_channel": "EMAIL", "receipt_reference": f"EOI-{key}", "idempotency_key": f"submission-{key}",
    }, ACTOR)
    review = core.eligibility_review(case["id"], pra["id"], {
        "review_stage": "EOI_INITIAL", "checklist": [
            {"item_key": "29A-A", "section": "29A(a)", "question": "Professional review question", "response": "NO", "evidence": "Synthetic evidence"},
        ],
    }, ACTOR, submission["id"], process["id"])
    review = core.finalize_eligibility(case["id"], review["id"], {"status": "ELIGIBLE", "professional_confirmed": True, "reason": "Synthetic professional conclusion"}, ACTOR)
    return pra, submission, review


def _issued_final_list(case, core, process, entries):
    listing = core.final_list(case["id"], process["id"], {"entries": entries, "professional_confirmed": True}, ACTOR)
    core.approve_list(case["id"], listing["id"], ACTOR)
    return core.issue_list(case["id"], listing["id"], {}, ACTOR)


def _approved_matrix(case, core, process):
    matrix = core.evaluation_matrix(case["id"], process["id"], {"criteria": [
        {"criterion_id": "A", "title": "Deterministic criterion", "scoring_type": "NUMERIC_FORMULA", "maximum_score": "60", "formula": {"mode": "DIRECT"}},
        {"criterion_id": "B", "title": "Professional criterion", "scoring_type": "MANUAL_PROFESSIONAL", "maximum_score": "40"},
    ]}, ACTOR)
    return core.approve_process_document(case["id"], matrix["id"], {"professional_confirmed": True}, ACTOR)


def test_schema_workflow_sequence_and_template_gaps(tmp_path):
    store, case, core = _base(tmp_path)
    definitions = WorkflowService(store).definitions()
    assert [row["step_code"] for row in definitions] == [f"CIRP-{number:03d}" for number in range(1, 106)]
    with store.connect() as connection:
        assert connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0] == 15
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"resolution_process_records", "resolution_process_items", "resolution_process_dispatches", "process_deadline_revisions"} <= tables
    process = core.eoi_process(case["id"], {"deposit_required": False}, ACTOR)
    assert process["data"]["template_status"] == "EOI_EDITABLE_TEMPLATE_REQUIRED"


def test_eoi_process_register_checklist_idempotency_and_immutability(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    assert process["status"] == "PUBLISHED" and process["data"]["deposit_amount"] == "1000000.00"
    assert "Keshav" not in str(process) and "2000000" not in str(process)
    with pytest.raises(ValueError, match="immutable"):
        core.eligibility_criterion(case["id"], process["id"], {"title": "Late criterion", "criterion_type": "TEXT"}, ACTOR)
    pra = core.pra(case["id"], {"pra_type": "COMPANY", "legal_name": "Checklist Applicant", "email": "check@example.test"}, ACTOR)
    first = core.eoi_submission(case["id"], process["id"], pra["id"], {"submission_channel": "EMAIL", "idempotency_key": "same-eoi"}, ACTOR)
    replay = core.eoi_submission(case["id"], process["id"], pra["id"], {"submission_channel": "EMAIL", "idempotency_key": "same-eoi"}, ACTOR)
    assert first["id"] == replay["id"] and replay["idempotent_replay"] is True
    statuses = ["RECEIVED"] * 6 + ["DEFICIENT", "REQUIRED"]
    checklist = core.eoi_checklist(case["id"], first["id"], {"items": [
        {"item_key": f"DOC-{index}", "title": f"Synthetic document {index}", "status": status}
        for index, status in enumerate(statuses, 1)
    ]}, ACTOR)
    summary = core.checklist_summary(case["id"], checklist["id"])
    assert (summary["total"], summary["received"], summary["deficient"], summary["required"]) == (8, 6, 1, 1)


def test_consortium_members_and_unknown_section29a_never_auto_qualify(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    consortium = core.pra(case["id"], {"pra_type": "CONSORTIUM", "legal_name": "Consortium Beta", "email": "beta@example.test"}, ACTOR)
    lead = core.consortium_member(case["id"], consortium["id"], {"member_legal_name": "Lead Member", "lead_member": True, "percentage_holding": "60", "financials_relied_upon": True}, ACTOR)
    member = core.consortium_member(case["id"], consortium["id"], {"member_legal_name": "Member B", "lead_member": False, "percentage_holding": "40", "financials_relied_upon": False}, ACTOR)
    assert {row["data"]["percentage_holding"] for row in core.list(case["id"], "CONSORTIUM_MEMBER", consortium["id"])} == {"60", "40"}
    for participant in (lead, member):
        review = core.eligibility_review(case["id"], participant["id"], {"review_stage": "EOI_INITIAL", "checklist": [{"item_key": "29A", "response": "NO"}]}, ACTOR, process_id=process["id"])
        core.finalize_eligibility(case["id"], review["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)
    review = core.eligibility_review(case["id"], consortium["id"], {"review_stage": "EOI_INITIAL", "checklist": [{"item_key": "CONNECTED", "response": "UNKNOWN", "legal_review_required": True}]}, ACTOR, process_id=process["id"])
    with pytest.raises(ValueError, match="UNKNOWN"):
        core.finalize_eligibility(case["id"], review["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)
    core.eligibility_checklist_item(case["id"], review["id"], {"item_key": "CONNECTED", "response": "NO", "legal_review_required": True, "professional_conclusion": "Reviewed and cleared"}, ACTOR)
    assert core.finalize_eligibility(case["id"], review["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)["status"] == "ELIGIBLE"


def test_provisional_objection_and_final_list_preserve_prior_snapshot(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    alpha, _, alpha_review = _eligible_pra(case, core, process)
    beta = core.pra(case["id"], {"pra_type": "COMPANY", "legal_name": "PRA Beta Ltd", "email": "beta@example.test"}, ACTOR)
    beta_review = core.eligibility_review(case["id"], beta["id"], {"review_stage": "PROVISIONAL_LIST", "checklist": [{"item_key": "29A", "response": "YES"}]}, ACTOR, process_id=process["id"])
    beta_review = core.finalize_eligibility(case["id"], beta_review["id"], {"status": "INELIGIBLE", "professional_confirmed": True, "reason": "Synthetic initial conclusion"}, ACTOR)
    provisional = core.provisional_list(case["id"], process["id"], {"professional_confirmed": True, "entries": [
        {"pra_id": alpha["id"], "review_id": alpha_review["id"], "result": "ELIGIBLE"},
        {"pra_id": beta["id"], "review_id": beta_review["id"], "result": "INELIGIBLE"},
    ]}, ACTOR)
    core.approve_list(case["id"], provisional["id"], ACTOR)
    provisional = core.issue_list(case["id"], provisional["id"], {}, ACTOR)
    objection = core.objection(case["id"], provisional["id"], beta["id"], {"objection_text": "Synthetic evidence supplied"}, ACTOR)
    core.decide_objection(case["id"], objection["id"], {"status": "ACCEPTED", "decision_text": "Fresh review required", "professional_confirmed": True}, ACTOR)
    final_review = core.eligibility_review(case["id"], beta["id"], {"review_stage": "FINAL_LIST", "checklist": [{"item_key": "29A", "response": "NO"}]}, ACTOR, process_id=process["id"])
    final_review = core.finalize_eligibility(case["id"], final_review["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)
    final = _issued_final_list(case, core, process, [
        {"pra_id": alpha["id"], "review_id": alpha_review["id"], "result": "ELIGIBLE"},
        {"pra_id": beta["id"], "review_id": final_review["id"], "result": "ELIGIBLE", "objection_id": objection["id"]},
    ])
    assert [item["status"] for item in core.get(case["id"], provisional["id"])["items"]] == ["ELIGIBLE", "INELIGIBLE"]
    assert [item["status"] for item in final["items"]] == ["ELIGIBLE", "ELIGIBLE"]
    with pytest.raises(ValueError, match="immutable"):
        core.add_item(case["id"], provisional["id"], {"item_key": "tamper", "status": "ELIGIBLE"}, ACTOR)


def test_issue_package_reuses_nda_im_and_vdr_gate(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    pra, _, review = _eligible_pra(case, core, process)
    _issued_final_list(case, core, process, [{"pra_id": pra["id"], "review_id": review["id"], "result": "ELIGIBLE"}])
    rfrp = core.rfrp(case["id"], process["id"], {"plan_submission_deadline": "2026-11-20"}, ACTOR)
    rfrp = core.approve_process_document(case["id"], rfrp["id"], {"professional_confirmed": True}, ACTOR)
    rfrp = core.issue_process_document(case["id"], rfrp["id"], {}, ACTOR)
    assert rfrp["status"] == "ISSUED"
    matrix = _approved_matrix(case, core, process)
    phase4 = Phase4Core(store)
    im_workspace = phase4.initialize_im(case["id"], ACTOR)
    im_document = _document(store, case["id"], "IM V2.pdf", "CONFIDENTIAL_CIRP")
    im = phase4.im_version(case["id"], im_workspace["id"], {"status": "FINAL", "document_id": im_document["id"], "version_label": "IM V2"}, ACTOR)
    vdr = phase4.create(case["id"], "vdr", "WORKSPACE", {"status": "DRAFT"}, ACTOR)
    undertaking = phase4.undertaking(case["id"], {"recipient_type": "PRA", "recipient_name": "PRA Alpha Ltd", "recipient_email": "alpha@example.test", "pra_id": pra["id"]}, ACTOR)
    with pytest.raises(ValueError, match="verified"):
        core.issue_package(case["id"], process["id"], pra["id"], {"rfrp_id": rfrp["id"], "evaluation_matrix_id": matrix["id"], "im_version_id": im["id"], "vdr_workspace_id": vdr["id"], "undertaking_id": undertaking["id"]}, ACTOR)
    undertaking = phase4.verify_undertaking(case["id"], undertaking["id"], ACTOR)
    package = core.issue_package(case["id"], process["id"], pra["id"], {"rfrp_id": rfrp["id"], "evaluation_matrix_id": matrix["id"], "im_version_id": im["id"], "vdr_workspace_id": vdr["id"], "undertaking_id": undertaking["id"], "idempotency_key": "package-alpha"}, ACTOR)
    assert package["status"] == "ISSUED" and package["data"]["versions"] == {"final_list": 1, "rfrp": 1, "evaluation_matrix": 1, "im": 1}
    assert phase4.can_access_vdr(case["id"], vdr["id"], package["data"]["vdr_recipient_id"])


def test_plan_versions_section30_final29a_and_decimal_evaluation(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    pra, _, initial = _eligible_pra(case, core, process)
    _issued_final_list(case, core, process, [{"pra_id": pra["id"], "review_id": initial["id"], "result": "ELIGIBLE"}])
    v1doc = _document(store, case["id"], "Plan Alpha V1.pdf", "RESTRICTED_RESOLUTION_PLAN")
    v2doc = _document(store, case["id"], "Plan Alpha V2.pdf", "RESTRICTED_RESOLUTION_PLAN")
    v1 = core.resolution_plan(case["id"], process["id"], pra["id"], {"document_id": v1doc["id"], "received_at": "2026-11-20T10:00:00Z", "idempotency_key": "plan-v1"}, ACTOR)
    v2 = core.resolution_plan(case["id"], process["id"], pra["id"], {"document_id": v2doc["id"], "received_at": "2026-11-25T10:00:00Z", "idempotency_key": "plan-v2"}, ACTOR)
    replay = core.resolution_plan(case["id"], process["id"], pra["id"], {"document_id": v2doc["id"], "received_at": "2026-11-25T10:00:00Z", "idempotency_key": "plan-v2"}, ACTOR)
    assert replay["id"] == v2["id"] and replay["status"] == "RECEIVED"
    assert (core.get(case["id"], v1["id"])["status"], v2["version_number"], v2["data"]["supersedes_plan_version_id"]) == ("SUPERSEDED", 2, v1["id"])
    section30 = core.section30_review(case["id"], v2["id"], {"items": [
        {"item_key": "CIRP_COSTS", "response": "COMPLIANT", "plan_page": 12},
        {"item_key": "IMPLEMENTATION", "response": "MORE_INFORMATION_REQUIRED", "plan_clause": "14.2"},
    ]}, ACTOR)
    with pytest.raises(ValueError, match="Unresolved"):
        core.finalize_section30(case["id"], section30["id"], {"status": "COMPLIANT", "professional_confirmed": True}, ACTOR)
    core.add_item(case["id"], section30["id"], {"item_type": "SECTION_30_2", "item_key": "IMPLEMENTATION", "response": "COMPLIANT", "status": "COMPLIANT", "plan_clause": "14.2", "reviewer_note": "Clarified"}, ACTOR)
    section30 = core.finalize_section30(case["id"], section30["id"], {"status": "COMPLIANT", "professional_confirmed": True}, ACTOR)
    recheck = core.final_eligibility_recheck(case["id"], v2["id"], {"checklist": [{"item_key": "CURRENT-29A", "response": "NO"}]}, ACTOR)
    recheck = core.finalize_eligibility(case["id"], recheck["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)
    matrix = _approved_matrix(case, core, process)
    evaluation = core.evaluate_plan(case["id"], v2["id"], matrix["id"], {"scores": [
        {"criterion_id": "A", "value": "60"},
        {"criterion_id": "B", "score": "22.50", "authorized_scorer": ACTOR, "reason": "Professional scoring note"},
    ]}, ACTOR)
    assert evaluation["status"] == "LOCKED" and Decimal(evaluation["data"]["total_score"]) == Decimal("82.50")
    assert section30["plan_id"] == v2["id"] and recheck["version_number"] == 1


def test_addendum_deadline_and_negotiation_preserve_chronology(tmp_path):
    store, case, core = _base(tmp_path)
    process = _published_process(store, case, core)
    pra, _, review = _eligible_pra(case, core, process)
    _issued_final_list(case, core, process, [{"pra_id": pra["id"], "review_id": review["id"], "result": "ELIGIBLE"}])
    addendum = core.addendum(case["id"], process["id"], {"applies_to": "EOI", "reason": "Synthetic approved date change", "professional_confirmed": True, "pra_ids": [pra["id"]], "idempotency_key": "addendum-1"}, ACTOR)
    revision = core.deadline_revision(case["id"], process["id"], {"deadline_key": "EOI_due_date", "old_date": "2026-10-10", "new_date": "2026-10-15", "reason": "Approved extension", "approval_source": "Professional test fixture", "effective_date": "2026-10-01", "addendum_record_id": addendum["id"]}, ACTOR)
    assert revision["old_date"] == "2026-10-10" and core.get(case["id"], process["id"])["data"]["EOI_due_date"] == "2026-10-10"
    query = core.pra_query(case["id"], process["id"], pra["id"], {"query_number": "Q-1", "question": "Synthetic private query", "visibility": "PRIVATE_RESPONSE"}, ACTOR)
    core.respond_query(case["id"], query["id"], {"response": "Synthetic reviewed answer"}, ACTOR)
    negotiation = core.negotiation_process(case["id"], process["id"], {"negotiation_type": "REVISED_PLAN_ROUND", "process_rules": "Synthetic CoC-approved rule", "professional_confirmed": True}, ACTOR)
    round_one = core.negotiation_round(case["id"], negotiation["id"], {"start": "2026-12-01", "end": "2026-12-02"}, ACTOR)
    assert core.close_negotiation_round(case["id"], round_one["id"], {}, ACTOR)["status"] == "CLOSED"


def test_case_isolation_and_viewer_access_denial(tmp_path, monkeypatch):
    store, case, core = _base(tmp_path)
    process = core.eoi_process(case["id"], {"deposit_required": False}, ACTOR)
    other = store.create_case({"name": "Other Case Limited", "process_type": "CIRP"}, ACTOR)
    with pytest.raises(KeyError):
        core.eligibility_criterion(other["id"], process["id"], {"title": "Cross case", "criterion_type": "TEXT"}, ACTOR)
    viewer = store.create_user("phase6-viewer@example.com", "Phase 6 Viewer", "viewer", server.ADMIN_PASSWORD_HASH)
    store.set_case_assignments(case["id"], [viewer["id"]], ACTOR)
    monkeypatch.setattr(server, "casefile_store", store)
    with TestClient(server.app) as client:
        login = client.post("/api/auth/login", json={"email": viewer["email"], "password": server.admin_pwd})
        assert login.status_code == 200, login.json()
        response = client.get(f"/api/cases/{case['id']}/resolution-process/summary", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "PHASE6_ACCESS_DENIED"


def test_coc_voting_sra_security_and_plan_approval_reuse_existing_engines(tmp_path):
    store, case, _, _, _, _, _, _, constitution = build_standard_coc(tmp_path)
    core = Phase6Core(store)
    process = _published_process(store, case, core)
    pra, _, initial = _eligible_pra(case, core, process)
    _issued_final_list(case, core, process, [{"pra_id": pra["id"], "review_id": initial["id"], "result": "ELIGIBLE"}])
    plan_doc = _document(store, case["id"], "Selected Plan.pdf", "RESTRICTED_RESOLUTION_PLAN")
    plan = core.resolution_plan(case["id"], process["id"], pra["id"], {"document_id": plan_doc["id"]}, ACTOR)
    section30 = core.section30_review(case["id"], plan["id"], {"items": [{"item_key": "ALL", "response": "COMPLIANT"}]}, ACTOR)
    section30 = core.finalize_section30(case["id"], section30["id"], {"status": "COMPLIANT", "professional_confirmed": True}, ACTOR)
    final29a = core.final_eligibility_recheck(case["id"], plan["id"], {"review_stage": "PRE_COC_VOTE_RECHECK", "checklist": [{"item_key": "CURRENT", "response": "NO"}]}, ACTOR)
    final29a = core.finalize_eligibility(case["id"], final29a["id"], {"status": "ELIGIBLE", "professional_confirmed": True}, ACTOR)
    coc = CocMeetingCore(store)
    meeting = coc.create_meeting(case["id"], {"meeting_type": "SUBSEQUENT_COC", "coc_constitution_id": constitution["id"], "scheduled_start_at": "2026-12-10T10:00:00", "scheduled_end_at": "2026-12-10T12:00:00", "mode": "VIDEO_CONFERENCE"}, ACTOR)
    agenda = coc.create_agenda_version(case["id"], meeting["id"], ACTOR)
    placement = core.place_before_coc(case["id"], plan["id"], {"meeting_id": meeting["id"], "agenda_version_id": agenda["id"], "proposed_resolution_text": "RESOLVED THAT Plan Alpha V1 be approved."}, ACTOR)
    agenda = coc.finalize_agenda(case["id"], meeting["id"], agenda["id"], ACTOR)
    notice = coc.create_notice_draft(case["id"], meeting["id"], {"agenda_version_id": agenda["id"]}, ACTOR)
    notice_doc = _document(store, case["id"], "Plan CoC Notice.docx", "CONFIDENTIAL_CIRP")
    coc.link_notice_document(case["id"], meeting["id"], notice["id"], notice_doc["id"], ACTOR)
    coc.approve_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    coc.issue_notice(case["id"], meeting["id"], notice["id"], ACTOR)
    approval = coc.upsert_approval_rule({"rule_code": "PLAN-TEST-51", "effective_from": "2026-01-01", "minimum_for_voting_share": "51.0000", "status": "CONFIRMED"}, ACTOR)
    resolution = coc.create_resolution(case["id"], meeting["id"], {"agenda_item_id": placement["data"]["agenda_item_id"], "title": "Approve Plan Alpha", "voting_required": True, "approval_rule_id": approval["id"]}, ACTOR)
    coc.place_resolution(case["id"], meeting["id"], resolution["id"], ACTOR)
    session = coc.create_voting_session(case["id"], meeting["id"], {"resolution_ids": [resolution["id"]]}, ACTOR)
    coc.open_voting(case["id"], meeting["id"], session["id"], ACTOR)
    for member in coc.member_snapshot(case["id"], meeting["id"]):
        coc.record_vote(case["id"], meeting["id"], session["id"], resolution["id"], {"meeting_member_snapshot_id": member["id"], "vote": "FOR"}, ACTOR)
    coc.close_voting(case["id"], meeting["id"], session["id"], ACTOR)
    result = coc.calculate_voting_result(case["id"], meeting["id"], session["id"], resolution["id"], ACTOR)
    result = coc.finalize_voting_result(case["id"], meeting["id"], result["id"], ACTOR)
    vote = core.link_plan_vote(case["id"], plan["id"], {"voting_result_id": result["id"]}, ACTOR)
    sra = core.successful_ra(case["id"], plan["id"], {"plan_vote_link_id": vote["id"], "professional_confirmed": True, "performance_security_required": True}, ACTOR)
    assert sra["status"] == "PERFORMANCE_SECURITY_PENDING"
    security = core.process_deposit(case["id"], pra["id"], {"deposit_type": "PERFORMANCE_SECURITY", "status": "VERIFIED", "instrument_type": "BANK_GUARANTEE", "required_amount": "5000000", "received_amount": "5000000", "instrument_reference": "BG-SYNTH"}, ACTOR, process["id"])
    sra = core.verify_performance_security(case["id"], sra["id"], security["id"], ACTOR)
    assert sra["status"] == "READY_FOR_NCLT"
    approval_payload = {"section30_review_id": section30["id"], "final_eligibility_review_id": final29a["id"], "filing_date": "2026-12-20", "application_number": "IA-SYNTH-PLAN", "hearing_at": "2027-01-15T10:30:00", "bench": "Indore Bench", "idempotency_key": "plan-approval-filed"}
    workspace = core.plan_approval_workspace(case["id"], sra["id"], approval_payload, ACTOR)
    replay = core.plan_approval_workspace(case["id"], sra["id"], approval_payload, ACTOR)
    assert replay["id"] == workspace["id"] and replay["idempotent_replay"] is True
    assert workspace["status"] == "FILED" and workspace["data"]["plan_approval_application_template_status"] == "PLAN_APPROVAL_APPLICATION_TEMPLATE_REQUIRED"
    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM applications WHERE id=? AND case_id=?", (workspace["data"]["application_id"], case["id"])).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM hearings WHERE case_id=?", (case["id"],)).fetchone()[0] == 1
