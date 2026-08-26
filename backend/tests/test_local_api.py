"""Local SQLite API regression and case-isolation tests."""
import json

from fastapi.testclient import TestClient

import server
from database import CasefileDatabase
from security import MAGIC, unprotect_bytes


def _login(client: TestClient) -> dict:
    response = client.post(
        "/api/auth/login",
        json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd},
    )
    assert response.status_code == 200, response.text
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def _use_database(tmp_path, monkeypatch) -> CasefileDatabase:
    store = CasefileDatabase(tmp_path / "casefile-test.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    monkeypatch.setattr(server, "casefile_store", store)
    server.DOC_CACHE.clear()
    return store


def _create_case(client: TestClient, headers: dict, name: str, commencement: str = "2026-06-10") -> dict:
    response = client.post(
        "/api/cases",
        json={"name": name, "cin": f"CIN-{name[:3].upper()}", "process_type": "CIRP", "commencement_date": commencement, "values": {"cd_name": name}},
        headers=headers,
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_auth_and_protected_routes(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        assert client.get("/api/cases").status_code == 401
        headers = _login(client)
        me = client.get("/api/auth/me", headers=headers)
        assert me.status_code == 200
        assert me.json()["email"] == server.ADMIN_EMAIL


def test_sqlite_profile_case_deadlines_and_soft_archive(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = _login(client)
        profile = {"ip_name": "Test IP", "ibbi_reg_no": "IBBI/TEST/001"}
        assert client.put("/api/profile", json=profile, headers=headers).status_code == 200
        assert client.get("/api/profile", headers=headers).json()["ip_name"] == "Test IP"

        matter = _create_case(client, headers, "Example Limited")
        case_id = matter["id"]
        assert matter["cin"] == "CIN-EXA"
        deadlines = client.get(f"/api/cases/{case_id}/deadlines", headers=headers)
        assert deadlines.status_code == 200
        assert len(deadlines.json()) >= 10
        assert any(item["due_date"] == "2026-06-13" for item in deadlines.json())

        updated = client.put(f"/api/cases/{case_id}", json={"name": "Example Ltd", "commencement_date": "2026-06-11"}, headers=headers)
        assert updated.status_code == 200, updated.text
        refreshed = client.get(f"/api/cases/{case_id}/deadlines", headers=headers).json()
        assert any(item["due_date"] == "2026-06-14" for item in refreshed)

        assert client.delete(f"/api/cases/{case_id}", headers=headers).status_code == 200
        assert client.get(f"/api/cases/{case_id}", headers=headers).status_code == 404
        assert client.get("/api/cases", headers=headers).json() == []


def test_case_module_crud_and_isolation(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = _login(client)
        first = _create_case(client, headers, "First Limited")
        second = _create_case(client, headers, "Second Limited")
        created = client.post(f"/api/cases/{first['id']}/tasks", json={"title": "Verify books", "priority": "high", "due_date": "2026-07-01"}, headers=headers)
        assert created.status_code == 200, created.text
        task = created.json()
        assert task["case_id"] == first["id"]
        assert len(client.get(f"/api/cases/{first['id']}/tasks", headers=headers).json()) == 2
        assert len(client.get(f"/api/cases/{second['id']}/tasks", headers=headers).json()) == 1

        cross_case = client.put(f"/api/cases/{second['id']}/tasks/{task['id']}", json={"status": "completed"}, headers=headers)
        assert cross_case.status_code == 404
        completed = client.put(f"/api/cases/{first['id']}/tasks/{task['id']}", json={"status": "completed", "completed_at": "2026-06-30T10:00:00Z"}, headers=headers)
        assert completed.status_code == 200
        assert completed.json()["status"] == "completed"
        assert len(client.get(f"/api/cases/{first['id']}/activity", headers=headers).json()) >= 3
        assert len(client.get(f"/api/cases/{first['id']}/audit", headers=headers).json()) >= 3


def test_contact_dashboard_and_case_document_generation(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = _login(client)
        matter = _create_case(client, headers, "Document Limited")
        case_id = matter["id"]
        contact = client.post(f"/api/cases/{case_id}/contacts", json={"name": "Example Bank", "kind": "organization", "role": "Financial creditor", "email": "claims@example.test"}, headers=headers)
        assert contact.status_code == 200, contact.text
        assert client.get(f"/api/cases/{case_id}/contacts", headers=headers).json()[0]["role"] == "Financial creditor"

        generated = client.post(
            "/api/documents",
            json={
                "case_id": case_id,
                "template_id": "constitution-coc",
                "values": {"cd_name": "Document Limited", "cin": "U12345TEST", "nclt_bench": "Test Bench", "cp_ib_number": "CP(IB) 1/2026", "ip_name": "Test IP", "ibbi_reg_no": "IBBI/TEST/001"},
                "tables": {"df_creditors": [["Sr. No.", "Creditor", "Share"], ["1", "Bank", "100"]]},
            },
            headers=headers,
        )
        assert generated.status_code == 200, generated.text
        document_id = generated.json()["id"]
        assert generated.json()["case_id"] == case_id
        server.DOC_CACHE.clear()
        assert client.get(f"/api/documents/{document_id}/download/docx", headers=headers).content.startswith(b"PK")
        assert client.get(f"/api/documents/{document_id}/download/pdf", headers=headers).content.startswith(b"%PDF")
        assert len(client.get(f"/api/cases/{case_id}/documents", headers=headers).json()) == 1
        dashboard = client.get("/api/dashboard", headers=headers)
        assert dashboard.status_code == 200
        assert dashboard.json()["counts"]["active_cases"] == 1


def test_legacy_json_migrates_once_and_source_is_preserved(tmp_path):
    legacy_path = tmp_path / "casefile.json"
    source = {"matters": [{"id": "old-case", "name": "Legacy Limited", "values": {"cd_name": "Legacy Limited", "cin": "OLD-CIN"}}], "profiles": {"admin": {"ip_name": "Legacy IP"}}, "generated_documents": []}
    legacy_path.write_text(json.dumps(source), encoding="utf-8")
    store = CasefileDatabase(tmp_path / "migrated.db")
    first = store.migrate_legacy_json(legacy_path, "admin")
    second = store.migrate_legacy_json(legacy_path, "admin")
    assert first["cases"] == 1
    assert second["cases"] == 0
    assert store.list_cases()[0]["values"]["cin"] == "OLD-CIN"
    assert store.get_profile("admin")["ip_name"] == "Legacy IP"
    assert json.loads(legacy_path.read_text(encoding="utf-8")) == source


def test_every_operational_module_accepts_case_scoped_records(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        headers = _login(client)
        case_id = _create_case(client, headers, "All Modules Limited")["id"]

        samples = {
            "hearings": {"hearing_at": "2026-08-25T10:30", "purpose": "Status hearing"},
            "applications": {"application_type": "Progress report", "status": "filed"},
            "claims": {"received_date": "2026-08-22", "creditor_name": "Example Bank", "creditor_category": "FINANCIAL_CREDITOR", "form_type": "Form C", "claimed_amount": 1000, "email": "claims@example.test"},
            "coc-members": {"admitted_debt": 1000, "voting_share": 100, "valid_from": "2026-08-22"},
            "coc-meetings": {"meeting_number": 1, "meeting_at": "2026-08-26T11:00"},
            "communications": {"channel": "Email", "occurred_at": "2026-08-22T12:00", "subject": "Notice"},
            "assets": {"category": "Plant", "description": "Production line"},
            "financial-records": {"record_type": "Book creditor", "name": "Example supplier"},
            "valuations": {"asset_class": "Plant and machinery"},
            "expenses": {"category": "Publication", "expense_date": "2026-08-22", "amount": 5000},
            "contributions": {"called_amount": 5000, "due_date": "2026-08-30"},
        }
        created = {}
        for module, payload in samples.items():
            response = client.post(f"/api/cases/{case_id}/{module}", json=payload, headers=headers)
            assert response.status_code == 200, f"{module}: {response.text}"
            created[module] = response.json()

        order = client.post(
            f"/api/cases/{case_id}/orders",
            json={"hearing_id": created["hearings"]["id"], "order_date": "2026-08-25", "summary": "File response"},
            headers=headers,
        )
        assert order.status_code == 200, order.text
        direction = client.post(
            f"/api/cases/{case_id}/order-directions",
            json={"order_id": order.json()["id"], "direction": "File response", "due_date": "2026-09-01"},
            headers=headers,
        )
        assert direction.status_code == 200, direction.text
        claim_document = client.post(
            f"/api/cases/{case_id}/claim-documents",
            json={"claim_id": created["claims"]["id"], "name": "Claim form", "required": True},
            headers=headers,
        )
        assert claim_document.status_code == 200, claim_document.text
        vote = client.post(
            f"/api/cases/{case_id}/coc-votes",
            json={"meeting_id": created["coc-meetings"]["id"], "member_id": created["coc-members"]["id"], "agenda_key": "agenda-1", "vote": "yes", "voting_share": 100},
            headers=headers,
        )
        assert vote.status_code == 200, vote.text
        tasks = client.get(f"/api/cases/{case_id}/tasks", headers=headers).json()
        assert len(tasks) >= 6  # setup + hearing + claim + two CoC tasks + order direction
        assert any(task["source_type"] == "order-directions" for task in tasks)


def test_roles_firm_views_and_backup(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        admin_headers = _login(client)
        case_id = _create_case(client, admin_headers, "Security Limited")["id"]
        created_user = client.post(
            "/api/admin/users",
            json={"name": "Read Only Reviewer", "email": "reviewer@example.com", "role": "read-only", "password": "Temporary-Password-123"},
            headers=admin_headers,
        )
        assert created_user.status_code == 200, created_user.text
        assert client.put(
            f"/api/admin/cases/{case_id}/assignments",
            json={"user_ids": [created_user.json()["id"]]}, headers=admin_headers,
        ).status_code == 200
        login = client.post("/api/auth/login", json={"email": "reviewer@example.com", "password": "Temporary-Password-123"})
        assert login.status_code == 200, login.text
        read_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        assert client.get("/api/cases", headers=read_headers).status_code == 200
        assert client.post(f"/api/cases/{case_id}/tasks", json={"title": "Not allowed"}, headers=read_headers).status_code == 403

        assert client.get("/api/firm/tasks", headers=admin_headers).status_code == 200
        assert client.get("/api/firm/calendar", headers=admin_headers).status_code == 200
        assert client.get(f"/api/cases/{case_id}/report", headers=admin_headers).status_code == 200
        backup = client.get("/api/admin/backup", headers=admin_headers)
        assert backup.status_code == 200
        assert backup.content.startswith(MAGIC)
        assert unprotect_bytes(backup.content).startswith(b"SQLite format 3")
        restored = client.post(
            "/api/admin/restore",
            files={"file": ("test.casefile-backup", backup.content, "application/octet-stream")},
            headers=admin_headers,
        )
        assert restored.status_code == 200, restored.text
        assert client.get(f"/api/cases/{case_id}", headers=admin_headers).status_code == 200


def test_case_assignments_enforce_api_dashboard_and_firm_view_isolation(tmp_path, monkeypatch):
    _use_database(tmp_path, monkeypatch)
    with TestClient(server.app) as client:
        admin_headers = _login(client)
        first = _create_case(client, admin_headers, "Assigned Limited")
        second = _create_case(client, admin_headers, "Restricted Limited")
        client.post(f"/api/cases/{first['id']}/tasks", json={"title": "Visible assigned task", "due_date": "2026-09-01"}, headers=admin_headers)
        client.post(f"/api/cases/{second['id']}/tasks", json={"title": "Hidden restricted task", "due_date": "2026-09-01"}, headers=admin_headers)

        associate = client.post(
            "/api/admin/users",
            json={"name": "Case Associate", "email": "associate@example.com", "role": "associate", "password": "Temporary-Password-123"},
            headers=admin_headers,
        )
        assert associate.status_code == 200, associate.text
        associate_id = associate.json()["id"]
        assignment = client.put(
            f"/api/admin/cases/{first['id']}/assignments",
            json={"user_ids": [associate_id]}, headers=admin_headers,
        )
        assert assignment.status_code == 200, assignment.text

        login = client.post("/api/auth/login", json={"email": "associate@example.com", "password": "Temporary-Password-123"})
        associate_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        visible_cases = client.get("/api/cases", headers=associate_headers)
        assert [item["id"] for item in visible_cases.json()] == [first["id"]]
        assert client.get(f"/api/cases/{first['id']}", headers=associate_headers).status_code == 200
        assert client.get(f"/api/cases/{second['id']}", headers=associate_headers).status_code == 404
        assert client.get(f"/api/cases/{second['id']}/tasks", headers=associate_headers).status_code == 404
        assert client.post(f"/api/cases/{second['id']}/tasks", json={"title": "URL manipulation"}, headers=associate_headers).status_code == 404
        firm_tasks = client.get("/api/firm/tasks", headers=associate_headers).json()
        assert any(item["title"] == "Visible assigned task" for item in firm_tasks)
        assert all(item["title"] != "Hidden restricted task" for item in firm_tasks)
        dashboard = client.get("/api/dashboard", headers=associate_headers)
        assert dashboard.status_code == 200, dashboard.text
        assert dashboard.json()["counts"]["active_cases"] == 1

        viewer = client.post(
            "/api/admin/users",
            json={"name": "Case Viewer", "email": "viewer@example.com", "role": "viewer", "password": "Temporary-Password-123"},
            headers=admin_headers,
        ).json()
        client.put(f"/api/admin/cases/{first['id']}/assignments", json={"user_ids": [associate_id, viewer["id"]]}, headers=admin_headers)
        viewer_login = client.post("/api/auth/login", json={"email": "viewer@example.com", "password": "Temporary-Password-123"}).json()
        viewer_headers = {"Authorization": f"Bearer {viewer_login['access_token']}"}
        assert client.get(f"/api/cases/{first['id']}", headers=viewer_headers).status_code == 200
        assert client.post(f"/api/cases/{first['id']}/tasks", json={"title": "Viewer write"}, headers=viewer_headers).status_code == 403
