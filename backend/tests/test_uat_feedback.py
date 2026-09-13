"""Regression tests for the UAT feedback queue and its access boundary."""

from fastapi.testclient import TestClient

import server
from database import CasefileDatabase


def _headers(client: TestClient, email: str, password: str) -> dict:
    response = client.post("/api/auth/login", json={"email": email, "password": password})
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _store(tmp_path, monkeypatch) -> CasefileDatabase:
    store = CasefileDatabase(tmp_path / "uat-feedback.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "APP_ENVIRONMENT", "UAT")
    monkeypatch.setattr(server, "APP_RELEASE_ID", "uat-feedback-test")
    return store


def _report(case_id=None):
    return {
        "case_id": case_id,
        "module": "Claims Register",
        "action_taken": "Open Claims and select New Claim.",
        "expected_result": "The intake form should open.",
        "actual_result": "The page stays on the register.",
        "severity": "HIGH",
    }


def test_uat_feedback_is_case_safe_and_triaged_only_by_firm_leads(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        admin = _headers(client, server.ADMIN_EMAIL, server.admin_pwd)
        case = client.post("/api/cases", json={"name": "Synthetic UAT Limited", "process_type": "CIRP"}, headers=admin).json()
        associate = client.post("/api/admin/users", json={"name": "UAT Associate", "email": "uat-associate@example.com", "role": "associate", "password": "Temporary-Password-123"}, headers=admin).json()
        client.put(f"/api/admin/cases/{case['id']}/assignments", json={"user_ids": [associate['id']]}, headers=admin)
        associate_headers = _headers(client, "uat-associate@example.com", "Temporary-Password-123")

        created = client.post("/api/uat-feedback", json=_report(case["id"]), headers=associate_headers)
        assert created.status_code == 200, created.text
        assert created.json()["environment"] == "UAT"
        assert created.json()["release_id"] == "uat-feedback-test"
        assert created.json()["status"] == "NEW"
        report_id = created.json()["id"]

        assert [item["id"] for item in client.get("/api/uat-feedback", headers=associate_headers).json()] == [report_id]
        assert client.put(f"/api/uat-feedback/{report_id}", json={"status": "TRIAGED", "triage_notes": "Reproduce locally"}, headers=associate_headers).status_code == 403

        triaged = client.put(f"/api/uat-feedback/{report_id}", json={"status": "IN_PROGRESS", "triage_notes": "Regression test added."}, headers=admin)
        assert triaged.status_code == 200, triaged.text
        assert triaged.json()["status"] == "IN_PROGRESS"
        assert client.get(f"/api/cases/{case['id']}/activity", headers=admin).status_code == 200
        assert any(item["event_type"] == "UAT_FEEDBACK_TRIAGED" for item in client.get(f"/api/cases/{case['id']}/activity", headers=admin).json())

    assert store.list_uat_feedback()[0]["triage_notes"] == "Regression test added."


def test_read_only_tester_can_report_without_receiving_operational_write_access(tmp_path, monkeypatch):
    _store(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        admin = _headers(client, server.ADMIN_EMAIL, server.admin_pwd)
        client.post("/api/admin/users", json={"name": "UAT Viewer", "email": "uat-viewer@example.com", "role": "viewer", "password": "Temporary-Password-123"}, headers=admin)
        viewer = _headers(client, "uat-viewer@example.com", "Temporary-Password-123")

        assert client.post("/api/uat-feedback", json=_report(), headers=viewer).status_code == 200
        assert client.post("/api/cases", json={"name": "Must Not Create"}, headers=viewer).status_code == 403
