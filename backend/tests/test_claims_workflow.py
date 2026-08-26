"""Claims Phase 1 API, calculation, history, isolation and authorization tests."""

from pathlib import Path

from fastapi.testclient import TestClient

import server
from database import CasefileDatabase


def setup_app(tmp_path, monkeypatch):
    store = CasefileDatabase(tmp_path / "claims.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    monkeypatch.setattr(server, "casefile_store", store)
    data_dir = tmp_path / "data"
    files_dir = data_dir / "files"
    files_dir.mkdir(parents=True)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "CASE_FILES_DIR", files_dir)
    return store


def login(client):
    response = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def create_case(client, headers, name="Claims Limited"):
    response = client.post("/api/cases", json={"name": name, "process_type": "CIRP", "commencement_date": "2026-08-01"}, headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def claim_payload(**changes):
    payload = {
        "received_date": "2026-08-25", "received_via": "Email", "creditor_name": "Example Bank Limited",
        "creditor_category": "FINANCIAL_CREDITOR", "form_type": "Form C", "claimed_amount": 1150,
        "principal_claimed": 1000, "interest_claimed": 100, "other_amount_claimed": 25,
        "email": "claims@example.test", "related_party_status": "UNKNOWN", "idempotency_key": "claim-one",
    }
    payload.update(changes)
    return payload


def create_claim(client, headers, case_id, **changes):
    response = client.post(f"/api/cases/{case_id}/claims", json=claim_payload(**changes), headers=headers)
    assert response.status_code == 200, response.text
    return response.json()


def test_claim_intake_mandatory_fields_calculations_documents_and_duplicate_handling(tmp_path, monkeypatch):
    setup_app(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = login(client)
        case_id = create_case(client, headers)["id"]
        missing = client.post(f"/api/cases/{case_id}/claims", json={"creditor_name": "Incomplete"}, headers=headers)
        assert missing.status_code == 422
        negative = client.post(f"/api/cases/{case_id}/claims", json=claim_payload(claimed_amount=-1), headers=headers)
        assert negative.status_code == 422

        claim = create_claim(client, headers, case_id)
        assert claim["claim_number"] == "CLM-0001"
        assert claim["claimed_amount"] == 1150
        assert claim["calculated_component_total"] == 1125
        assert claim["amount_not_admitted"] == 1150
        assert claim["duplicate_candidates"] == []

        duplicate = client.post(f"/api/cases/{case_id}/claims", json=claim_payload(), headers=headers)
        assert duplicate.status_code == 200
        assert duplicate.json()["id"] == claim["id"]
        assert duplicate.json()["duplicate_submission"] is True
        assert len(client.get(f"/api/cases/{case_id}/claims", headers=headers).json()) == 1

        upload = client.post(
            f"/api/cases/{case_id}/claims/{claim['id']}/documents/upload",
            data={"document_type": "Claim Form"}, files={"file": ("form-c.pdf", b"%PDF-test", "application/pdf")}, headers=headers,
        )
        assert upload.status_code == 200, upload.text
        assert upload.json()["linked_type"] == "claim"
        detail = client.get(f"/api/cases/{case_id}/claims/{claim['id']}", headers=headers).json()
        assert detail["total_mismatch"] is True
        assert len(detail["documents"]) == 1
        assert detail["documents"][0]["id"] == upload.json()["id"]
        downloaded = client.get(f"/api/cases/{case_id}/documents/{upload.json()['id']}/file", headers=headers)
        assert downloaded.status_code == 200
        assert downloaded.content == b"%PDF-test"


def test_verification_query_response_revision_decisions_summary_and_creditors_export(tmp_path, monkeypatch):
    setup_app(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = login(client)
        case_id = create_case(client, headers)["id"]
        financial = create_claim(client, headers, case_id, claimed_amount=1100, principal_claimed=1000, interest_claimed=100, other_amount_claimed=0)
        started = client.post(f"/api/cases/{case_id}/claims/{financial['id']}/verification/start", headers=headers)
        assert started.status_code == 200
        admitted = client.post(f"/api/cases/{case_id}/claims/{financial['id']}/decision", json={
            "decision_status": "ADMITTED", "principal_admitted": 1000, "interest_admitted": 100,
            "other_amount_admitted": 0, "decision_date": "2026-08-27", "verification_notes": "Books and bank statement checked",
        }, headers=headers)
        assert admitted.status_code == 200, admitted.text
        assert admitted.json()["admitted_amount"] == 1100
        assert admitted.json()["amount_not_admitted"] == 0

        operational = create_claim(client, headers, case_id, creditor_name="Trade Supplier", creditor_category="OPERATIONAL_CREDITOR", form_type="Form B", email="trade@example.test", claimed_amount=800, principal_claimed=800, interest_claimed=None, other_amount_claimed=None, idempotency_key="claim-two")
        query = client.post(f"/api/cases/{case_id}/claims/{operational['id']}/queries", json={
            "query_date": "2026-08-27", "subject": "Ledger required", "query_text": "Please provide the complete ledger.",
            "information_requested": "Ledger", "sent_to": "trade@example.test", "status": "SENT",
        }, headers=headers)
        assert query.status_code == 200, query.text
        response = client.post(f"/api/cases/{case_id}/claims/{operational['id']}/queries/{query.json()['id']}/responses", json={
            "response_received_date": "2026-08-29", "response_notes": "Ledger received", "source_reference": "office-email-29", "query_status": "RESPONDED",
        }, headers=headers)
        assert response.status_code == 200, response.text
        revised = client.post(f"/api/cases/{case_id}/claims/{operational['id']}/revisions", json={
            "reason": "Creditor supplied revised ledger", "changes": {"claimed_amount": 750, "principal_claimed": 750, "interest_claimed": None, "other_amount_claimed": None},
        }, headers=headers)
        assert revised.status_code == 200, revised.text
        assert revised.json()["revision"] == 2
        partial = client.post(f"/api/cases/{case_id}/claims/{operational['id']}/decision", json={
            "decision_status": "PARTLY_ADMITTED", "principal_admitted": 600, "interest_admitted": 0, "other_amount_admitted": 0,
            "decision_date": "2026-08-30", "reason": "Invoices support only part of the ledger balance",
        }, headers=headers)
        assert partial.status_code == 200, partial.text
        assert partial.json()["claimed_amount"] == 750
        assert partial.json()["admitted_amount"] == 600
        assert partial.json()["amount_not_admitted"] == 150

        rejected_claim = create_claim(client, headers, case_id, creditor_name="Unsupported Claimant", creditor_category="OTHER_CREDITOR", form_type="Other", email="other@example.test", claimed_amount=100, principal_claimed=100, interest_claimed=None, other_amount_claimed=None, idempotency_key="claim-three")
        no_reason = client.post(f"/api/cases/{case_id}/claims/{rejected_claim['id']}/decision", json={"decision_status": "REJECTED", "principal_admitted": 0, "interest_admitted": 0, "other_amount_admitted": 0}, headers=headers)
        assert no_reason.status_code == 422
        rejected = client.post(f"/api/cases/{case_id}/claims/{rejected_claim['id']}/decision", json={"decision_status": "REJECTED", "principal_admitted": 0, "interest_admitted": 0, "other_amount_admitted": 0, "reason": "No evidence of debt"}, headers=headers)
        assert rejected.status_code == 200, rejected.text

        excessive = client.post(f"/api/cases/{case_id}/claims/{rejected_claim['id']}/decision", json={"decision_status": "ADMITTED", "principal_admitted": 120, "interest_admitted": 0, "other_amount_admitted": 0}, headers=headers)
        assert excessive.status_code == 422
        override = client.post(f"/api/cases/{case_id}/claims/{rejected_claim['id']}/decision", json={"decision_status": "ADMITTED", "principal_admitted": 120, "interest_admitted": 0, "other_amount_admitted": 0, "override_reason": "Post-filing adjustment approved by RP"}, headers=headers)
        assert override.status_code == 200, override.text
        assert override.json()["decisions"][0]["override_reason"]

        summary = client.get(f"/api/cases/{case_id}/claims/summary", headers=headers).json()
        assert summary["total_claims"] == 3
        assert summary["total_claimed"] == 1950
        assert summary["total_admitted"] == 1820
        creditors = client.get(f"/api/cases/{case_id}/claims/list-of-creditors", headers=headers)
        assert creditors.status_code == 200
        assert {row["creditor"] for row in creditors.json()} == {"Example Bank Limited", "Trade Supplier", "Unsupported Claimant"}
        export = client.get(f"/api/cases/{case_id}/claims/list-of-creditors/export/docx", headers=headers)
        assert export.status_code == 200, export.text
        assert export.content.startswith(b"PK")
        detail = client.get(f"/api/cases/{case_id}/claims/{operational['id']}", headers=headers).json()
        assert len(detail["revisions"]) == 2
        assert len(detail["queries"][0]["responses"]) == 1
        assert any(event["event_type"] == "CLAIM_QUERY_RESPONSE_RECORDED" for event in detail["history"])
        audit = client.get(f"/api/cases/{case_id}/audit", headers=headers).json()
        actions = {item["action"] for item in audit}
        assert {"CLAIM_CREATED", "CLAIM_VERIFICATION_STARTED", "CLAIM_QUERY_CREATED", "CLAIM_QUERY_RESPONSE_RECORDED", "CLAIM_ADMITTED", "CLAIM_PARTLY_ADMITTED", "CLAIM_REJECTED", "CLAIM_DECISION_REVISED"} <= actions


def test_claim_case_isolation_unauthorized_access_and_viewer_read_only(tmp_path, monkeypatch):
    setup_app(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        admin = login(client)
        allowed = create_case(client, admin, "Allowed Limited")
        restricted = create_case(client, admin, "Restricted Limited")
        claim = create_claim(client, admin, restricted["id"])
        created_user = client.post("/api/admin/users", json={"name": "Claims Viewer", "email": "claims-viewer@example.com", "role": "viewer", "password": "Temporary-Password-123"}, headers=admin)
        assert created_user.status_code == 200, created_user.text
        user = created_user.json()
        assert client.put(f"/api/admin/cases/{allowed['id']}/assignments", json={"user_ids": [user["id"]]}, headers=admin).status_code == 200
        viewer_login = client.post("/api/auth/login", json={"email": "claims-viewer@example.com", "password": "Temporary-Password-123"})
        assert viewer_login.status_code == 200, viewer_login.text
        token = viewer_login.json()["access_token"]
        viewer = {"Authorization": f"Bearer {token}"}
        assert client.get(f"/api/cases/{allowed['id']}/claims", headers=viewer).status_code == 200
        assert client.post(f"/api/cases/{allowed['id']}/claims", json=claim_payload(idempotency_key="viewer-write"), headers=viewer).status_code == 403
        assert client.get(f"/api/cases/{restricted['id']}/claims", headers=viewer).status_code == 404
        assert client.get(f"/api/cases/{restricted['id']}/claims/{claim['id']}", headers=viewer).status_code == 404
        assert client.get(f"/api/cases/{restricted['id']}/claims/list-of-creditors/export/docx", headers=viewer).status_code == 404
