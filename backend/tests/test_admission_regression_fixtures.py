"""Locked source-derived admission-order regression contracts.

Expected JSON is maintained independently from parser output.  The hash
manifest makes accidental or parser-driven fixture rewriting visible.
"""

from __future__ import annotations

from hashlib import sha256
import json
from pathlib import Path

import pytest

from admission_intake import AdmissionOrderExtractor


FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "nclt_orders"
FIXTURE_NAMES = ("mahakali", "kshipra_motors", "organic_world")


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def test_locked_fixture_hash_manifest_and_review_status():
    manifest = _load_json(FIXTURE_ROOT / "manifest.json")
    assert manifest["status"] == "LOCKED_REGRESSION_FIXTURE_SET"
    for relative_path, expected_hash in manifest["files"].items():
        path = FIXTURE_ROOT / relative_path
        assert path.is_file(), relative_path
        assert sha256(path.read_bytes()).hexdigest() == expected_hash, relative_path
    for name in FIXTURE_NAMES:
        contract = _load_json(FIXTURE_ROOT / "expected" / f"{name}.expected.json")
        assert contract["review_status"] == "LOCKED_REGRESSION_FIXTURE"
        assert contract["parser_output_used"] is False
        assert contract["locked_from_independent_source_review"] is True


def test_locked_user_decisions_are_preserved():
    mahakali = _load_json(FIXTURE_ROOT / "expected" / "mahakali.expected.json")
    assert mahakali["expected"]["corporate_debtor"]["cin"] == "U15499MP2002PTC015006"
    raw_cins = {item["raw"] for item in mahakali["candidate_inventory"]["cin_candidates"]}
    assert "Ul5499MP2002PTC015006" in raw_cins

    organic = _load_json(FIXTURE_ROOT / "expected" / "organic_world.expected.json")
    assert organic["expected"]["corporate_debtor"]["address"] == (
        "Survey No. 145/1/4, Gram Malikhedi, Nemawar Road, Indore, "
        "Madhya Pradesh - 452006"
    )
    addresses = organic["candidate_inventory"]["corporate_debtor_address_candidates"]
    assert any(item["value"].startswith("45/1/4") and not item["selected_as_expected"] for item in addresses)
    assert organic["provenance"]["corporate_debtor.address"]["verification_status"] == "ORDER_DERIVED_NOT_MCA_VERIFIED"

    kshipra = _load_json(FIXTURE_ROOT / "expected" / "kshipra_motors.expected.json")
    assert kshipra["expected"]["applicant"]["address"] == (
        '"Trishul", 3rd Floor, Opposite Samartheshwar Temple, Near Law Garden, '
        "Ellis Bridge, Ahmedabad - 380006, Gujarat"
    )
    branches = kshipra["candidate_inventory"]["other_address_candidates"]
    assert any("Ujjain" in item["value"] and not item["selected_as_expected"] for item in branches)


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_parser_matches_locked_source_derived_contract(name: str):
    contract = _load_json(FIXTURE_ROOT / "expected" / f"{name}.expected.json")
    source = FIXTURE_ROOT / "source" / f"{name}.pdf"
    actual = AdmissionOrderExtractor().extract(source)["selected"]
    actual["applicant"].pop("branch_address", None)
    assert actual == contract["expected"]


@pytest.mark.parametrize("name", FIXTURE_NAMES)
def test_structured_parser_preserves_candidates_and_provenance(name: str):
    source = FIXTURE_ROOT / "source" / f"{name}.pdf"
    result = AdmissionOrderExtractor().extract(source)
    assert result["schema_version"] == "2.0"
    assert result["document"]["pages"]
    assert all(page["blocks"] for page in result["document"]["pages"])
    assert len(result["candidates"]["cin_candidates"]) >= 2
    for path, evidence in result["provenance"].items():
        assert evidence["confidence"] in {"HIGH", "MEDIUM", "LOW", "NOT_FOUND"}, path
        assert "validation_status" in evidence
        assert "source_snippet" in evidence


def test_locked_ambiguities_are_retained_without_unsafe_guessing():
    parser = AdmissionOrderExtractor()
    mahakali = parser.extract(FIXTURE_ROOT / "source" / "mahakali.pdf")
    corrected = next(item for item in mahakali["candidates"]["cin_candidates"] if item["role"] == "CORPORATE_DEBTOR")
    assert corrected["raw"] == "Ul5499MP2002PTC015006"
    assert corrected["value"] == "U15499MP2002PTC015006"
    assert corrected["validation_status"] == "CONFIRMED_POSITIONAL_CORRECTION"

    organic = parser.extract(FIXTURE_ROOT / "source" / "organic_world.pdf")
    addresses = organic["candidates"]["address_candidates"]
    assert any(item["value"].startswith("45/1/4") and not item["selected"] for item in addresses)
    assert organic["provenance"]["irp.email"]["confidence"] == "NOT_FOUND"
    assert organic["selected"]["irp"]["email"] is None

    kshipra = parser.extract(FIXTURE_ROOT / "source" / "kshipra_motors.pdf")
    registrations = kshipra["candidates"]["ibbi_registration_candidates"]
    assert {item["role"] for item in registrations} >= {"PROPOSED_IRP", "APPOINTED_IRP"}
    assert kshipra["proposed_irp"]["registration_number"]["role"] == "PROPOSED_IRP"
    assert kshipra["selected"]["applicant"]["branch_address"].endswith("Madhya Pradesh")
