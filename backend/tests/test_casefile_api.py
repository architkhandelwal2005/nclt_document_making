import os
import uuid

import requests


BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "").rstrip("/")


def test_templates_and_document_validation():
    templates = requests.get(f"{BASE_URL}/api/templates", timeout=20)
    assert templates.status_code == 200
    nclt = next(item for item in templates.json() if item["id"] == "nclt-company-particulars")
    assert len(nclt["fields"]) == 8

    invalid = requests.post(
        f"{BASE_URL}/api/documents",
        json={"template_id": nclt["id"], "values": {}},
        timeout=20,
    )
    assert invalid.status_code == 422
    assert "Required fields missing" in invalid.json()["detail"]


def test_generate_persist_and_download_exports():
    token = uuid.uuid4().hex[:8]
    values = {
        "company_name": f"TEST Casefile {token} Ltd",
        "cin": "U12345MH2010PTC000000",
        "registered_address": "1 Test Street, Mumbai",
        "nclt_bench": "Mumbai Bench",
        "case_number": "CP (IB) No. 1/2026",
        "applicant_name": "Test Applicant",
        "professional_name": "Test Professional",
        "date": "19/08/2026",
    }
    created = requests.post(
        f"{BASE_URL}/api/documents",
        json={"template_id": "nclt-company-particulars", "values": values, "notes": "TEST note"},
        timeout=20,
    )
    assert created.status_code == 200
    document = created.json()
    assert document["company_name"] == values["company_name"]
    assert document["notes"] == "TEST note"

    recent = requests.get(f"{BASE_URL}/api/documents", timeout=20)
    assert recent.status_code == 200
    assert any(item["id"] == document["id"] for item in recent.json())

    word = requests.get(f"{BASE_URL}/api/documents/{document['id']}/download/docx", timeout=20)
    assert word.status_code == 200
    assert word.headers["content-type"].startswith("application/vnd.openxmlformats-officedocument.wordprocessingml.document")
    assert word.content[:2] == b"PK"

    pdf = requests.get(f"{BASE_URL}/api/documents/{document['id']}/download/pdf", timeout=20)
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"
    assert pdf.content.startswith(b"%PDF")


def test_unknown_template_and_export_are_404():
    unknown = requests.post(
        f"{BASE_URL}/api/documents", json={"template_id": "missing", "values": {}}, timeout=20
    )
    assert unknown.status_code == 404
    missing_export = requests.get(
        f"{BASE_URL}/api/documents/missing/download/pdf", timeout=20
    )
    assert missing_export.status_code == 404