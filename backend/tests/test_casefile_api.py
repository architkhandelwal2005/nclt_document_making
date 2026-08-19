"""Casefile NCLT Document API tests - auth + templates + generation + exports."""
import io
import os
import zipfile
from pathlib import Path

import pytest
import requests

# Load REACT_APP_BACKEND_URL from frontend/.env if not in env
if not os.environ.get("REACT_APP_BACKEND_URL"):
    env_path = Path(__file__).resolve().parents[2] / "frontend" / ".env"
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            if line.startswith("REACT_APP_BACKEND_URL="):
                os.environ["REACT_APP_BACKEND_URL"] = line.split("=", 1)[1].strip().strip('"')
                break

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "arun@casefile.app"
ADMIN_PASSWORD = "Casefile2026!"


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD})
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    data = r.json()
    assert data["user"]["email"] == ADMIN_EMAIL
    assert data["user"]["role"] == "admin"
    assert isinstance(data["access_token"], str) and len(data["access_token"]) > 20
    return data["access_token"]


@pytest.fixture(scope="session")
def auth_headers(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- Auth ----------------
class TestAuth:
    def test_login_invalid(self):
        r = requests.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": "wrong-pass"})
        assert r.status_code == 401
        assert "detail" in r.json()

    def test_login_success(self, token):
        assert token

    def test_me_requires_token(self):
        r = requests.get(f"{API}/auth/me")
        assert r.status_code == 401

    def test_me_with_token(self, auth_headers):
        r = requests.get(f"{API}/auth/me", headers=auth_headers)
        assert r.status_code == 200
        data = r.json()
        assert data["email"] == ADMIN_EMAIL
        assert data["role"] == "admin"

    def test_templates_requires_auth(self):
        r = requests.get(f"{API}/templates")
        assert r.status_code == 401

    def test_documents_requires_auth(self):
        r = requests.get(f"{API}/documents")
        assert r.status_code == 401


# ---------------- Templates ----------------
class TestTemplates:
    def test_five_templates_with_fields_and_tables(self, auth_headers):
        r = requests.get(f"{API}/templates", headers=auth_headers)
        assert r.status_code == 200
        templates = r.json()
        assert len(templates) == 5
        ids = {t["id"] for t in templates}
        assert ids == {"voting-agenda", "constitution-coc", "notice-first-coc", "notice-second-coc", "loc-filing"}
        for t in templates:
            assert isinstance(t["fields"], list) and len(t["fields"]) > 0
            assert isinstance(t["table_inputs"], list)
            for f in t["fields"]:
                assert {"key", "label"} <= set(f.keys())
            for ti in t["table_inputs"]:
                assert {"key", "label", "columns"} <= set(ti.keys())
                assert isinstance(ti["columns"], list) and ti["columns"]


# ---------------- Document generation + exports ----------------
CONSTITUTION_VALUES = {
    "cd_name": "TEST_ACME Industries Ltd",
    "cin": "U12345MH2020PLC000001",
    "nclt_bench": "Mumbai Bench",
    "cp_ib_number": "CP(IB)-123/MB/2024",
    "claim_cutoff_date": "2025-01-15",
    "loc_date": "2025-02-01",
    "ip_name": "Arun Kumar",
    "ibbi_reg_no": "IBBI/IPA-001/IP-P00001/2024-2025/12345",
}

NOTICE_VALUES = {
    "cd_name": "TEST_ACME Industries Ltd",
    "cirp_order_date": "2025-01-05",
    "meeting_date": "2025-02-10",
    "meeting_time": "11:00",
    "meeting_mode": "Video conference",
    "notice_date": "2025-02-03",
    "ip_name": "Arun Kumar",
    "ibbi_reg_no": "IBBI/IPA-001/IP-P00001/2024-2025/12345",
}


class TestDocumentGeneration:
    def test_missing_required_returns_422(self, auth_headers):
        r = requests.post(f"{API}/documents", json={"template_id": "constitution-coc", "values": {}, "tables": {}}, headers=auth_headers)
        assert r.status_code == 422
        assert "missing" in r.json()["detail"].lower()

    def test_generate_constitution_and_docx_contains_cd_name(self, auth_headers, token):
        payload = {
            "template_id": "constitution-coc",
            "values": CONSTITUTION_VALUES,
            "tables": {"df_creditors": [["Sr. No.", "Financial creditor", "Voting share (%)"], ["1", "TEST_Bank of Foo", "60"], ["2", "TEST_Bank of Bar", "40"]]},
            "notes": "TEST notes",
        }
        r = requests.post(f"{API}/documents", json=payload, headers=auth_headers)
        assert r.status_code == 200, r.text
        doc = r.json()
        doc_id = doc["id"]
        assert doc["template_id"] == "constitution-coc"
        assert doc["company_name"] == CONSTITUTION_VALUES["cd_name"]

        # Persistence check via GET list
        r2 = requests.get(f"{API}/documents", headers=auth_headers)
        assert r2.status_code == 200
        assert any(d["id"] == doc_id for d in r2.json())

        # DOCX via ?token= export endpoint
        r3 = requests.get(f"{API}/documents/{doc_id}/export/docx", params={"token": token})
        assert r3.status_code == 200
        assert len(r3.content) > 10_000
        # verify token replacement — the cd_name text should appear, raw token should not
        zf = zipfile.ZipFile(io.BytesIO(r3.content))
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        assert CONSTITUTION_VALUES["cd_name"] in xml
        assert "{cd_name}" not in xml
        assert "{{cd_name}}" not in xml

        # PDF export
        r4 = requests.get(f"{API}/documents/{doc_id}/export/pdf", params={"token": token})
        assert r4.status_code == 200
        assert r4.content.startswith(b"%PDF")
        assert len(r4.content) > 1_000

    def test_notice_first_coc_table_injected(self, auth_headers, token):
        payload = {
            "template_id": "notice-first-coc",
            "values": NOTICE_VALUES,
            "tables": {
                "df_creditors": [
                    ["Sr. No.", "Financial creditor", "Voting share (%)"],
                    ["1", "TEST_Alpha Bank", "55"],
                    ["2", "TEST_Beta NBFC", "45"],
                ],
                "df_suspended_mgmt": [
                    ["Sr. No.", "Name", "Designation"],
                    ["1", "TEST_John Director", "MD"],
                ],
            },
            "notes": "",
        }
        r = requests.post(f"{API}/documents", json=payload, headers=auth_headers)
        assert r.status_code == 200, r.text
        doc_id = r.json()["id"]

        r2 = requests.get(f"{API}/documents/{doc_id}/export/docx", params={"token": token})
        assert r2.status_code == 200
        zf = zipfile.ZipFile(io.BytesIO(r2.content))
        xml = zf.read("word/document.xml").decode("utf-8", errors="ignore")
        # Row content should be present
        assert "TEST_Alpha Bank" in xml
        assert "TEST_Beta NBFC" in xml
        # A table element should exist referencing our injected content — check tbl tag appears
        assert "<w:tbl" in xml

    def test_export_bad_token_401(self, auth_headers):
        # create quick doc
        r = requests.post(f"{API}/documents", json={"template_id": "constitution-coc", "values": CONSTITUTION_VALUES, "tables": {}}, headers=auth_headers)
        doc_id = r.json()["id"]
        r2 = requests.get(f"{API}/documents/{doc_id}/export/docx", params={"token": "not-a-token"})
        assert r2.status_code == 401
