"""Deterministic tests for the NCLT fetcher; no test accesses the live site."""

from pathlib import Path
import asyncio

import pytest
from fastapi.testclient import TestClient

import server
from database import CasefileDatabase
from nclt_fetcher import (
    ERROR_MESSAGES, NcltFetcherConfig, NcltOrderFetcherService,
    case_reference_matches, parse_case_details, parse_search_results,
    safe_component, source_identifier,
)


FIXTURES = Path(__file__).parent / "fixtures" / "nclt_fetcher"


def _markup(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _request(case_id=None, number="72", year=2025):
    return {
        "case_id": case_id, "case_number": number, "case_year": year,
        "case_type": "16", "case_type_label": "Company Petition IB (IBC)", "bench": "indore",
    }


def test_search_results_are_validated_by_number_and_year():
    results = parse_search_results(_markup("search_results.html"), "https://nclt.gov.in", "72", 2025)
    assert len(results) == 2
    assert [item["exact_reference_match"] for item in results] == [True, False]
    assert results[0]["details_url"].startswith("https://nclt.gov.in/case-details")
    assert case_reference_matches("CP (IB) 72/2025", "72", 2025)
    assert not case_reference_matches("CP (IB) 172/2025", "72", 2025)


def test_case_summary_and_all_proceeding_rows_are_parsed():
    result = parse_case_details(_markup("case_details.html"), "https://nclt.gov.in")
    assert result["case"] == {
        "case_identifier": "INDORE-TEST-72-2025", "case_number": "CP (IB) 72/2025",
        "title": "Axis Bank Limited vs Kshipra Motors Private Limited",
        "applicant": "Axis Bank Limited", "respondent": "Kshipra Motors Private Limited",
        "bench": "Indore Bench", "status": "Disposed",
    }
    assert len(result["proceedings"]) == 3
    assert [row["download_status"] for row in result["proceedings"]] == ["NEW", "NEW", "NO_ORDER"]
    assert [row["order_type"] for row in result["proceedings"]] == ["Final Order", "Interim Order", ""]
    assert result["proceedings"][0]["date"] == "2026-07-29"
    assert result["proceedings"][1]["next_date"] == "2026-07-29"


def test_hearing_date_is_not_confused_with_an_earlier_next_date_column():
    markup = """
    <table><thead><tr><th>Next Date</th><th>Hearing Date</th><th>Purpose</th><th>Order</th></tr></thead>
    <tbody><tr><td>15-09-2026</td><td>01-09-2026</td><td>Hearing</td>
    <td><a href="/orders/view?id=date-order">Interim Order</a></td></tr></tbody></table>
    """
    proceeding = parse_case_details(markup, "https://nclt.gov.in")["proceedings"][0]
    assert proceeding["date"] == "2026-09-01"
    assert proceeding["next_date"] == "2026-09-15"


def test_identifier_and_filename_helpers_are_stable_and_sanitized():
    first = source_identifier("https://nclt.gov.in/orders/view?id=abc", "2026-07-29", "Final Order")
    second = source_identifier("https://nclt.gov.in/orders/view?id=abc", "2026-07-29", "Final Order")
    assert first == second
    assert safe_component("2026-07-29 CP(IB) 72/2025 Final Order") == "2026_07_29_CP_IB_72_2025_Final_Order"


def test_persistent_duplicate_detection_does_not_change_source_expectation(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    service = NcltOrderFetcherService(store, tmp_path / "data", NcltFetcherConfig(headless=True))
    first_id = service._create_run(_request(), "actor")
    first = service.get_run(first_id)
    parsed = parse_case_details(_markup("case_details.html"), "https://nclt.gov.in")
    stored = service._store_result(first, parsed, "actor")
    assert stored["result"]["summary"] == {
        "proceedings_found": 3, "orders_found": 2, "new_orders": 2, "already_downloaded": 0,
    }

    second_id = service._create_run(_request(), "actor")
    second = service.get_run(second_id)
    parsed_again = parse_case_details(_markup("case_details.html"), "https://nclt.gov.in")
    repeated = service._store_result(second, parsed_again, "actor")
    assert repeated["result"]["summary"]["new_orders"] == 0
    # Undownloaded records stay NEW; they are not falsely labelled downloaded.
    assert repeated["result"]["summary"]["already_downloaded"] == 0
    assert {row["record_id"] for row in repeated["result"]["proceedings"] if row["order_url"]} == {
        row["id"] for row in stored["records"]
    }

    # Date and order label are supporting signals, not a safe identity key:
    # two distinct PDFs filed on the same date must both be retained.
    third_id = service._create_run(_request(), "actor")
    distinct = dict(parsed_again["proceedings"][0])
    distinct["order_url"] = "https://nclt.gov.in/orders/view?id=second-order-same-date"
    distinct["source_identifier"] = source_identifier(
        distinct["order_url"], distinct["date"], distinct["order_type"], distinct["source_row"],
    )
    same_day_result = {"case": parsed_again["case"], "proceedings": [distinct]}
    same_day = service._store_result(service.get_run(third_id), same_day_result, "actor")
    assert same_day["result"]["summary"]["new_orders"] == 1


def test_pdf_validation_rejects_html_and_accepts_pdf(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    service = NcltOrderFetcherService(store, tmp_path / "data")
    service._validate_pdf(b"%PDF-1.7\n" + b"0" * 200, "application/pdf")
    with pytest.raises(ValueError, match="valid PDF"):
        service._validate_pdf(b"<html>portal error</html>" * 10, "text/html")
    assert "CAPTCHA" in ERROR_MESSAGES["MANUAL_ACTION_REQUIRED"]


def test_schema_contains_fetch_tracking_and_preserves_existing_modules(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    with store.connect() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
    assert {"nclt_fetch_runs", "nclt_fetch_records", "cases", "documents", "hearings", "orders"} <= tables
    assert version == 5


def test_valid_download_is_registered_once_in_case_documents(tmp_path):
    class FakeResponse:
        ok, status = True, 200
        headers = {"content-type": "application/pdf"}

        def __init__(self, marker):
            self.marker = marker

        async def body(self):
            return b"%PDF-1.7\n" + self.marker.encode("utf-8") + b"valid-order-content" * 20

    class FakeRequest:
        async def get(self, url, timeout=None):
            return FakeResponse(url)

    class FakeContext:
        request = FakeRequest()

    class FakeSession:
        context = FakeContext()

    store = CasefileDatabase(tmp_path / "casefile.db")
    case = store.create_case({"name": "Kshipra Motors Private Limited"}, "actor")
    service = NcltOrderFetcherService(store, tmp_path / "data", NcltFetcherConfig(headless=True))
    run_id = service._create_run(_request(case["id"]), "actor")
    run = service.get_run(run_id)
    service._store_result(run, parse_case_details(_markup("case_details.html"), "https://nclt.gov.in"), "actor")
    service.sessions[run_id] = FakeSession()

    downloaded = asyncio.run(service.download_new(run_id, "actor"))
    assert downloaded["status"] == "COMPLETED"
    assert downloaded["result"]["download_summary"] == {"downloaded": 2, "skipped": 0, "failed": 0, "failures": []}
    documents = store.list_module_records(case["id"], "documents")
    assert len(documents) == 2
    assert all(item["category"] == "NCLT Order" and item["source_type"] == "nclt_public_portal" for item in documents)
    for record in downloaded["records"]:
        assert record["status"] == "DOWNLOADED"
        assert service.get_record_file(record["id"]).read_bytes().startswith(b"%PDF")

    repeated = asyncio.run(service.download_new(run_id, "actor"))
    assert repeated["result"]["download_summary"]["downloaded"] == 0
    assert len(store.list_module_records(case["id"], "documents")) == 2


def test_api_auth_read_only_and_case_access_are_enforced(tmp_path, monkeypatch):
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    service = NcltOrderFetcherService(store, tmp_path / "data", NcltFetcherConfig(headless=True))
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "nclt_fetcher", service)

    payload = {
        "case_number": "72", "case_year": 2025, "case_type": "16",
        "case_type_label": "Company Petition IB (IBC)", "bench": "indore",
    }
    with TestClient(server.app) as client:
        assert client.get("/api/nclt-fetcher/config").status_code == 401
        login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        admin_headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        config = client.get("/api/nclt-fetcher/config", headers=admin_headers)
        assert config.status_code == 200
        assert config.json()["default_bench"] == "indore"
        assert client.post(
            "/api/nclt-fetcher/runs", json={**payload, "case_id": "unavailable-case"}, headers=admin_headers,
        ).status_code == 404

        viewer = client.post(
            "/api/admin/users",
            json={"name": "NCLT Viewer", "email": "nclt-viewer@example.com", "role": "viewer", "password": "Temporary-Password-123"},
            headers=admin_headers,
        )
        viewer_login = client.post(
            "/api/auth/login", json={"email": "nclt-viewer@example.com", "password": "Temporary-Password-123"},
        )
        viewer_headers = {"Authorization": f"Bearer {viewer_login.json()['access_token']}"}
        assert viewer.status_code == 200
        assert client.get("/api/nclt-fetcher/runs", headers=viewer_headers).status_code == 200
        assert client.post("/api/nclt-fetcher/runs", json=payload, headers=viewer_headers).status_code == 403
