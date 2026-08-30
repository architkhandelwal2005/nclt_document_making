"""Core guardrails for CIRP-077--084 using only synthetic records."""
import pytest
from fastapi.testclient import TestClient

import server
from database import CasefileDatabase
from transaction_audit_core import TransactionAuditCore
from transaction_auditor_quotation import render_transaction_auditor_quotation
from workflow import WorkflowService
from test_claims_coc_phase2 import ACTOR, build_standard_coc


def _core(tmp_path):
    store, case, *_ = build_standard_coc(tmp_path)
    return store, case, TransactionAuditCore(store)


def test_scope_document_availability_and_related_party_confirmation(tmp_path):
    store, case, core = _core(tmp_path)
    engagement = core.engagement(case["id"], {"status": "ACTIVE"}, ACTOR)
    scope = core.scope(case["id"], engagement["id"], {"review_type": "PREFERENTIAL", "period_from": "2024-05-06", "period_to": "2026-05-06", "basis": "Professional review", "professional_confirmed": True, "status": "CONFIRMED"}, ACTOR)
    assert scope["period_from"] == "2024-05-06" and scope["confirmed_by"] == ACTOR
    reqs=[]
    for index, status in enumerate(["RECEIVED"]*6+["PARTLY_RECEIVED"]*2+["NOT_AVAILABLE","FOLLOW_UP_REQUIRED"], 1):
        req=core.document_requirement(case["id"], engagement["id"], {"category": "BANK_STATEMENT", "description": f"Synthetic {index}"}, ACTOR)
        reqs.append(core.requirement_status(case["id"], req["id"], {"status": status}, ACTOR))
    assert core.availability_summary(case["id"], engagement["id"]) == {"requested":10,"received":6,"partly_received":2,"not_available":1,"follow_up_required":1,"reviewed":0}
    contact=store.create_contact({"name":"Synthetic Contact"}, ACTOR, case["id"])
    party=core.related_party(case["id"], {"contact_id":contact["id"],"relationship_type":"DIRECTOR"}, ACTOR)
    assert party["confirmed_status"] == "UNVERIFIED"
    assert core.confirm_related_party(case["id"],party["id"],"CONFIRMED",ACTOR)["confirmed_status"] == "CONFIRMED"


def test_finding_never_creates_application_without_layered_reviews(tmp_path):
    _, case, core = _core(tmp_path)
    engagement=core.engagement(case["id"],{},ACTOR)
    finding=core.finding(case["id"],engagement["id"],{"auditor_classification":"PREFERENTIAL","title":"Synthetic payment","finding_amount":"1000000"},ACTOR)
    core.finalize_finding(case["id"],finding["id"],ACTOR)
    with pytest.raises(ValueError,match="Completed RP review"):
        core.avoidance_decision(case["id"],engagement["id"],{"finding_ids":[finding["id"]],"decision":"FILE_APPLICATION","professional_confirmed":True},ACTOR)
    core.review_finding(case["id"],finding["id"],"RP",{"status":"ACCEPTED_FOR_LEGAL_REVIEW"},ACTOR)
    core.review_finding(case["id"],finding["id"],"LEGAL",{"status":"COMPLETE"},ACTOR)
    decision=core.avoidance_decision(case["id"],engagement["id"],{"finding_ids":[finding["id"]],"decision":"FILE_APPLICATION","professional_confirmed":True},ACTOR)
    application=core.avoidance_application(case["id"],decision["id"],{"ia_number":"IA-TEST","filed_date":"2026-09-01","status":"FILED"},ACTOR)
    assert application["status"] == "FILED"


def test_no_finding_and_case_isolation(tmp_path):
    store,case,core=_core(tmp_path); engagement=core.engagement(case["id"],{},ACTOR)
    stream=core.workstream(case["id"],engagement["id"],{"review_type":"EXTORTIONATE"},ACTOR)
    assert core.complete_workstream(case["id"],stream["id"],{"status":"REVIEW_COMPLETE_NO_FINDING","professional_confirmed":True},ACTOR)["status"] == "REVIEW_COMPLETE_NO_FINDING"
    other=store.create_case({"name":"Other Synthetic","process_type":"CIRP"},ACTOR)
    with pytest.raises(KeyError): core.scope(other["id"],engagement["id"],{"review_type":"PREFERENTIAL"},ACTOR)
    assert {"CIRP-077","CIRP-084"}.issubset({row["step_code"] for row in WorkflowService(store).definitions()})


def _document(store, case_id, name):
    return store.create_module_record(case_id, "documents", {
        "name": name, "category": "Transaction audit evidence", "status": "final",
        "source_type": "test", "mime_type": "application/pdf",
    }, ACTOR)


def test_quotation_bank_source_exhibit_and_report_versions(tmp_path):
    store, case, core = _core(tmp_path)
    store.update_case(case["id"], {
        "nclt_bench": "Indore Bench", "order_date": "2026-08-01", "registered_address": "1 Demo Road",
    }, ACTOR)
    recipient = store.create_contact({"name": "ABC & Co.", "email": "auditor@example.test"}, ACTOR, case["id"])
    engagement = core.engagement(case["id"], {"status": "ACTIVE", "idempotency_key": "audit-engagement"}, ACTOR)
    with pytest.raises(ValueError, match="REVIEW_REQUIRED"):
        core.quotation_preview(case["id"], engagement["id"], {
            "recipient_contact_id": recipient["id"], "professional_name": "Demo RP", "ibbi_registration_number": "IBBI/IPA-001",
        }, ACTOR)
    scope = core.scope(case["id"], engagement["id"], {
        "review_type": "PREFERENTIAL", "period_from": "2024-08-01", "period_to": "2026-08-01",
        "status": "CONFIRMED", "professional_confirmed": True,
    }, ACTOR)
    values = {"recipient_contact_id": recipient["id"], "professional_name": "Demo RP", "professional_role": "RP", "ibbi_registration_number": "IBBI/IPA-001", "afa_validity": "31-12-2027", "process_email": "cirp@example.test", "ibbi_email": "rp@example.test", "professional_office_address": "2 Professional Road", "quotation_due_date": "2026-08-20", "quotation_due_time": "02:00 PM", "idempotency_key": "quote-abc"}
    quote = core.quotation_preview(case["id"], engagement["id"], values, ACTOR)
    replay = core.quotation_preview(case["id"], engagement["id"], values, ACTOR)
    assert quote["id"] == replay["id"] and replay["idempotent_replay"] is True
    body = quote["data"]["body"]
    assert "1st August 2024 to 1st August 2026" in body
    assert "Further, it may please be noted" in body and "02:00 PM" in body
    assert "Process Specific Email ID: cirp@example.test" in body
    issued = core.issue_quotation(case["id"], quote["id"], ACTOR)
    assert core.issue_quotation(case["id"], quote["id"], ACTOR)["idempotent_replay"] is True
    proof = _document(store, case["id"], "Dispatch proof")
    assert core.record_quotation_dispatch(case["id"], quote["id"], {"proof_document_id": proof["id"]}, ACTOR)["status"] == "ISSUED"
    response = core.quotation_response(case["id"], quote["id"], {"professional_contact_id": recipient["id"], "fee": "50000", "idempotency_key": "response-abc"}, ACTOR)
    selected = core.select_auditor(case["id"], engagement["id"], {"quotation_response_id": response["id"], "professional_confirmed": True}, ACTOR)
    assert core.appoint_auditor(case["id"], selected["id"], {"status": "ACTIVE", "appointment_date": "2026-08-15"}, ACTOR)["status"] == "ACTIVE"
    source = _document(store, case["id"], "Bank statement")
    bank = core.bank_coverage(case["id"], engagement["id"], {"bank_name": "Bank A", "required_period_from": "2024-08-01", "required_period_to": "2026-08-01", "coverage_status": "REVIEW_REQUIRED"}, ACTOR)
    bank = core.update_bank_coverage(case["id"], bank["id"], {"coverage_status": "COMPLETE", "available_period_from": "2024-08-01", "available_period_to": "2026-08-01", "document_ids": [source["id"]]}, ACTOR)
    assert bank["coverage_status"] == "COMPLETE"
    index = core.source_index_entry(case["id"], engagement["id"], {"document_id": source["id"], "source_category": "BANK_STATEMENT", "source_reference": "Annexure 2", "received_from": "RP", "received_date": "2026-08-10", "availability_status": "RECEIVED", "relied_upon": True}, ACTOR)
    finding = core.finding(case["id"], engagement["id"], {"auditor_classification": "SUSPICIOUS_UNCLASSIFIED", "title": "Synthetic observation"}, ACTOR)
    core.finding_evidence(case["id"], finding["id"], {"document_id": source["id"], "source_index_id": index["id"], "page_reference": "Sheet A!B2:D8", "exhibit_reference": "Exhibit 1"}, ACTOR)
    with store.connect() as c:
        evidence = c.execute("SELECT * FROM transaction_finding_evidence WHERE finding_id=?", (finding["id"],)).fetchone()
        assert evidence["document_id"] == index["document_id"] and evidence["document_version"] == 1
        assert c.execute("SELECT COUNT(*) FROM case_events WHERE event_type='TRANSACTION_AUDITOR_QUOTATION_INVITED' AND case_id=?", (case["id"],)).fetchone()[0] == 1
    draft = core.report_version(case["id"], engagement["id"], {"status": "DRAFT", "document_id": source["id"], "received_date": "2026-08-12"}, ACTOR)
    final = core.report_version(case["id"], engagement["id"], {"status": "FINAL", "document_id": source["id"], "received_date": "2026-08-14"}, ACTOR)
    assert (draft["version_number"], final["version_number"], final["status"]) == (1, 2, "FINAL")
    assert scope["status"] == "CONFIRMED"


def test_transaction_auditor_renderer_tracks_office_structure_without_hardcoded_periods():
    rendered = render_transaction_auditor_quotation({
        "corporate_debtor_name": "Example Components Limited",
        "registered_office_address": "10 Industrial Estate, Pune",
        "nclt_bench": "Mumbai Bench",
        "cirp_commencement_date": "2027-02-05",
        "admission_order_date": "2027-02-05",
        "order_received_date": "2027-02-08",
        "professional_name": "Ms. Example Professional",
        "professional_role": "RP",
        "ibbi_registration_number": "IBBI/IPA-TEST/00001",
        "afa_validity": "31-12-2027",
        "professional_office_address": "20 Professional Avenue, Mumbai",
        "process_email": "cirp.example@example.com",
        "ibbi_email": "professional@example.com",
        "recipient_name": "Independent Audit LLP",
        "quotation_due_date": "2027-02-20",
        "quotation_due_time": "02:00 PM",
        "confirmed_scopes": [
            {"review_type": "PREFERENTIAL", "period_from": "2025-02-05", "period_to": "2027-02-05", "status": "CONFIRMED"},
            {"review_type": "FRAUDULENT_WRONGFUL", "period_from": "2023-04-01", "period_to": "2027-02-05", "status": "CONFIRMED"},
            {"review_type": "UNDERVALUE", "period_from": "2026-02-05", "period_to": "2027-02-05", "status": "REVIEW_REQUIRED"},
        ],
    })
    body = rendered["body"]
    assert body.startswith("Dear Sir,")
    assert "Copy of order received on 8th February 2027" in body
    assert "a) Preferential transactions under section 43" in body
    assert "5th February 2025 to 5th February 2027" in body
    assert "b) Fraudulent or wrongful transactions under section 66" in body
    assert "1st April 2023 till 5th February 2027" in body
    assert "Undervalued transactions" not in body
    assert "Further, it may please be noted" in body
    assert "AFA valid till 31-12-2027" in body
    assert "Keshav Proteins" not in body and "01.04.2021" not in body and "for 2 years" not in body
    assert rendered["recipient_name"] == "Independent Audit LLP"


def test_many_findings_one_application_requires_both_reviews(tmp_path):
    _, case, core = _core(tmp_path)
    engagement = core.engagement(case["id"], {"status": "ACTIVE"}, ACTOR)
    findings = []
    for number in ("TA-003", "TA-004"):
        finding = core.finding(case["id"], engagement["id"], {"finding_number": number, "auditor_classification": "PREFERENTIAL", "title": number}, ACTOR)
        findings.append(core.finalize_finding(case["id"], finding["id"], ACTOR))
    for finding in findings:
        core.review_finding(case["id"], finding["id"], "RP", {"status": "ACCEPTED_FOR_LEGAL_REVIEW"}, ACTOR)
        core.review_finding(case["id"], finding["id"], "LEGAL", {"status": "RECOMMEND_FILE"}, ACTOR)
    decision = core.avoidance_decision(case["id"], engagement["id"], {"finding_ids": [item["id"] for item in findings], "decision": "FILE_APPLICATION", "professional_confirmed": True}, ACTOR)
    app = core.avoidance_application(case["id"], decision["id"], {"status": "DRAFT"}, ACTOR)
    hearing = core.store.create_module_record(case["id"], "hearings", {"hearing_at": "2026-10-01T10:00:00", "purpose": "Synthetic avoidance hearing"}, ACTOR)
    filed = core.update_avoidance_application(case["id"], app["id"], {"status": "FILED", "ia_number": "IA-SYNTH-001", "filed_date": "2026-09-01", "next_hearing_date": "2026-10-01", "hearing_id": hearing["id"]}, ACTOR)
    with core.store.connect() as c:
        assert c.execute("SELECT COUNT(*) FROM avoidance_application_findings WHERE avoidance_application_id=?", (filed["id"],)).fetchone()[0] == 2
    assert filed["status"] == "FILED" and filed["hearing_id"] == hearing["id"]


def test_restricted_transaction_audit_document_denies_case_viewer(tmp_path, monkeypatch):
    store = CasefileDatabase(tmp_path / "restricted-phase5.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    case = store.create_case({"name": "Restricted Demo", "process_type": "CIRP"}, server.ADMIN_ID)
    viewer = store.create_user("phase5-viewer@example.com", "Phase 5 Viewer", "viewer", server.ADMIN_PASSWORD_HASH)
    store.set_case_assignments(case["id"], [viewer["id"]], server.ADMIN_ID)
    document = store.create_module_record(case["id"], "documents", {"name": "Audit report.pdf", "category": "Transaction Audit", "storage_path": "case-files/not-present.pdf", "confidentiality_classification": "RESTRICTED_TRANSACTION_AUDIT"}, server.ADMIN_ID)
    monkeypatch.setattr(server, "casefile_store", store)
    with TestClient(server.app) as client:
        login = client.post("/api/auth/login", json={"email": viewer["email"], "password": server.admin_pwd})
        assert login.status_code == 200
        response = client.get(f"/api/cases/{case['id']}/documents/{document['id']}/file", headers={"Authorization": f"Bearer {login.json()['access_token']}"})
    assert response.status_code == 403 and response.json()["detail"]["code"] == "RESTRICTED_DOCUMENT"
