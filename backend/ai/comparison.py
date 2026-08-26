"""Field-by-field deterministic parser-versus-AI comparison."""

from __future__ import annotations

from typing import Any
import re


FIELD_MAP = {
    "case.bench": ("case.nclt_bench", "case.nclt_bench", "Bench"),
    "case.court_number": ("case.court_number", "case.court_number", "Court number"),
    "case.case_number": ("case.case_number", "case.petition_number", "Case number"),
    "case.case_type": (None, None, "Case type"),
    "case.section": ("case.ibc_section", "case.admission_section", "IBC section"),
    "case.order_date": ("case.order_date", "case.order_date", "Order date"),
    "case.cirp_commencement_date": ("case.cirp_commencement_date", "case.commencement_date", "CIRP commencement date"),
    "case.order_upload_date": ("case.order_upload_date", "case.order_upload_date", "Order upload date"),
    "applicant.name": ("applicant.name", "applicant.name", "Applicant name"),
    "applicant.cin": ("applicant.cin", "applicant.cin", "Applicant CIN"),
    "applicant.address": ("applicant.address", "applicant.address", "Applicant registered address"),
    "corporate_debtor.name": ("corporate_debtor.name", "case.name", "Corporate Debtor name"),
    "corporate_debtor.cin": ("corporate_debtor.cin", "case.cin", "Corporate Debtor CIN"),
    "corporate_debtor.address": ("corporate_debtor.address", "case.registered_address", "Corporate Debtor registered office"),
    "proposed_irp.name": (None, None, "Proposed IRP name"),
    "proposed_irp.registration_number": ("proposed_irp.registration_number.value", None, "Proposed IRP registration"),
    "proposed_irp.address": (None, None, "Proposed IRP address"),
    "proposed_irp.email": (None, None, "Proposed IRP email"),
    "proposed_irp.afa_details": (None, None, "Proposed IRP AFA details"),
    "proposed_irp.afa_valid_until": (None, None, "Proposed IRP AFA validity"),
    "appointed_irp.name": ("irp.name", "irp.name", "Appointed IRP name"),
    "appointed_irp.registration_number": ("irp.registration_number", "irp.registration_number", "Appointed IRP registration"),
    "appointed_irp.address": ("irp.address", "irp.address", "Appointed IRP address"),
    "appointed_irp.email": ("irp.email", "irp.email", "Appointed IRP email"),
    "appointed_irp.afa_details": (None, None, "Appointed IRP AFA details"),
    "appointed_irp.afa_valid_until": ("irp.afa_valid_until", "irp.afa_valid_until", "Appointed IRP AFA validity"),
}


def _get(value: dict[str, Any], path: str | None) -> Any:
    current: Any = value
    if not path:
        return None
    for part in path.split("."):
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current


def _normal(value: Any, path: str) -> str:
    if value in (None, ""):
        return ""
    text = re.sub(r"\s+", " ", str(value)).strip().casefold()
    if path.endswith(".cin") or path.endswith(".registration_number"):
        return re.sub(r"\s+", "", text)
    return re.sub(r"\s*([,;:\-])\s*", r"\1", text).rstrip(".,")


def compare_extractions(parser_result: dict[str, Any], ai_result: dict[str, Any], validation: dict[str, Any],
                        chunk_conflicts: dict[str, list[dict[str, Any]]] | None = None) -> dict[str, Any]:
    selected = parser_result.get("selected", {})
    chunk_conflicts = chunk_conflicts or {}
    rows: list[dict[str, Any]] = []
    counts = {key: 0 for key in ("MATCH", "AI_ONLY", "PARSER_ONLY", "CONFLICT", "BOTH_NOT_FOUND")}
    for ai_path, (parser_path, review_path, label) in FIELD_MAP.items():
        evidence = _get(ai_result, ai_path) or {}
        ai_value = evidence.get("value") if isinstance(evidence, dict) else None
        parser_value = _get(selected, parser_path)
        valid = validation.get("fields", {}).get(ai_path, {"status": "NOT_FOUND", "errors": []})
        competing = chunk_conflicts.get(ai_path, [])
        if competing:
            state, review_status, final_value = "CONFLICT", "CONFLICT_REVIEW", parser_value
        elif parser_value in (None, "") and ai_value in (None, ""):
            state, review_status, final_value = "BOTH_NOT_FOUND", "NOT_FOUND", None
        elif parser_value in (None, ""):
            state, review_status, final_value = "AI_ONLY", "AI_ONLY_REVIEW", None
        elif ai_value in (None, ""):
            state, review_status, final_value = "PARSER_ONLY", "PARSER_ONLY", parser_value
        elif _normal(parser_value, ai_path) == _normal(ai_value, ai_path):
            state = "MATCH"
            review_status = "CONFIRMED_MATCH" if valid["status"] == "VALID" else "CONFLICT_REVIEW"
            final_value = parser_value
        else:
            state, review_status, final_value = "CONFLICT", "CONFLICT_REVIEW", parser_value
        counts[state] += 1
        rows.append({
            "field": ai_path, "label": label, "parser_value": parser_value, "ai_value": ai_value,
            "comparison_state": state, "review_status": review_status,
            "validation_status": valid["status"], "validation_errors": valid.get("errors", []),
            "page": evidence.get("page"), "evidence": evidence.get("source_text"),
            "basis": evidence.get("basis"), "review_path": review_path,
            "final_selected_value": final_value,
            "ai_candidates": competing,
        })
    return {"rows": rows, "counts": counts, "chunk_conflicts": chunk_conflicts}
