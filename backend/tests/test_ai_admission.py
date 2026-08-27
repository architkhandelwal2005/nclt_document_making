"""Non-billable tests for the parallel Admission Order AI pilot."""

from __future__ import annotations

from pathlib import Path
import shutil

import pytest
from fastapi.testclient import TestClient

import server
from admission_intake import AdmissionIntakeService
from ai.comparison import compare_extractions
from ai.config import AIConfig
from ai.groq_provider import GroqProvider
from ai.input_prep import plan_inputs
from ai.merge import merge_chunk_extractions
from ai.provider import AIProvider, AIProviderError, ProviderResult
from ai.openai_provider import OpenAIProvider
from ai.schemas.admission_order import AdmissionOrderExtraction
from ai.service import AdmissionAIService, AIServiceError
from ai.validators.admission_order import validate_extraction
from database import CasefileDatabase
from mca_provider import ManualMcaProvider


FIXTURES = Path(__file__).parent / "fixtures" / "nclt_orders"


def ev(value, page=1, source="Explicit source text", basis="EXPLICIT"):
    return {"value": value, "page": page if value is not None else None,
            "source_text": source if value is not None else None,
            "basis": basis if value is not None else "NOT_FOUND"}


def ai_result(selected):
    case, applicant, debtor, irp = selected["case"], selected["applicant"], selected["corporate_debtor"], selected["irp"]
    blank_irp = {key: ev(None) for key in ("name", "registration_number", "address", "email", "afa_details", "afa_valid_until")}
    return {
        "case": {
            "bench": ev(case["nclt_bench"]), "court_number": ev(case["court_number"]),
            "case_number": ev(case["case_number"]), "case_type": ev("Company Petition IB (IBC)"),
            "section": ev(case["ibc_section"]), "order_date": ev(case["order_date"]),
            "cirp_commencement_date": ev(case["cirp_commencement_date"]),
            "order_upload_date": ev(case["order_upload_date"]),
        },
        "applicant": {key: ev(applicant.get(key)) for key in ("name", "cin", "address")},
        "corporate_debtor": {key: ev(debtor.get(key)) for key in ("name", "cin", "address")},
        "proposed_irp": blank_irp,
        "appointed_irp": {
            "name": ev(irp.get("name")), "registration_number": ev(irp.get("registration_number")),
            "address": ev(irp.get("address")), "email": ev(irp.get("email")),
            "afa_details": ev(None), "afa_valid_until": ev(irp.get("afa_valid_until")),
        },
    }


class FakeProvider(AIProvider):
    name = "openai"

    def __init__(self, result):
        self.result = result
        self.calls = 0

    def extract(self, **kwargs):
        self.calls += 1
        return ProviderResult(self.result, "openai", kwargs["model"], 12_000, 1_000, "mock-response")


def staged_intake(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    intake_service = AdmissionIntakeService(store, tmp_path / "data", ManualMcaProvider())
    staged = tmp_path / "mahakali.pdf"
    shutil.copy2(FIXTURES / "source" / "mahakali.pdf", staged)
    return store, intake_service.create(staged, "mahakali.pdf", "actor")


def test_ai_job_cache_usage_and_comparison_do_not_modify_parser(tmp_path):
    store, intake = staged_intake(tmp_path)
    parser_before = intake["extracted"]["selected"]
    provider = FakeProvider(ai_result(parser_before))
    config = AIConfig(enabled=True, api_key="test-only", extraction_model="gpt-5.6-luna")
    service = AdmissionAIService(store, tmp_path / "data", config, provider)

    first = service.run(intake, "actor")
    assert first["status"] == "SUCCEEDED"
    assert first["input_tokens"] == 12_000 and first["output_tokens"] == 1_000
    assert first["estimated_cost"] == pytest.approx(0.0036)
    rows = {row["field"]: row for row in first["comparison"]["rows"]}
    assert rows["corporate_debtor.cin"]["comparison_state"] == "MATCH"
    assert rows["case.case_type"]["comparison_state"] == "AI_ONLY"
    assert rows["proposed_irp.name"]["comparison_state"] == "BOTH_NOT_FOUND"
    assert intake["extracted"]["selected"] == parser_before

    cached = service.run(intake, "actor")
    assert cached["status"] == "CACHE_HIT" and cached["cache_hit_of"] == first["id"]
    assert cached["estimated_cost"] == 0 and provider.calls == 1
    service.run(intake, "actor", reanalyze=True)
    assert provider.calls == 2


def test_disabled_and_failed_ai_leave_deterministic_intake_available(tmp_path):
    store, intake = staged_intake(tmp_path)
    disabled = AdmissionAIService(store, tmp_path / "data", AIConfig(enabled=False))
    with pytest.raises(AIServiceError) as error:
        disabled.run(intake, "actor")
    assert error.value.code == "AI_DISABLED"
    assert intake["review"]["case"]["name"]

    class FailedProvider(AIProvider):
        name = "openai"
        def extract(self, **kwargs):
            raise AIProviderError("API_TIMEOUT", "timed out")

    failed = AdmissionAIService(store, tmp_path / "data", AIConfig(enabled=True, api_key="test"), FailedProvider())
    with pytest.raises(AIServiceError) as provider_error:
        failed.run(intake, "actor", reanalyze=True)
    assert provider_error.value.code == "API_TIMEOUT"
    latest = failed.latest_for_intake(intake["id"])
    assert latest["status"] == "FAILED" and latest["error_code"] == "API_TIMEOUT"


def test_deterministic_validation_rejects_invalid_identifiers_roles_and_dates():
    selected = {
        "case": {"nclt_bench": "Indore Bench", "court_number": "1", "case_number": "CP(IB) 1/2026", "ibc_section": "7", "order_date": "2026-08-03", "cirp_commencement_date": "2026-08-03", "order_upload_date": None},
        "applicant": {"name": "Applicant Ltd", "cin": None, "address": None},
        "corporate_debtor": {"name": "Debtor Ltd", "cin": None, "address": None},
        "irp": {"name": None, "registration_number": None, "address": None, "email": None, "afa_valid_until": None},
    }
    result = ai_result(selected)
    result["corporate_debtor"]["cin"] = ev("MADE-UP-CIN")
    result["corporate_debtor"]["name"] = ev("Applicant Ltd")
    result["case"]["cirp_commencement_date"] = ev("2026-08-02")
    validation = validate_extraction(result, 18)
    assert validation["status"] == "VALIDATION_FAILED"
    assert validation["fields"]["corporate_debtor.cin"]["status"] == "INVALID"
    assert validation["fields"]["corporate_debtor.name"]["status"] == "INVALID"
    assert validation["fields"]["case.cirp_commencement_date"]["status"] == "INVALID"
    comparison = compare_extractions({"selected": selected}, result, validation)
    assert next(row for row in comparison["rows"] if row["field"] == "corporate_debtor.cin")["review_status"] == "AI_ONLY_REVIEW"


def test_ai_schema_migration_and_public_config_never_expose_key(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    service = AdmissionAIService(store, tmp_path / "data", AIConfig(enabled=True, api_key="secret-test-key"))
    public = service.public_config()
    assert public["available"] is True and "api_key" not in public
    with store.connect() as connection:
        tables = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        version = connection.execute("SELECT MAX(version) FROM schema_migrations").fetchone()[0]
        columns = {row["name"] for row in connection.execute("PRAGMA table_info(ai_jobs)")}
    assert version == 9 and "ai_jobs" in tables
    assert {"document_hash", "prompt_version", "schema_version", "input_tokens", "api_calls", "latency_ms",
            "actual_cost", "estimated_list_cost", "estimated_cost", "comparison_json"} <= columns


def test_ai_api_is_authenticated_disabled_safely_and_never_returns_key(tmp_path, monkeypatch):
    store, intake = staged_intake(tmp_path)
    store.ensure_admin(server.ADMIN_ID, server.ADMIN_EMAIL, server.ADMIN_NAME, server.ADMIN_PASSWORD_HASH)
    service = AdmissionAIService(store, tmp_path / "data", AIConfig(enabled=False, api_key="must-not-leak"))
    monkeypatch.setattr(server, "casefile_store", store)
    monkeypatch.setattr(server, "admission_intakes", AdmissionIntakeService(store, tmp_path / "data", ManualMcaProvider()))
    monkeypatch.setattr(server, "admission_ai", service)
    with TestClient(server.app) as client:
        assert client.get("/api/ai/config").status_code == 401
        login = client.post("/api/auth/login", json={"email": server.ADMIN_EMAIL, "password": server.admin_pwd})
        headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
        config = client.get("/api/ai/config", headers=headers)
        assert config.status_code == 200 and "api_key" not in config.json()
        response = client.post(
            f"/api/admission-intakes/{intake['id']}/ai-extraction", json={"reanalyze": False}, headers=headers,
        )
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "AI_DISABLED"


def test_openai_provider_uses_responses_typed_parse_without_network(monkeypatch):
    parsed = AdmissionOrderExtraction.model_validate(ai_result({
        "case": {"nclt_bench": "Indore Bench", "court_number": "1", "case_number": "CP(IB) 1/2026", "ibc_section": "7", "order_date": "2026-08-03", "cirp_commencement_date": "2026-08-03", "order_upload_date": None},
        "applicant": {"name": "Applicant Ltd", "cin": None, "address": None},
        "corporate_debtor": {"name": "Debtor Ltd", "cin": None, "address": None},
        "irp": {"name": None, "registration_number": None, "address": None, "email": None, "afa_valid_until": None},
    }))
    captured = {}

    class Responses:
        def parse(self, **kwargs):
            captured.update(kwargs)
            return type("Response", (), {"output_parsed": parsed, "usage": type("Usage", (), {"input_tokens": 10, "output_tokens": 2})(), "model": "gpt-5.6-luna", "id": "mock"})()

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.responses = Responses()

    monkeypatch.setattr("openai.OpenAI", Client)
    result = OpenAIProvider("test-key", 30).extract(system_prompt="prompt", document_text="--- PAGE 1 ---", model="gpt-5.6-luna")
    assert captured["text_format"] is AdmissionOrderExtraction
    assert captured["store"] is False
    assert captured["client"]["max_retries"] == 1
    assert result.input_tokens == 10 and result.output_tokens == 2


def test_groq_configuration_requires_its_own_key_without_exposing_it(monkeypatch, tmp_path):
    monkeypatch.setenv("AI_ENABLED", "true")
    monkeypatch.setenv("AI_PROVIDER", "groq")
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    monkeypatch.setenv("GROQ_EXTRACTION_MODEL", "openai/gpt-oss-120b")
    config = AIConfig.from_environment()
    assert config.provider == "groq" and config.extraction_model == "openai/gpt-oss-120b"
    assert config.public()["configured"] is False and "api_key" not in config.public()
    service = AdmissionAIService(CasefileDatabase(tmp_path / "casefile.db"), tmp_path / "data", config)
    with pytest.raises(AIServiceError) as error:
        service._provider_instance()
    assert error.value.code == "GROQ_API_KEY_NOT_CONFIGURED"


def test_groq_provider_uses_official_sdk_strict_schema_without_network(monkeypatch):
    parsed = ai_result({
        "case": {"nclt_bench": "Indore Bench", "court_number": "1", "case_number": "CP(IB) 1/2026", "ibc_section": "7", "order_date": "2026-08-03", "cirp_commencement_date": "2026-08-03", "order_upload_date": None},
        "applicant": {"name": "Applicant Ltd", "cin": None, "address": None},
        "corporate_debtor": {"name": "Debtor Ltd", "cin": None, "address": None},
        "irp": {"name": None, "registration_number": None, "address": None, "email": None, "afa_valid_until": None},
    })
    captured = {}

    class Completions:
        def create(self, **kwargs):
            import json
            captured.update(kwargs)
            message = type("Message", (), {"content": json.dumps(parsed)})()
            usage = type("Usage", (), {"prompt_tokens": 321, "completion_tokens": 123})()
            return type("Response", (), {"choices": [type("Choice", (), {"message": message})()],
                                           "usage": usage, "model": "openai/gpt-oss-120b", "id": "groq-mock"})()

    class Client:
        def __init__(self, **kwargs):
            captured["client"] = kwargs
            self.chat = type("Chat", (), {"completions": Completions()})()

    monkeypatch.setattr("groq.Groq", Client)
    result = GroqProvider("test-key", 30).extract(
        system_prompt="prompt", document_text="--- PAGE 1 ---", model="openai/gpt-oss-120b",
    )
    schema_config = captured["response_format"]["json_schema"]
    assert captured["model"] == "openai/gpt-oss-120b"
    assert schema_config["strict"] is True and schema_config["schema"]["additionalProperties"] is False
    assert captured["store"] is False and captured["client"]["max_retries"] == 0
    assert result.provider == "groq" and result.input_tokens == 321 and result.output_tokens == 123
    assert result.api_calls == 1 and result.latency_ms >= 0


def test_page_aware_groq_plan_and_merge_retain_competing_candidates(tmp_path):
    _, intake = staged_intake(tmp_path)
    plan = plan_inputs(intake["extracted"], provider="groq", direct_token_limit=4000, chunk_target_tokens=1800)
    assert plan.estimated_document_tokens == 7336
    assert plan.chunking_required and len(plan.chunks) == 5
    assert plan.page_ranges[0][0] == 1 and plan.page_ranges[-1][1] == 18
    assert sum(chunk.count("--- PAGE ") for chunk in plan.chunks) == 18

    first = ai_result(intake["extracted"]["selected"])
    second = ai_result(intake["extracted"]["selected"])
    first["corporate_debtor"]["address"] = ev("Address A", 4, "registered office Address A")
    second["corporate_debtor"]["address"] = ev("Address B", 5, "registered office Address B")
    merged, conflicts = merge_chunk_extractions([first, second])
    assert merged["corporate_debtor"]["address"]["basis"] == "NOT_FOUND"
    assert {item["value"] for item in conflicts["corporate_debtor.address"]} == {"Address A", "Address B"}
    comparison = compare_extractions(intake["extracted"], merged, validate_extraction(merged, 18), conflicts)
    row = next(item for item in comparison["rows"] if item["field"] == "corporate_debtor.address")
    assert row["comparison_state"] == "CONFLICT" and len(row["ai_candidates"]) == 2


def test_groq_free_accounting_and_cache_make_no_second_provider_call(tmp_path):
    store, intake = staged_intake(tmp_path)

    class FakeGroq(AIProvider):
        name = "groq"
        def __init__(self):
            self.calls = 0
        def extract(self, **kwargs):
            self.calls += 1
            return ProviderResult(ai_result(intake["extracted"]["selected"]), "groq",
                                  "openai/gpt-oss-120b", 1000, 200, f"mock-{self.calls}", 1, 25)

    provider = FakeGroq()
    config = AIConfig(enabled=True, provider="groq", api_key="test-only",
                      extraction_model="openai/gpt-oss-120b", billing_mode="free",
                      direct_document_token_limit=4000, chunk_target_tokens=1800)
    service = AdmissionAIService(store, tmp_path / "data", config, provider)
    first = service.run(intake, "actor")
    assert first["api_calls"] == 5 and provider.calls == 5 and first["latency_ms"] == 125
    assert first["actual_cost"] == 0 and first["estimated_list_cost"] == pytest.approx(0.00135)
    cached = service.run(intake, "actor")
    assert cached["status"] == "CACHE_HIT" and cached["api_calls"] == 0
    assert cached["input_tokens"] == 0 and cached["output_tokens"] == 0 and provider.calls == 5


def test_groq_schema_rejected_chunk_is_not_guessed_and_remaining_chunks_continue(tmp_path):
    store, intake = staged_intake(tmp_path)

    class PartiallyRejectedGroq(AIProvider):
        name = "groq"
        def __init__(self):
            self.calls = 0
        def extract(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise AIProviderError(
                    "INVALID_STRUCTURED_RESPONSE", "strict schema rejected applicant role fields",
                    api_calls=1, latency_ms=10,
                )
            return ProviderResult(ai_result(intake["extracted"]["selected"]), "groq",
                                  "openai/gpt-oss-120b", 100, 20, f"partial-{self.calls}", 1, 10)

    provider = PartiallyRejectedGroq()
    config = AIConfig(enabled=True, provider="groq", api_key="test-only",
                      extraction_model="openai/gpt-oss-120b", billing_mode="free",
                      direct_document_token_limit=4000, chunk_target_tokens=1800)
    job = AdmissionAIService(store, tmp_path / "data", config, provider).run(intake, "actor")
    assert job["status"] == "REVIEW_REQUIRED" and job["error_code"] == "PARTIAL_STRUCTURED_OUTPUT_FAILURE"
    assert job["api_calls"] == 5 and provider.calls == 5
    assert job["comparison"]["input_plan"]["chunk_errors"][0]["pages"] == [1, 5]
    assert job["actual_cost"] == 0
