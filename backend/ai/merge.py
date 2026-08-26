"""Deterministic merge for page-chunk extraction results."""

from __future__ import annotations

import re
from typing import Any

from .comparison import FIELD_MAP


def not_found_extraction() -> dict[str, Any]:
    evidence = lambda: {"value": None, "page": None, "source_text": None, "basis": "NOT_FOUND"}
    return {
        "case": {key: evidence() for key in (
            "bench", "court_number", "case_number", "case_type", "section", "order_date",
            "cirp_commencement_date", "order_upload_date",
        )},
        "applicant": {key: evidence() for key in ("name", "cin", "address")},
        "corporate_debtor": {key: evidence() for key in ("name", "cin", "address")},
        "proposed_irp": {key: evidence() for key in (
            "name", "registration_number", "address", "email", "afa_details", "afa_valid_until",
        )},
        "appointed_irp": {key: evidence() for key in (
            "name", "registration_number", "address", "email", "afa_details", "afa_valid_until",
        )},
    }


def _get(value: dict[str, Any], path: str) -> dict[str, Any]:
    current: Any = value
    for part in path.split("."):
        current = current[part]
    return current


def _set(value: dict[str, Any], path: str, evidence: dict[str, Any]) -> None:
    current = value
    parts = path.split(".")
    for part in parts[:-1]:
        current = current[part]
    current[parts[-1]] = evidence


def _normal(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip().casefold().rstrip(".,")


def merge_chunk_extractions(results: list[dict[str, Any]]) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if not results:
        raise ValueError("At least one extraction result is required")
    merged = results[0].copy()
    # Deep reconstruction is provided by field replacement below; model output
    # contains only dictionaries and scalar values.
    import copy
    merged = copy.deepcopy(results[0])
    conflicts: dict[str, list[dict[str, Any]]] = {}
    for path in FIELD_MAP:
        candidates = [_get(result, path) for result in results]
        found = [candidate for candidate in candidates if candidate.get("value") not in (None, "")]
        groups: dict[str, list[dict[str, Any]]] = {}
        for candidate in found:
            groups.setdefault(_normal(candidate["value"]), []).append(candidate)
        if not groups:
            _set(merged, path, {"value": None, "page": None, "source_text": None, "basis": "NOT_FOUND"})
        elif len(groups) == 1:
            same = next(iter(groups.values()))
            chosen = sorted(same, key=lambda item: (item.get("basis") != "EXPLICIT", item.get("page") or 10**9))[0]
            _set(merged, path, chosen)
        else:
            competing = [items[0] for items in groups.values()]
            conflicts[path] = competing
            _set(merged, path, {"value": None, "page": None, "source_text": None, "basis": "NOT_FOUND"})
    return merged, conflicts
