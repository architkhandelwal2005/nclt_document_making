"""Admission-order staged import tests using the user-supplied reference PDF."""

from pathlib import Path
import json
import shutil

import pytest
from fastapi.testclient import TestClient

import server
from admission_intake import AdmissionIntakeService
from database import CasefileDatabase
from mca_provider import ManualMcaProvider, MockMcaProvider


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "nclt_orders"
SAMPLE = FIXTURE_ROOT / "source" / "mahakali.pdf"
KSHIPRA_SAMPLE = FIXTURE_ROOT / "source" / "kshipra_motors.pdf"


def _expected(name: str) -> dict:
    return json.loads((FIXTURE_ROOT / "expected" / f"{name}.expected.json").read_text(encoding="utf-8"))


def _login(client: TestClient) -> dict:
    response = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
    assert response.status_code == 200
    return {"Authorization": f"Bearer {response.json()['access_token']}"}


def test_mahakali_staged_import_and_idempotency(tmp_path, monkeypatch):
    expected = _expected("mahakali")["expected"]
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    service = AdmissionIntakeService(store, tmp_path / "data", ManualMcaProvider())
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "admission_intakes", service)

    with TestClient(server.app) as client:
        headers = _login(client)
        with SAMPLE.open("rb") as source:
            response = client.post("/api/admission-intakes", files={"file": ("mahakali.pdf", source, "application/pdf")}, headers=headers)
        assert response.status_code == 200, response.text
        intake = response.json()
        review = intake["review"]
        assert intake["page_count"] == 18
        assert review["case"]["name"] == expected["corporate_debtor"]["name"]
        assert intake["extracted"]["provenance"]["case.cin"]["raw"] == "Ul5499MP2002PTC015006"
        assert intake["extracted"]["provenance"]["case.cin"]["value"] == expected["corporate_debtor"]["cin"]
        assert review["case"]["petition_number"] == expected["case"]["case_number"]
        assert review["case"]["order_date"] == expected["case"]["order_date"]
        assert review["applicant"]["cin"] == expected["applicant"]["cin"]
        assert review["irp"]["registration_number"] == expected["irp"]["registration_number"]
        assert len(review["payment_evidence"]) == 2
        assert review["contribution"]["called_amount_paise"] == 10_000_000
        assert intake["mca"]["status"] == "manual_required"

        unacknowledged = client.post(
            f"/api/admission-intakes/{intake['id']}/confirm",
            json={"action": "create", "review": review}, headers=headers,
        )
        assert unacknowledged.status_code == 422
        assert "acknowledge" in unacknowledged.json()["detail"].lower()

        # Model the mandatory human-review correction before canonical import.
        review["case"]["cin"] = expected["corporate_debtor"]["cin"]
        review["case"]["registered_address"] = expected["corporate_debtor"]["address"]
        review["applicant"]["address"] = expected["applicant"]["address"]
        review["irp"].update(expected["irp"])

        confirmed = client.post(
            f"/api/admission-intakes/{intake['id']}/confirm",
            json={"action": "create", "review": review, "review_acknowledged": True}, headers=headers,
        )
        assert confirmed.status_code == 200, confirmed.text
        case_id = confirmed.json()["case"]["id"]
        assert client.get(f"/api/cases/{case_id}/documents", headers=headers).json()[0]["category"] == "NCLT admission order"
        assert len(client.get(f"/api/cases/{case_id}/payment-evidence", headers=headers).json()) == 2
        assert len(client.get(f"/api/cases/{case_id}/document-requirements", headers=headers).json()) >= 10
        assert any(row["source_type"] == "admission_order_intake" for row in client.get(f"/api/cases/{case_id}/tasks", headers=headers).json())
        assert client.post(f"/api/admission-intakes/{intake['id']}/confirm", json={"action": "update", "target_case_id": case_id, "review": review, "review_acknowledged": True}, headers=headers).status_code == 409


def test_mock_mca_provider_is_replaceable():
    provider = MockMcaProvider({"U123": {"company_status": "Active"}})
    assert provider.lookup("u123")["master_data"]["company_status"] == "Active"
    assert provider.lookup("missing")["status"] == "not_found"


def test_kshipra_extraction_and_blank_contribution_payer_import(tmp_path):
    expected = _expected("kshipra_motors")["expected"]
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin("admin", "admin@example.test", "Admin", "unused-test-hash")
    service = AdmissionIntakeService(store, tmp_path / "data", ManualMcaProvider())
    staged = tmp_path / "kshipra-order.pdf"
    shutil.copy2(KSHIPRA_SAMPLE, staged)

    intake = service.create(staged, KSHIPRA_SAMPLE.name, "admin")
    review = intake["review"]

    assert review["case"]["name"] == expected["corporate_debtor"]["name"]
    assert review["case"]["cin"] == expected["corporate_debtor"]["cin"]
    assert review["case"]["registered_address"]
    assert review["applicant"]["name"] == expected["applicant"]["name"]
    assert review["applicant"]["cin"] == expected["applicant"]["cin"]
    assert review["irp"]["afa_valid_until"] == expected["irp"]["afa_valid_until"]
    assert review["mca"]["authorised_capital"] == "2,75,00,000"
    assert review["mca"]["paid_up_capital"] == "2,05,00,000"

    # Regression: the UI previously retained a null payer after a manually
    # entered applicant name, causing a NOT NULL database error on confirmation.
    review["case"]["registered_address"] = expected["corporate_debtor"]["address"]
    review["applicant"]["address"] = expected["applicant"]["address"]
    review["irp"].update(expected["irp"])
    review["contribution"]["payer_name"] = None
    confirmed = service.confirm(intake["id"], review, "admin", "create", review_acknowledged=True)
    assert confirmed["case"]["name"] == expected["corporate_debtor"]["name"]
    with store.connect() as connection:
        contribution = connection.execute(
            "SELECT payer_name FROM contributions WHERE case_id=?", (confirmed["case"]["id"],)
        ).fetchone()
    assert contribution["payer_name"] == expected["applicant"]["name"]
