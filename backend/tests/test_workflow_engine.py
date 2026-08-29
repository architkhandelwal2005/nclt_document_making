"""Regression tests for the deterministic CIRP workflow/event/deadline core."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import server
from database import CasefileDatabase
from workflow import EventEngine, WorkflowError, WorkflowService


def _store(tmp_path: Path) -> CasefileDatabase:
    store = CasefileDatabase(tmp_path / "workflow-test.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    return store


def _case(store: CasefileDatabase, name: str, commencement: str | None = "2026-07-31") -> dict:
    return store.create_case(
        {"name": name, "process_type": "CIRP", "commencement_date": commencement}, server.ADMIN_ID,
    )


def _step(workflow: dict, code: str) -> dict:
    return next(item for item in workflow["steps"] if item["step_code"] == code)


def _admission_event(store: CasefileDatabase, case_id: str, key: str = "admission-confirmed") -> dict:
    return EventEngine(store).record_event(
        case_id, "ADMISSION_ORDER_CONFIRMED", "2026-07-31", server.ADMIN_ID,
        source_type="test", source_id=case_id,
        metadata={"cirp_commencement_date": "2026-07-31", "irp_appointment_date": "2026-07-31"},
        idempotency_key=key,
    )


def test_master_seed_is_versioned_and_case_execution_is_separate(tmp_path):
    store = _store(tmp_path)
    service = WorkflowService(store)
    definitions = service.definitions()
    assert [item["step_code"] for item in definitions] == [f"CIRP-{number:03d}" for number in range(1, 77)]
    assert all(item["workflow_version"] == "CIRP_2026_V1" for item in definitions)
    assert service.definition("CIRP-021")["deadline_rules"][0]["offset_days"] == 3

    case = _case(store, "Mahakali-style CIRP Test")
    _admission_event(store, case["id"])
    execution = service.get_case_workflow(case["id"])
    assert execution["workflow"]["workflow_version"] == "CIRP_2026_V1"
    assert len(execution["steps"]) == 76
    assert _step(execution, "CIRP-001")["status"] == "READY"
    assert _step(execution, "CIRP-007")["effective_due_date"] == "2026-08-01"
    assert _step(execution, "CIRP-012")["effective_due_date"] == "2026-08-02"
    assert _step(execution, "CIRP-021")["effective_due_date"] == "2026-08-03"
    assert _step(execution, "CIRP-022")["status"] == "NOT_TRIGGERED"
    assert _step(execution, "CIRP-023")["status"] == "NOT_TRIGGERED"


def test_event_workflow_task_and_deadline_creation_are_idempotent(tmp_path):
    store = _store(tmp_path)
    case = _case(store, "Idempotency Limited")
    first = _admission_event(store, case["id"], "same-event")
    second = _admission_event(store, case["id"], "same-event")
    assert first["id"] == second["id"]
    assert second["idempotent_replay"] is True

    service = WorkflowService(store)
    first_init = service.initialize_cirp(case["id"], server.ADMIN_ID)
    second_init = service.initialize_cirp(case["id"], server.ADMIN_ID)
    assert first_init["workflow"]["id"] == second_init["workflow"]["id"]
    assert first_init["created"] is False
    assert second_init["idempotent_replay"] is True

    with store.connect() as connection:
        assert connection.execute("SELECT COUNT(*) FROM case_workflows WHERE case_id=?", (case["id"],)).fetchone()[0] == 1
        assert connection.execute("SELECT COUNT(*) FROM case_workflow_steps WHERE case_id=?", (case["id"],)).fetchone()[0] == 76
        assert connection.execute("SELECT COUNT(*) FROM case_deadlines WHERE case_id=?", (case["id"],)).fetchone()[0] == 76
        assert connection.execute("SELECT COUNT(*) FROM tasks WHERE case_id=? AND workflow_step_id IS NOT NULL", (case["id"],)).fetchone()[0] == 21
        # One admission event plus two independently derived confirmed anchors.
        assert connection.execute("SELECT COUNT(*) FROM case_events WHERE case_id=?", (case["id"],)).fetchone()[0] == 3


def test_missing_anchor_remains_review_required_instead_of_guessing(tmp_path):
    store = _store(tmp_path)
    case = _case(store, "Missing Anchor Limited", None)
    workflow = WorkflowService(store).initialize_cirp(case["id"], server.ADMIN_ID)
    assert all(item["calculated_due_date"] is None for item in workflow["steps"])
    assert all(item["deadline_status"] == "REVIEW_REQUIRED" for item in workflow["steps"])

    EventEngine(store).record_event(
        case["id"], "ADMISSION_ORDER_CONFIRMED", "2026-07-31", server.ADMIN_ID,
        source_type="test", source_id="no-anchor", idempotency_key="no-anchor",
    )
    workflow = WorkflowService(store).get_case_workflow(case["id"])
    assert _step(workflow, "CIRP-001")["status"] == "READY"
    assert _step(workflow, "CIRP-007")["status"] == "NOT_TRIGGERED"
    assert _step(workflow, "CIRP-021")["calculated_due_date"] is None


def test_evidence_approval_and_deadline_override_controls(tmp_path):
    store = _store(tmp_path)
    case = _case(store, "Evidence Limited")
    _admission_event(store, case["id"])
    service = WorkflowService(store)
    workflow = service.get_case_workflow(case["id"])
    takeover = _step(workflow, "CIRP-007")

    service.start_step(case["id"], takeover["id"], server.ADMIN_ID)
    with pytest.raises(WorkflowError) as missing:
        service.complete_step(case["id"], takeover["id"], server.ADMIN_ID)
    assert missing.value.code == "MISSING_REQUIRED_EVIDENCE"

    service.attach_evidence(
        case["id"], takeover["id"], server.ADMIN_ID, "SERVICE_PROOF",
        source_type="manual_reference", source_id="service-proof-1",
    )
    pending = service.complete_step(case["id"], takeover["id"], server.ADMIN_ID)
    assert pending["status"] == "PENDING_APPROVAL"
    completed = service.approve_step(case["id"], takeover["id"], server.ADMIN_ID)
    assert completed["status"] == "COMPLETED"
    assert completed["task_status"] == "completed"

    public_announcement = _step(service.get_case_workflow(case["id"]), "CIRP-021")
    service.deadlines.override(
        case["id"], public_announcement["id"], "2026-08-04", "Order-specific reviewed direction", server.ADMIN_ID,
    )
    EventEngine(store).record_event(
        case["id"], "PUBLIC_ANNOUNCEMENT_DRAFT_READY", "2026-08-01", server.ADMIN_ID,
        source_type="test", source_id="draft-1", idempotency_key="pa-draft-1",
    )
    refreshed = _step(service.get_case_workflow(case["id"]), "CIRP-021")
    assert refreshed["calculated_due_date"] == "2026-08-03"
    assert refreshed["override_due_date"] == "2026-08-04"
    assert refreshed["effective_due_date"] == "2026-08-04"


def test_public_announcement_confirmation_updates_linked_steps_without_unsafe_website_assumption(tmp_path):
    store = _store(tmp_path)
    case = _case(store, "Publication Limited")
    _admission_event(store, case["id"])
    document = store.create_module_record(
        case["id"], "documents",
        {"name": "Published Form A.pdf", "category": "Published Public Announcement", "status": "PUBLISHED",
         "source_type": "test", "storage_path": "test/published.pdf", "mime_type": "application/pdf"},
        server.ADMIN_ID,
    )
    events = EventEngine(store)
    events.record_event(
        case["id"], "PUBLIC_ANNOUNCEMENT_DRAFT_READY", "2026-08-01", server.ADMIN_ID,
        source_type="public_announcement", source_id="pa-1", idempotency_key="draft-ready",
    )
    events.record_event(
        case["id"], "PUBLIC_ANNOUNCEMENT_SENT_FOR_PUBLICATION", "2026-08-02", server.ADMIN_ID,
        source_type="public_announcement", source_id="pa-1", idempotency_key="pa-sent",
    )
    confirmed = events.record_event(
        case["id"], "PUBLIC_ANNOUNCEMENT_CONFIRMED", "2026-08-03", server.ADMIN_ID,
        source_type="public_announcement", source_id="pa-1", source_document_id=document["id"],
        idempotency_key="pa-confirmed",
    )
    replay = events.record_event(
        case["id"], "PUBLIC_ANNOUNCEMENT_CONFIRMED", "2026-08-03", server.ADMIN_ID,
        source_type="public_announcement", source_id="pa-1", source_document_id=document["id"],
        idempotency_key="pa-confirmed",
    )
    assert replay["id"] == confirmed["id"]
    assert replay["idempotent_replay"] is True
    workflow = WorkflowService(store).get_case_workflow(case["id"])
    for code in ("CIRP-021", "CIRP-022"):
        step = _step(workflow, code)
        assert step["status"] == "COMPLETED"
        assert step["approval_status"] == "APPROVED"
        assert step["evidence_status"] == "SATISFIED"
    website_upload = _step(workflow, "CIRP-023")
    assert website_upload["status"] == "IN_PROGRESS"
    assert website_upload["evidence_status"] == "MISSING"
    assert "website upload evidence remains required" in website_upload["remarks"].lower()
    with store.connect() as connection:
        assert connection.execute(
            "SELECT COUNT(*) FROM workflow_step_evidence WHERE case_id=?", (case["id"],)
        ).fetchone()[0] == 2


def test_case_isolation_and_role_authorization_via_api(tmp_path, monkeypatch):
    store = _store(tmp_path)
    first = _case(store, "First CIRP Limited")
    second = _case(store, "Second CIRP Limited")
    _admission_event(store, first["id"], "first-admission")
    _admission_event(store, second["id"], "second-admission")
    monkeypatch.setattr(server, "casefile_store", store)

    viewer = store.create_user("workflow-viewer@example.com", "Viewer", "viewer", server.ADMIN_PASSWORD_HASH)
    outsider = store.create_user("workflow-outsider@example.com", "Outsider", "staff", server.ADMIN_PASSWORD_HASH)
    store.set_case_assignments(first["id"], [viewer["id"]], server.ADMIN_ID)

    with TestClient(server.app) as client:
        admin_login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        admin_headers = {"Authorization": f"Bearer {admin_login.json()['access_token']}"}
        viewer_login = client.post("/api/auth/login", json={"email": viewer["email"], "password": server.admin_pwd})
        assert viewer_login.status_code == 200, viewer_login.text
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
        outsider_login = client.post("/api/auth/login", json={"email": outsider["email"], "password": server.admin_pwd})
        assert outsider_login.status_code == 200, outsider_login.text
        outsider_headers = {"Authorization": f"Bearer {outsider_login.json()['access_token']}"}

        assert client.get(f"/api/cases/{first['id']}/workflow", headers=viewer_headers).status_code == 200
        denied_mutation = client.post(
            f"/api/cases/{first['id']}/events",
            json={"event_type": "DOCUMENT_RECEIVED", "event_date": "2026-08-02"}, headers=viewer_headers,
        )
        assert denied_mutation.status_code == 403
        assert client.get(f"/api/cases/{first['id']}/workflow", headers=outsider_headers).status_code == 404

        first_workflow = client.get(f"/api/cases/{first['id']}/workflow", headers=admin_headers).json()
        second_workflow = client.get(f"/api/cases/{second['id']}/workflow", headers=admin_headers).json()
        assert {step["case_id"] for step in first_workflow["steps"]} == {first["id"]}
        assert {step["case_id"] for step in second_workflow["steps"]} == {second["id"]}
        assert {step["id"] for step in first_workflow["steps"]}.isdisjoint(
            {step["id"] for step in second_workflow["steps"]}
        )


def test_workflow_summary_and_event_ledger_api(tmp_path, monkeypatch):
    store = _store(tmp_path)
    case = _case(store, "Summary Limited")
    monkeypatch.setattr(server, "casefile_store", store)
    with TestClient(server.app) as client:
        login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        event = client.post(
            f"/api/cases/{case['id']}/events",
            json={
                "event_type": "ADMISSION_ORDER_CONFIRMED", "event_date": "2026-07-31",
                "source_type": "test", "source_id": "summary-admission",
                "metadata": {"cirp_commencement_date": "2026-07-31"},
                "idempotency_key": "summary-admission",
            },
            headers=headers,
        )
        assert event.status_code == 200, event.text
        summary = client.get(f"/api/cases/{case['id']}/workflow/summary", headers=headers)
        assert summary.status_code == 200, summary.text
        payload = summary.json()
        assert payload["total_steps"] == 76
        assert payload["ready"] == 21
        assert payload["not_triggered"] == 55
        assert payload["overdue_count"] == 21  # Event-gated NOT_TRIGGERED steps are excluded.
        assert payload["next_statutory_deadline"]["step"] == "CIRP-001"
        ledger = client.get(f"/api/cases/{case['id']}/events", headers=headers)
        assert ledger.status_code == 200
        assert [item["event_date"] for item in ledger.json()] == sorted(item["event_date"] for item in ledger.json())
