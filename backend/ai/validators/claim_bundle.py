"""Deterministic validation and reconciliation for claim-bundle AI output."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal, InvalidOperation
import re
from typing import Any

from ai.schemas.claim_bundle import DOCUMENT_TYPES


CIN_RE = re.compile(r"^[LU][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$", re.I)
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
DATE_FIELDS = {"claim_submission_date", "claim_as_on_date", "sanction_date", "agreement_date",
               "due_date", "default_date", "npa_date", "recall_notice_date", "acceleration_date",
               "notice_date", "award_or_order_date", "interest_start_date", "guarantee_date"}
MONEY_FIELDS = {"total_claimed", "principal_claimed", "interest_claimed", "other_amount_claimed",
                "original_facility_amount", "security_value", "guarantee_amount", "award_amount", "costs_awarded"}
RATE_FIELDS = {"contractual_interest_rate", "default_interest_rate", "post_award_interest_rate"}


def parse_money(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    text = str(value).replace(",", "")
    match = re.search(r"-?[0-9]+(?:\.[0-9]+)?", text)
    if not match:
        return None
    try:
        return Decimal(match.group()).quantize(Decimal("0.01"))
    except InvalidOperation:
        return None


def parse_rate(value: Any) -> Decimal | None:
    rate = parse_money(value)
    return rate if rate is not None and Decimal("0") <= rate <= Decimal("100") else None


def normalize_date(value: Any) -> str | None:
    if value in (None, ""):
        return None
    text = str(value).strip().replace("/", "-").replace(".", "-")
    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d-%m-%y", "%d %B %Y", "%d %b %Y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    return None


def validate_fact(field: str, fact: dict[str, Any], page_counts: dict[str, int]) -> dict[str, Any]:
    value, errors = fact.get("value"), []
    basis = fact.get("basis")
    page = fact.get("page")
    document_id = fact.get("document_id")
    if basis == "NOT_FOUND":
        if value not in (None, "") or page is not None or fact.get("source_text") not in (None, ""):
            errors.append("NOT_FOUND must not carry a value, page or evidence")
    else:
        if value in (None, ""):
            errors.append("Supported evidence requires a value")
        if not document_id or page is None or not str(fact.get("source_text") or "").strip() or not str(fact.get("bundle_file") or "").strip():
            errors.append("Value requires bundle file, document, page and source text")
        elif document_id not in page_counts:
            errors.append("Source document is not part of this Claim bundle")
        elif not 1 <= int(page) <= page_counts[document_id]:
            errors.append("Source page is outside the document")
    normalized = value
    if value not in (None, ""):
        if field in MONEY_FIELDS:
            amount = parse_money(value)
            if amount is None or amount < 0:
                errors.append("Invalid non-negative monetary amount")
            else:
                normalized = str(amount)
        elif field in RATE_FIELDS:
            rate = parse_rate(value)
            if rate is None:
                errors.append("Interest rate must be between 0 and 100")
            else:
                normalized = str(rate)
        elif field in DATE_FIELDS:
            parsed = normalize_date(value)
            if not parsed:
                errors.append("Invalid date")
            else:
                normalized = parsed
        elif field == "email" and not EMAIL_RE.match(str(value).strip()):
            errors.append("Invalid email")
        elif field == "creditor_identifier" and len(str(value).replace(" ", "")) == 21 and not CIN_RE.match(str(value).replace(" ", "")):
            errors.append("Invalid CIN format")
    return {"status": "INVALID" if errors else "VALID" if value not in (None, "") else "NOT_FOUND",
            "normalized_value": normalized, "errors": errors}


def validate_stage_facts(payload: Any, page_counts: dict[str, int]) -> dict[str, Any]:
    validations: dict[str, Any] = {}

    def walk(value: Any, path: str = "") -> None:
        if isinstance(value, dict) and {"value", "basis", "document_type", "page", "source_text"} <= set(value):
            field = path.rsplit(".", 1)[-1].split("[")[0]
            validations[path] = validate_fact(field, value, page_counts)
        elif isinstance(value, dict) and {"name", "document_id", "bundle_file", "page", "source_text", "basis", "document_type"} <= set(value):
            field = path.rsplit(".", 1)[-1].split("[")[0]
            validations[path] = validate_fact(field, {**value, "value": value["name"]}, page_counts)
        elif isinstance(value, dict):
            for key, child in value.items():
                walk(child, f"{path}.{key}" if path else key)
        elif isinstance(value, list):
            for index, child in enumerate(value):
                walk(child, f"{path}[{index}]")

    walk(payload)
    invalid = sum(item["status"] == "INVALID" for item in validations.values())
    return {"status": "VALIDATION_FAILED" if invalid else "VALID", "invalid_count": invalid, "facts": validations}


def build_inventory(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_type: dict[str, list[dict[str, Any]]] = {kind: [] for kind in DOCUMENT_TYPES if kind != "UNKNOWN"}
    for segment in segments:
        kind = segment.get("user_document_type") or segment.get("document_type") or "UNKNOWN"
        if kind != "UNKNOWN":
            by_type.setdefault(kind, []).append(segment)
    inventory = []
    for kind, matches in by_type.items():
        pages = sorted({page for item in matches for page in range(int(item["start_page"]), int(item["end_page"]) + 1)})
        inventory.append({
            "document_type": kind, "status": "FOUND" if matches else "NOT_FOUND", "pages": pages,
            "important_references": [item.get("title_text") or item.get("evidence_text") for item in matches if item.get("title_text") or item.get("evidence_text")],
        })
    return inventory


def _source(fact: dict[str, Any] | None) -> dict[str, Any]:
    fact = fact or {}
    return {key: fact.get(key) for key in ("value", "document_id", "bundle_file", "document_type", "page", "source_text", "basis")}


def _conflict(kind: str, topic: str, a: dict[str, Any] | None, b: dict[str, Any] | None) -> dict[str, Any]:
    return {"conflict_type": kind, "topic": topic, "source_a": _source(a), "source_b": _source(b),
            "status": "OPEN", "user_resolution": "", "notes": ""}


def reconcile(form: dict[str, Any], annexures: list[dict[str, Any]], inventory: list[dict[str, Any]]) -> dict[str, Any]:
    facilities = [item for group in annexures for item in group.get("facilities", [])]
    securities = [item for group in annexures for item in group.get("security", [])]
    guarantees = [item for group in annexures for item in group.get("guarantees", [])]
    proceedings = [item for group in annexures for item in group.get("proceedings", [])]
    components = [item for group in annexures for item in group.get("amount_components", [])]
    conflicts: list[dict[str, Any]] = []

    total_fact = form.get("total_claimed", {})
    total = parse_money(total_fact.get("value"))
    component_total = Decimal("0")
    usable_components = 0
    for component in components:
        amount = parse_money(component.get("amount", {}).get("value"))
        if amount is not None:
            usable_components += 1
            component_total += amount if component.get("operation") == "ADD" else -amount
    difference = None if total is None or not usable_components else (component_total - total).quantize(Decimal("0.01"))
    interest_calculation_found = any(item["document_type"] == "INTEREST_CALCULATION" and item["status"] == "FOUND" for item in inventory)
    if total is None:
        arithmetic_status = "REVIEW_REQUIRED"
    elif not usable_components:
        arithmetic_status = "SUPPORTING_CALCULATION_MISSING"
    elif abs(difference) <= Decimal("1.00"):
        arithmetic_status = "RECONCILED"
    else:
        arithmetic_status = "AMOUNT_MISMATCH"

    claimed_security = str(form.get("secured_status_as_claimed", {}).get("value") or "").upper()
    evidenced_security = next((item for item in securities if str(item.get("security_evidenced", {}).get("value") or "").upper() in {"YES", "TRUE", "EVIDENCED", "SECURED"}
                                or item.get("security_type", {}).get("value")), None)
    if claimed_security == "UNSECURED" and evidenced_security:
        conflicts.append(_conflict("SECURITY_STATUS_CONFLICT", "Security as claimed versus evidenced",
                                   form.get("secured_status_as_claimed"), evidenced_security.get("security_type") or evidenced_security.get("security_evidenced")))

    explicit_components = [form.get(key, {}) for key in ("principal_claimed", "interest_claimed", "other_amount_claimed")]
    form_components = [parse_money(item.get("value")) for item in explicit_components if item.get("value") not in (None, "")]
    if total is not None and form_components and sum(item for item in form_components if item is not None) != total:
        synthetic = {"value": str(sum(item for item in form_components if item is not None)), "document_type": "CLAIM_FORM",
                     "basis": "DERIVED_FROM_MULTIPLE_DOCUMENTS", "source_text": "Sum of explicit Form components"}
        conflicts.append(_conflict("CLAIM_TOTAL_MISMATCH", "Claim total versus explicit Form components", total_fact, synthetic))

    if arithmetic_status == "AMOUNT_MISMATCH":
        synthetic = {"value": str(component_total), "document_type": "STATEMENT_OF_ACCOUNT", "basis": "DERIVED_FROM_MULTIPLE_DOCUMENTS",
                     "source_text": "Deterministic sum of extracted supporting components"}
        conflicts.append(_conflict("CLAIM_VS_LEDGER_MISMATCH", "Claim total versus supporting arithmetic", total_fact, synthetic))
    if arithmetic_status == "SUPPORTING_CALCULATION_MISSING" or not interest_calculation_found:
        conflicts.append(_conflict("MISSING_SUPPORTING_CALCULATION", "Claim amount reconciliation", total_fact, None))

    award = next((item.get("award_amount") for item in proceedings if item.get("award_amount", {}).get("value") not in (None, "")), None)
    if award and total is not None and parse_money(award.get("value")) != total:
        conflicts.append(_conflict("AWARD_VS_CLAIM_RECONCILIATION", "Award amount versus final Form claim", award, total_fact))

    claimed_guarantors = {item.get("name", "").strip().lower() for item in form.get("guarantors_as_claimed", []) if item.get("name")}
    evidenced_guarantors = {item.get("guarantor", {}).get("name", "").strip().lower() for item in guarantees if item.get("guarantor", {}).get("name")}
    if claimed_guarantors and evidenced_guarantors and claimed_guarantors != evidenced_guarantors:
        conflicts.append(_conflict("GUARANTOR_CONFLICT", "Claimed versus evidenced guarantors",
                                   {"value": ", ".join(sorted(claimed_guarantors)), "document_type": "CLAIM_FORM", "basis": "EXPLICIT"},
                                   {"value": ", ".join(sorted(evidenced_guarantors)), "document_type": "GUARANTEE", "basis": "EXPLICIT"}))

    rates = [(parse_rate(item.get("contractual_interest_rate", {}).get("value")), item.get("contractual_interest_rate")) for item in facilities]
    rates = [(rate, fact) for rate, fact in rates if rate is not None]
    if len({rate for rate, _ in rates}) > 1:
        conflicts.append(_conflict("INTEREST_RATE_CONFLICT", "Contractual interest rates", rates[0][1], rates[1][1]))

    return {
        "status": arithmetic_status,
        "claimed_total": str(total) if total is not None else None,
        "supporting_component_total": str(component_total) if usable_components else None,
        "difference": str(difference) if difference is not None else None,
        "component_count": usable_components,
        "conflicts": conflicts,
        "facility_evidence": facilities,
        "security_evidence": securities,
        "guarantee_evidence": guarantees,
        "proceedings": proceedings,
    }
