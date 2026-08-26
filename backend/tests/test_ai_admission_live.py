"""Explicitly opt-in Groq GPT-OSS-120B benchmark; never runs in normal pytest."""

from __future__ import annotations

from pathlib import Path
import json
import os
import re
import shutil

import pytest

from admission_intake import AdmissionIntakeService
from ai.config import AIConfig
from ai.service import AdmissionAIService
from database import CasefileDatabase
from mca_provider import ManualMcaProvider


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "nclt_orders"
NAMES = ("mahakali", "kshipra_motors", "organic_world")
MODEL = "openai/gpt-oss-120b"
CRITICAL_FIELDS = {
    "case.case_number", "applicant.name", "applicant.cin", "corporate_debtor.name",
    "corporate_debtor.cin", "case.order_date", "case.cirp_commencement_date",
    "appointed_irp.name", "appointed_irp.registration_number",
}
PROVENANCE_PATHS = {
    "case.bench": "case.nclt_bench", "case.court_number": "case.court_number",
    "case.case_number": "case.case_number", "case.section": "case.ibc_section",
    "case.order_date": "case.order_date", "case.cirp_commencement_date": "case.cirp_commencement_date",
    "applicant.name": "applicant.name", "applicant.cin": "applicant.cin",
    "applicant.address": "applicant.address", "corporate_debtor.name": "corporate_debtor.name",
    "corporate_debtor.cin": "corporate_debtor.cin", "corporate_debtor.address": "corporate_debtor.address",
    "appointed_irp.name": "irp.name", "appointed_irp.registration_number": "irp.registration_number",
    "appointed_irp.address": "irp.address", "appointed_irp.email": "irp.email",
    "appointed_irp.afa_valid_until": "irp.afa_valid_until",
}
pytestmark = pytest.mark.skipif(
    os.environ.get("RUN_AI_LIVE_TESTS") != "1"
    or not os.environ.get("GROQ_API_KEY")
    or os.environ.get("GROQ_BILLING_MODE", "unknown").lower() != "free",
    reason="set RUN_AI_LIVE_TESTS=1, GROQ_API_KEY, and GROQ_BILLING_MODE=free for the zero-cost Groq benchmark",
)


def _normal(value):
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold().rstrip(".,")


def _flatten_expected(expected):
    case = expected["case"]
    result = {
        "case.bench": case["nclt_bench"], "case.court_number": case["court_number"],
        "case.case_number": case["case_number"], "case.section": case["ibc_section"],
        "case.order_date": case["order_date"], "case.cirp_commencement_date": case["cirp_commencement_date"],
        "case.order_upload_date": case["order_upload_date"],
    }
    for section in ("applicant", "corporate_debtor"):
        for key in ("name", "cin", "address"):
            result[f"{section}.{key}"] = expected[section][key]
    for key in ("name", "registration_number", "address", "email", "afa_valid_until"):
        result[f"appointed_irp.{key}"] = expected["irp"][key]
    return result


def _word_overlap(left, right):
    words = lambda value: set(re.findall(r"[a-z0-9]{3,}", str(value or "").casefold()))
    expected_words, actual_words = words(left), words(right)
    return len(expected_words & actual_words) / len(expected_words) if expected_words else 1.0


def _metric(values):
    return {"correct": sum(values), "total": len(values),
            "percentage": round(100 * sum(values) / len(values), 2) if values else None}


def test_live_groq_three_fixture_evaluation_and_cache(tmp_path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    intake_service = AdmissionIntakeService(store, tmp_path / "data", ManualMcaProvider())
    config = AIConfig(
        enabled=True, provider="groq", api_key=os.environ["GROQ_API_KEY"], extraction_model=MODEL,
        timeout_seconds=120, billing_mode="free", direct_document_token_limit=4000,
        chunk_target_tokens=1800,
    )
    ai_service = AdmissionAIService(store, tmp_path / "data", config)
    report = {
        "provider": "groq", "model": MODEL, "strict_structured_outputs": True,
        "cases": [], "critical_errors": [], "hallucinations_or_unsupported": [],
        "totals": {"api_calls": 0, "input_tokens": 0, "output_tokens": 0,
                   "latency_ms": 0, "actual_cost": 0.0, "estimated_list_cost": 0.0},
    }
    metrics = {key: [] for key in (
        "overall", "critical", "non_critical", "entity_role", "cin", "date",
        "address", "irp", "not_found", "evidence",
    )}
    cache_result = None

    for name in NAMES:
        source = tmp_path / f"{name}.pdf"
        shutil.copy2(FIXTURE_ROOT / "source" / f"{name}.pdf", source)
        intake = intake_service.create(source, source.name, "groq-live-evaluation")
        job = ai_service.run(intake, "groq-live-evaluation", reanalyze=True)
        locked = json.loads((FIXTURE_ROOT / "expected" / f"{name}.expected.json").read_text(encoding="utf-8"))
        expected_flat = _flatten_expected(locked["expected"])
        rows = []
        for row in job["comparison"]["rows"]:
            field = row["field"]
            if field not in expected_flat:
                continue
            expected_value, ai_value = expected_flat[field], row["ai_value"]
            exact = _normal(expected_value) == _normal(ai_value)
            expected_source = locked.get("provenance", {}).get(PROVENANCE_PATHS.get(field, ""), {})
            expected_page = expected_source.get("page")
            evidence_text = row.get("evidence")
            page_accurate = (row.get("page") == expected_page) if expected_page else (ai_value is None)
            if ai_value is None:
                evidence_supported = expected_value is None
            else:
                evidence_supported = bool(evidence_text) and (
                    _normal(ai_value) in _normal(evidence_text)
                    or _word_overlap(expected_source.get("source_snippet"), evidence_text) >= 0.25
                )
            evidence_accurate = exact and page_accurate and evidence_supported
            field_report = {
                "field": field, "expected": expected_value, "parser": row["parser_value"],
                "gpt_oss_120b": ai_value, "evidence_page": row.get("page"),
                "expected_evidence_page": expected_page, "evidence_text": evidence_text,
                "validation": row["validation_status"], "status": row["comparison_state"],
                "exact": exact, "page_accurate": page_accurate,
                "evidence_supported": evidence_supported, "ai_candidates": row.get("ai_candidates", []),
            }
            rows.append(field_report)
            metrics["overall"].append(exact)
            metrics["critical" if field in CRITICAL_FIELDS else "non_critical"].append(exact)
            if field.endswith(".name"):
                metrics["entity_role"].append(exact)
            if field.endswith(".cin"):
                metrics["cin"].append(exact)
            if "date" in field or field.endswith("afa_valid_until"):
                metrics["date"].append(exact)
            if field.endswith(".address"):
                metrics["address"].append(exact)
            if field.startswith("appointed_irp."):
                metrics["irp"].append(exact)
            if expected_value is None:
                metrics["not_found"].append(exact)
            if expected_value is not None:
                metrics["evidence"].append(evidence_accurate)
            if field in CRITICAL_FIELDS and not exact:
                report["critical_errors"].append({"case": name, **field_report})
            if expected_value is None and ai_value is not None:
                report["hallucinations_or_unsupported"].append(
                    {"case": name, "type": "VALUE_WHERE_EXPECTED_NOT_FOUND", **field_report}
                )
            elif ai_value is not None and (not evidence_supported or not page_accurate):
                report["hallucinations_or_unsupported"].append(
                    {"case": name, "type": "UNSUPPORTED_OR_WRONG_PAGE_EVIDENCE", **field_report}
                )

        case_report = {
            "name": name, "estimated_document_tokens": job["comparison"]["input_plan"]["estimated_document_tokens"],
            "chunking_required": job["comparison"]["input_plan"]["chunking_required"],
            "page_ranges": job["comparison"]["input_plan"]["page_ranges"],
            "chunk_errors": job["comparison"]["input_plan"].get("chunk_errors", []),
            "fields": rows, "exact": sum(item["exact"] for item in rows), "total": len(rows),
            "api_calls": job["api_calls"], "input_tokens": job["input_tokens"],
            "output_tokens": job["output_tokens"], "latency_ms": job["latency_ms"],
            "actual_cost": job["actual_cost"], "estimated_list_cost": job["estimated_list_cost"],
        }
        report["cases"].append(case_report)
        for chunk_error in case_report["chunk_errors"]:
            report["critical_errors"].append({
                "case": name, "type": "STRICT_SCHEMA_REJECTION", **chunk_error,
            })
            report["hallucinations_or_unsupported"].append({
                "case": name, "type": "PROVIDER_REJECTED_ROLE_OR_SCHEMA_OUTPUT", **chunk_error,
            })
        for key in report["totals"]:
            report["totals"][key] += case_report[key] or 0

        if name == "mahakali":
            cached = ai_service.run(intake, "groq-live-evaluation", reanalyze=False)
            assert cached["status"] == "CACHE_HIT"
            assert cached["api_calls"] == cached["input_tokens"] == cached["output_tokens"] == 0
            assert cached["parsed_result"] == job["parsed_result"]
            cache_result = {"hit": True, "new_api_calls": cached["api_calls"],
                            "new_input_tokens": cached["input_tokens"],
                            "new_output_tokens": cached["output_tokens"],
                            "same_extraction": cached["parsed_result"] == job["parsed_result"]}

    report["metrics"] = {key: _metric(values) for key, values in metrics.items()}
    report["cache_test"] = cache_result
    report_path = Path(os.environ.get(
        "AI_EVALUATION_REPORT_PATH",
        FIXTURE_ROOT.parents[3] / "test_reports" / "groq_gpt_oss_120b_admission_evaluation.json",
    ))
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    assert len(report["cases"]) == 3 and report["cache_test"]["hit"] is True
