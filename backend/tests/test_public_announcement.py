from pathlib import Path
from zipfile import ZipFile
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from database import CasefileDatabase  # noqa: E402
from public_announcement import clean_registered_address, extract_published_pdf, generate_form_a  # noqa: E402
import server  # noqa: E402


def _values(company="Acme Foods Limited"):
    return {
        "corporate_debtor_name": company,
        "date_of_incorporation": "2002-03-04",
        "registration_authority": "ROC Gwalior",
        "cin": "U15499MP2002PTC015006",
        "registered_and_principal_address": "Registered office, Indore",
        "cirp_commencement_date": "2026-07-31",
        "order_upload_date": "2026-08-03",
        "estimated_closure_date": "2027-01-27",
        "irp_name": "Test Professional",
        "irp_registration_number": "IBBI/IPA-001/IP-P00001/2020-2021/10001",
        "irp_registered_address": "Professional address, Indore",
        "irp_registered_email": "irp@example.test",
        "correspondence_address": "Process address, Indore",
        "process_specific_email": "cirp.acme@example.test",
        "claims_submission_last_date": "2026-08-17",
        "creditor_classes": "No class ascertained.",
        "authorised_representatives": "Not applicable.",
        "forms_weblink": "https://ibbi.gov.in/en/home/downloads",
        "authorised_representative_details": "Not applicable.",
        "announcement_date": "2026-08-06",
        "announcement_place": "Indore",
        "publication_date": "2026-08-06",
        "newspaper_name": "Test Daily",
        "publication_language": "English",
        "edition_or_place": "Indore",
        "afa_valid_until": "2026-12-31",
    }


def test_form_a_is_editable_legal_size_and_contains_no_newspaper_pages(tmp_path):
    output = generate_form_a(_values(), tmp_path / "form-a.docx")
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "FORM A" in document_xml
    assert "Acme Foods Limited" in document_xml
    assert "PUBLIC ANNOUNCEMENT" in document_xml
    assert "CM Targets SP Ahead" not in document_xml
    assert 'w:w="12240"' in document_xml  # 8.5-inch legal page width in twentieths of a point.
    assert 'w:h="20160"' in document_xml  # 14-inch legal page height.


def test_form_a_removes_legacy_share_capital_spill_from_address(tmp_path):
    values = _values()
    values["registered_and_principal_address"] = (
        "74, Chimanganj Mandi, Agar Road, Ujjain - 456001, Madhya Pradesh. "
        "The Nominal Share Capital of the"
    )
    assert clean_registered_address(values["registered_and_principal_address"]) == (
        "74, Chimanganj Mandi, Agar Road, Ujjain - 456001, Madhya Pradesh"
    )
    output = generate_form_a(values, tmp_path / "clean-address.docx")
    with ZipFile(output) as archive:
        document_xml = archive.read("word/document.xml").decode("utf-8")
    assert "The Nominal Share Capital of the" not in document_xml


def test_two_stage_record_preserves_documents_and_gates_downstream(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin("admin", "admin@example.test", "Admin", "unused")
    case = store.create_case({"name": "Acme Foods Limited", "cin": "U15499MP2002PTC015006", "commencement_date": "2026-07-31"}, "admin")
    values = _values()
    draft = store.save_public_announcement_draft(
        case["id"], values, {"cin": {"source_type": "admission_order"}}, "admin",
        {"name": "Public_Announcement_Form_A_Acme_2026-08-06.docx", "storage_path": "files/draft.docx"},
    )
    assert draft["status"] == "DRAFT"
    assert store.get_case(case["id"])["values"].get("pa_date") is None
    store.set_public_announcement_stage(case["id"], "READY_FOR_PUBLICATION", "admin")
    sent = store.set_public_announcement_stage(case["id"], "SENT_FOR_PUBLICATION", "admin")
    assert sent["draft_document_id"]

    extracted = {"method": "pdf_text", "page_count": 1, "fields": values, "warnings": []}
    uploaded = store.store_published_announcement(
        case["id"], "newspaper.pdf", "files/published.pdf", "application/pdf",
        extracted, values, [], "admin",
    )
    assert uploaded["published_document_id"] != uploaded["draft_document_id"]
    assert store.get_case(case["id"])["values"].get("pa_date") is None
    with pytest.raises(ValueError, match="mandatory"):
        store.confirm_published_announcement(case["id"], "admin")

    store.review_published_announcement(case["id"], values, [], "admin")
    confirmed = store.confirm_published_announcement(case["id"], "admin")
    assert confirmed["status"] == "PUBLISHED"
    updated_case = store.get_case(case["id"])
    assert updated_case["values"]["pa_date"] == "2026-08-06"
    assert updated_case["values"]["claim_cutoff_date"] == "2026-08-17"
    claims_deadline = next(row for row in store.list_module_records(case["id"], "deadlines") if row["rule_id"] == "cirp-model-14")
    assert claims_deadline["due_date"] == "2026-08-17"
    assert claims_deadline["evidence_document_id"] == confirmed["published_document_id"]


def test_conflict_requires_explicit_confirmation(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin("admin", "admin@example.test", "Admin", "unused")
    case = store.create_case({"name": "Existing Limited", "commencement_date": "2026-01-01"}, "admin")
    values = _values("Existing Limited")
    store.save_public_announcement_draft(case["id"], values, {}, "admin", {"name": "draft.docx", "storage_path": "draft.docx"})
    store.set_public_announcement_stage(case["id"], "READY_FOR_PUBLICATION", "admin")
    store.set_public_announcement_stage(case["id"], "SENT_FOR_PUBLICATION", "admin")
    conflict = [{"field": "cin", "existing_value": "OLD", "published_value": "NEW"}]
    store.store_published_announcement(case["id"], "published.pdf", "published.pdf", "application/pdf", {"method": "manual_required"}, values, conflict, "admin")
    store.review_published_announcement(case["id"], values, conflict, "admin")
    with pytest.raises(ValueError, match="Explicit conflict confirmation"):
        store.confirm_published_announcement(case["id"], "admin")
    assert store.confirm_published_announcement(case["id"], "admin", True)["status"] == "PUBLISHED"


@pytest.mark.skipif(not Path(r"D:\Downloads\Public Announcement IBBI (1).pdf").exists(), reason="Reference PDF is unavailable")
def test_api_enforces_exact_two_stage_sequence(tmp_path, monkeypatch):
    store = CasefileDatabase(tmp_path / "api.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    case = store.create_case({
        "name": "Stale Workspace Name", "cin": "U15499MP2002PTC015006",
        "registered_address": "Indore", "commencement_date": "2026-07-31",
    }, server.ADMIN_ID)
    store.save_profile(server.ADMIN_ID, {
        "ip_name": "Navin Khandelwal", "ibbi_reg_no": "IBBI/IPA-001/IP-P00703/2017-18/11301",
        "ip_reg_address": "206, Navneet Plaza, Indore", "ip_email": "navink25@yahoo.com",
        "process_email": "cirp.mahakalifoods@gmail.com", "afa_validity": "2026-12-31",
    })
    data_dir = tmp_path / "data"
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "DATA_DIR", data_dir)
    monkeypatch.setattr(server, "CASE_FILES_DIR", data_dir / "files")
    monkeypatch.setattr(server, "_latest_confirmed_intake", lambda _case_id: {"id": "intake", "document_id": "order-doc", "status": "confirmed", "review": {}})
    with TestClient(server.app) as client:
        login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        initial = client.get(f"/api/cases/{case['id']}/public-announcement", headers=headers)
        assert initial.status_code == 200
        incomplete = {
            **initial.json()["draft_data"],
            **_values("Mahakali Foods Private Limited"),
            "date_of_incorporation": "",
            "registration_authority": "",
        }
        rejected = client.post(
            f"/api/cases/{case['id']}/public-announcement/generate",
            json={"values": incomplete}, headers=headers,
        )
        assert rejected.status_code == 422
        assert "date of incorporation" in rejected.json()["detail"]
        assert "registration authority / RoC location" in rejected.json()["detail"]
        values = {**initial.json()["draft_data"], **_values("Mahakali Foods Private Limited")}
        generated = client.post(f"/api/cases/{case['id']}/public-announcement/generate", json={"values": values}, headers=headers)
        assert generated.status_code == 200, generated.text
        assert generated.json()["status"] == "DRAFT"
        documents = client.get(f"/api/cases/{case['id']}/documents", headers=headers)
        assert documents.status_code == 200
        assert any(
            item["name"].startswith("Public_Announcement_Form_A_Mahakali_Foods_Private_Limited_")
            for item in documents.json()
        )
        assert client.post(f"/api/cases/{case['id']}/public-announcement/sent", headers=headers).status_code == 422
        assert client.post(f"/api/cases/{case['id']}/public-announcement/ready", headers=headers).status_code == 200
        assert client.post(f"/api/cases/{case['id']}/public-announcement/sent", headers=headers).status_code == 200
        with Path(r"D:\Downloads\Public Announcement IBBI (1).pdf").open("rb") as source:
            uploaded = client.post(
                f"/api/cases/{case['id']}/public-announcement/published",
                files={"file": ("published.pdf", source, "application/pdf")}, headers=headers,
            )
        assert uploaded.status_code == 200, uploaded.text
        review = uploaded.json()["published_review"]
        review.update({"newspaper_name": "The Free Press Journal", "publication_language": "English"})
        reviewed = client.put(f"/api/cases/{case['id']}/public-announcement/published-review", json={"values": review}, headers=headers)
        assert reviewed.status_code == 200, reviewed.text
        first_confirmation = client.post(f"/api/cases/{case['id']}/public-announcement/confirm", json={"accept_conflicts": False}, headers=headers)
        assert first_confirmation.status_code == 422
        confirmed = client.post(f"/api/cases/{case['id']}/public-announcement/confirm", json={"accept_conflicts": True}, headers=headers)
        assert confirmed.status_code == 200, confirmed.text
        assert confirmed.json()["status"] == "PUBLISHED"


SAMPLE = Path(r"D:\Downloads\Public Announcement IBBI (1).pdf")


@pytest.mark.skipif(not SAMPLE.exists(), reason="User-supplied Public Announcement reference PDF is not present")
def test_reference_pdf_is_read_as_form_and_publication_evidence():
    result = extract_published_pdf(SAMPLE)
    assert result["page_count"] == 4
    assert result["method"] in {"pdf_text", "tesseract_ocr", "manual_required"}
    assert result["fields"]["corporate_debtor_name"] == "MAHAKALI FOODS PRIVATE LIMITED"
    assert result["fields"]["cin"] == "U15499MP2002PTC015006"
    assert result["fields"]["claims_submission_last_date"] == "2026-08-17"
    assert result["fields"]["process_specific_email"] == "cirp.mahakalifoods@gmail.com"
