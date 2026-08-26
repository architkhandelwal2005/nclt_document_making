"""Deterministic validation for AI-extracted legal facts."""

from __future__ import annotations

from datetime import date
from typing import Any
import re


CIN_PATTERN = re.compile(r"^[LU]\d{5}[A-Z]{2}\d{4}[A-Z]{3}\d{6}$")
IBBI_PATTERN = re.compile(r"^IBBI/IPA-\d{3}/IP-[A-Z]\d{5}/\d{4}-\d{4}/\d{5}$")
EMAIL_PATTERN = re.compile(r"^[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}$", re.I)
CASE_PATTERN = re.compile(r"(?:C\.?P\.?)?\s*\(?IB\)?[^0-9]*\d+[^0-9]+\d{4}", re.I)
DATE_FIELDS = {"case.order_date", "case.cirp_commencement_date", "case.order_upload_date", "proposed_irp.afa_valid_until", "appointed_irp.afa_valid_until"}


def _flatten(value: dict[str, Any], prefix: str = "") -> dict[str, dict[str, Any]]:
    rows: dict[str, dict[str, Any]] = {}
    for key, item in value.items():
        path = f"{prefix}.{key}" if prefix else key
        if isinstance(item, dict) and {"value", "page", "source_text", "basis"} <= set(item):
            rows[path] = item
        elif isinstance(item, dict):
            rows.update(_flatten(item, path))
    return rows


def _valid_date(value: str) -> bool:
    try:
        date.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


def validate_extraction(extraction: dict[str, Any], page_count: int) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    for path, evidence in _flatten(extraction).items():
        value = evidence.get("value")
        page = evidence.get("page")
        source = str(evidence.get("source_text") or "").strip()
        basis = evidence.get("basis")
        errors: list[str] = []
        if value in (None, ""):
            if basis != "NOT_FOUND":
                errors.append("A blank value must use NOT_FOUND basis")
            status = "NOT_FOUND" if not errors else "INVALID"
        else:
            value = str(value).strip()
            if basis == "NOT_FOUND":
                errors.append("A populated value cannot use NOT_FOUND basis")
            if not isinstance(page, int) or page < 1 or page > page_count:
                errors.append("Evidence page is outside the document")
            if not source:
                errors.append("Source evidence is required")
            if path.endswith(".cin") and not CIN_PATTERN.fullmatch(value.upper()):
                errors.append("CIN does not match the 21-character legal format")
            if path.endswith(".registration_number") and not IBBI_PATTERN.fullmatch(value.upper().replace(" ", "")):
                errors.append("IBBI registration number has an invalid format")
            if path.endswith(".email") and not EMAIL_PATTERN.fullmatch(value):
                errors.append("Email address has an invalid format")
            if path in DATE_FIELDS and not _valid_date(value):
                errors.append("Date is not a valid ISO calendar date")
            if path == "case.case_number" and not CASE_PATTERN.search(value):
                errors.append("Case number does not identify an IB case and year")
            if path.endswith(".address") and len(value) < 10:
                errors.append("Address is too short to review safely")
            status = "VALID" if not errors else "INVALID"
        fields[path] = {
            "status": status, "errors": errors, "page": page,
            "source_text": evidence.get("source_text"), "basis": basis,
        }

    role_errors: list[str] = []
    applicant = extraction.get("applicant", {}).get("name", {}).get("value")
    debtor = extraction.get("corporate_debtor", {}).get("name", {}).get("value")
    if applicant and debtor and re.sub(r"\W", "", applicant).casefold() == re.sub(r"\W", "", debtor).casefold():
        role_errors.append("Applicant and Corporate Debtor cannot be the same extracted entity")
        fields["applicant.name"]["status"] = fields["corporate_debtor.name"]["status"] = "INVALID"
    order_date = extraction.get("case", {}).get("order_date", {}).get("value")
    commencement = extraction.get("case", {}).get("cirp_commencement_date", {}).get("value")
    if order_date and commencement and _valid_date(order_date) and _valid_date(commencement) and commencement < order_date:
        role_errors.append("CIRP commencement date cannot precede the order date")
        fields["case.cirp_commencement_date"]["status"] = "INVALID"
    return {
        "status": "VALID" if not role_errors and all(item["status"] != "INVALID" for item in fields.values()) else "VALIDATION_FAILED",
        "fields": fields, "role_errors": role_errors,
    }
