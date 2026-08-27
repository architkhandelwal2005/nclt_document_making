"""Free, mocked regression tests for the staged Claims AI pipeline."""

from __future__ import annotations

from pathlib import Path
from hashlib import sha256
import json

import pytest

from ai.claim_bundle_service import ClaimBundleError, ClaimBundleService
from ai.config import AIConfig
from ai.provider import AIProvider, ProviderResult
from ai.validators.claim_bundle import reconcile
from claims_workflow import ClaimsWorkflow
from database import CasefileDatabase
from pypdf import PdfReader


def fact(value, document_id, document_type, source="Explicit supporting text"):
    return {
        "value": value, "document_id": document_id if value is not None else None,
        "bundle_file": "test-bundle", "document_type": document_type,
        "page": 1 if value is not None else None, "source_text": source if value is not None else None,
        "basis": "EXPLICIT" if value is not None else "NOT_FOUND",
        "confidence": "HIGH" if value is not None else "NOT_FOUND",
    }


def named(name, document_id, document_type):
    return {"name": name, "document_id": document_id, "bundle_file": "test-bundle", "page": 1,
            "source_text": name, "document_type": document_type, "basis": "EXPLICIT", "confidence": "HIGH"}


class StagedProvider(AIProvider):
    name = "groq"

    def __init__(self):
        self.calls = []

    def extract(self, **kwargs):
        self.calls.append(kwargs["schema_name"])
        text, schema_name = kwargs["document_text"], kwargs["schema_name"]
        marker = text.split("|", 2)[1].strip() if "|" in text else "bundle"
        if schema_name == "claim_bundle_classification":
            if "form.txt" in text:
                kind = "CLAIM_FORM"
            elif "sanction.txt" in text:
                kind = "SANCTION_LETTER"
            else:
                kind = "ARBITRATION_AWARD"
            parsed = {"segments": [{"start_page": 1, "end_page": 1, "document_type": kind,
                                      "confidence": "HIGH", "title_text": kind, "evidence_text": kind}]}
        elif schema_name == "claim_form_extraction":
            document_id = marker
            values = {
                "claim_form_type": "Form C", "claim_submission_date": "17-08-2026", "claim_as_on_date": "31-07-2026",
                "claim_reference": None, "creditor_name": "Samunnati Finance Private Limited",
                "creditor_legal_name": "Samunnati Finance Private Limited", "creditor_identifier": "U65990TN2021PTC146392",
                "creditor_address": "Chennai", "email": "vasudevan.s@samunnati.com", "phone": None,
                "contact_person": "Dr Vasudevan S", "authorized_representative": "Dr Vasudevan S",
                "authorized_representative_designation": "Legal Counsel", "creditor_category": "FINANCIAL_CREDITOR",
                "total_claimed": "6,08,55,576", "principal_claimed": None, "interest_claimed": None,
                "other_amount_claimed": None, "currency": "INR", "corporate_debtor_name": "Mahakali Foods Private Limited",
                "nature_of_debt": "Financial debt", "basis_of_claim": "Long Term Loan", "facility_type": "Long Term Loan",
                "secured_status_as_claimed": "UNSECURED", "security_description_as_claimed": None,
                "bank_details": "IDFC First Bank",
            }
            parsed = {key: fact(value, document_id, "CLAIM_FORM") for key, value in values.items()}
            parsed["guarantors_as_claimed"] = [named("Pankaj Saha", document_id, "CLAIM_FORM"), named("Paritosh Saha", document_id, "CLAIM_FORM")]
        elif schema_name == "claim_annexure_extraction":
            document_id = marker
            blank_facility = {key: fact(None, document_id, "SANCTION_LETTER") for key in (
                "facility_type", "original_facility_amount", "sanction_letter_reference", "sanction_date", "agreement_date",
                "disbursement_details", "contractual_interest_rate", "default_interest_rate", "penal_interest_terms",
                "repayment_terms", "due_date", "default_date", "npa_date", "recall_notice_date", "acceleration_date", "facility_purpose")}
            if "sanction.txt" in text:
                blank_facility.update({
                    "facility_type": fact("Long Term Loan", document_id, "SANCTION_LETTER"),
                    "original_facility_amount": fact("3,00,00,000", document_id, "SANCTION_LETTER"),
                    "sanction_letter_reference": fact("SAMFIN/AE/0234/2021-22", document_id, "SANCTION_LETTER"),
                    "sanction_date": fact("20-09-2021", document_id, "SANCTION_LETTER"),
                    "agreement_date": fact("01-10-2021", document_id, "LOAN_AGREEMENT"),
                    "contractual_interest_rate": fact("19%", document_id, "SANCTION_LETTER"),
                })
                security = [{
                    "security_evidenced": fact("YES", document_id, "HYPOTHECATION"),
                    "security_type": fact("HYPOTHECATION", document_id, "HYPOTHECATION"),
                    "hypothecation_details": fact("Stock and book debts", document_id, "HYPOTHECATION"),
                    "mortgage_details": fact(None, document_id, "MORTGAGE"), "charge_details": fact(None, document_id, "SECURITY_DOCUMENT"),
                    "collateral": fact("Stock and book debts", document_id, "HYPOTHECATION"),
                    "security_value": fact(None, document_id, "HYPOTHECATION"),
                }]
                guarantees = [{"guarantor": named(name, document_id, "GUARANTEE"), "guarantee_amount": fact(None, document_id, "GUARANTEE"),
                                "guarantee_date": fact("01-10-2021", document_id, "GUARANTEE"),
                                "guarantee_type": fact("PERSONAL", document_id, "GUARANTEE")} for name in ("Pankaj Saha", "Paritosh Saha")]
                parsed = {"facilities": [blank_facility], "security": security, "guarantees": guarantees,
                          "proceedings": [], "amount_components": [], "referenced_documents": []}
            else:
                proceeding = {key: fact(None, document_id, "ARBITRATION_AWARD") for key in (
                    "proceeding_type", "forum", "case_reference", "notice_date", "award_or_order_date", "award_amount",
                    "costs_awarded", "post_award_interest_rate", "interest_start_date", "status")}
                proceeding.update({"proceeding_type": fact("ARBITRATION", document_id, "ARBITRATION_AWARD"),
                                   "case_reference": fact("ACP No. 6 of 2024", document_id, "ARBITRATION_AWARD"),
                                   "award_or_order_date": fact("05-08-2024", document_id, "ARBITRATION_AWARD"),
                                   "award_amount": fact("4,02,52,695", document_id, "ARBITRATION_AWARD"),
                                   "costs_awarded": fact("1,00,000", document_id, "ARBITRATION_AWARD"),
                                   "post_award_interest_rate": fact("18%", document_id, "ARBITRATION_AWARD")})
                parsed = {"facilities": [], "security": [], "guarantees": [], "proceedings": [proceeding],
                          "amount_components": [], "referenced_documents": ["Statement of Account"]}
        else:
            parsed = {"relationships": [], "missing_documents": [{"document_type": "INTEREST_CALCULATION",
                       "reason": "Referenced calculation not found", "referenced_on_pages": [1]}],
                      "query_suggestions": []}
        return ProviderResult(parsed, "groq", kwargs["model"], 100, 25, "mock", 1, 5)


def setup_claim(tmp_path: Path):
    store = CasefileDatabase(tmp_path / "casefile.db")
    store.ensure_admin("actor", "actor@example.com", "Actor", "hash")
    case = store.create_case({"name": "Mahakali Foods Private Limited", "case_number": "CP(IB) 1/2026"}, "actor")
    workflow = ClaimsWorkflow(store, tmp_path / "data")
    claim = workflow.create(case["id"], {"received_date": "2026-08-17", "creditor_name": "Manual creditor",
        "creditor_category": "FINANCIAL_CREDITOR", "form_type": "Form C", "claimed_amount": 1,
        "email": "manual@example.com"}, "actor")
    for name in ("form.txt", "sanction.txt", "award.txt"):
        path = tmp_path / "data" / "claims" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"{name} explicit claim evidence " * 20, encoding="utf-8")
        store.create_module_record(case["id"], "documents", {"name": name, "category": "Claim Bundle", "status": "filed",
            "storage_path": str(path.relative_to(tmp_path / "data")), "mime_type": "text/plain", "source_type": "claim-upload",
            "linked_type": "claim", "linked_id": claim["id"], "metadata": {}}, "actor")
    return store, case["id"], claim["id"]


def test_staged_bundle_separates_assertions_evidence_and_conflicts(tmp_path):
    store, case_id, claim_id = setup_claim(tmp_path)
    provider = StagedProvider()
    service = ClaimBundleService(store, tmp_path / "data", AIConfig(enabled=True, provider="groq", api_key="test",
                                 extraction_model="openai/gpt-oss-120b", billing_mode="free"), provider)
    bundle = service.create_bundle(case_id, claim_id, "actor")
    result = service.analyze(case_id, claim_id, bundle["id"], "actor")
    kinds = {item["document_type"] for item in result["segments"]}
    assert {"CLAIM_FORM", "SANCTION_LETTER", "ARBITRATION_AWARD"} <= kinds
    conflicts = {item["conflict_type"] for item in result["conflicts"]}
    assert {"SECURITY_STATUS_CONFLICT", "AWARD_VS_CLAIM_RECONCILIATION", "MISSING_SUPPORTING_CALCULATION"} <= conflicts
    assert result["reconciliation"]["status"] == "SUPPORTING_CALCULATION_MISSING"
    assert next(item for item in result["inventory"] if item["document_type"] == "SANCTION_LETTER")["status"] == "FOUND"
    assert any(item["subject"] == "Clarify security status" for item in result["query_suggestions"])
    with store.connect() as connection:
        layers = {row[0] for row in connection.execute("SELECT DISTINCT layer FROM claim_evidence_facts WHERE bundle_id=?", (bundle["id"],))}
    assert layers == {"CLAIMANT_ASSERTION", "SUPPORTING_EVIDENCE"}
    assert "claim_form_extraction" in provider.calls and "claim_reconciliation" in provider.calls


def test_granular_cache_and_review_never_auto_accept_conflicts(tmp_path):
    store, case_id, claim_id = setup_claim(tmp_path)
    provider = StagedProvider()
    service = ClaimBundleService(store, tmp_path / "data", AIConfig(enabled=True, provider="groq", api_key="test",
                                 extraction_model="openai/gpt-oss-120b", billing_mode="free"), provider)
    bundle = service.create_bundle(case_id, claim_id, "actor")
    service.analyze(case_id, claim_id, bundle["id"], "actor")
    calls = len(provider.calls)
    cached = service.analyze(case_id, claim_id, bundle["id"], "actor")
    assert len(provider.calls) == calls and cached["cache_hits_this_run"] == calls
    assert cached["review"]["reanalysis_delta"]["baseline_bundle_id"] == bundle["id"]
    assert cached["review"]["reanalysis_delta"]["changed_facts"] == []
    confirmed = service.review(case_id, claim_id, bundle["id"], [], "actor",
                               accept_all_non_conflicting=True, confirm=True)
    assert confirmed["status"] == "CONFIRMED_WITH_OPEN_ISSUES"
    claim = ClaimsWorkflow(store, tmp_path / "data").get(case_id, claim_id)
    assert claim["creditor_name"] == "Samunnati Finance Private Limited"
    assert claim["claimed_amount"] == 1  # award/claim conflict prevents bulk acceptance
    assert claim["status"] == "RECEIVED"  # AI never admits or rejects


def test_ai_disabled_leaves_manual_claim_and_schema_intact(tmp_path):
    store, case_id, claim_id = setup_claim(tmp_path)
    service = ClaimBundleService(store, tmp_path / "data", AIConfig(enabled=False))
    bundle = service.create_bundle(case_id, claim_id, "actor")
    with pytest.raises(ClaimBundleError) as exc:
        service.analyze(case_id, claim_id, bundle["id"], "actor")
    assert exc.value.code == "AI_DISABLED"
    assert ClaimsWorkflow(store, tmp_path / "data").get(case_id, claim_id)["claimed_amount"] == 1
    with store.connect() as connection:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        columns = {row[1] for row in connection.execute("PRAGMA table_info(ai_jobs)")}
    assert {"claim_bundles", "claim_bundle_segments", "claim_evidence_facts", "claim_conflicts"} <= tables
    assert {"claim_id", "bundle_id", "page_start", "page_end", "input_fingerprint"} <= columns


def test_independently_reviewed_mahakali_metadata_and_arithmetic():
    fixture_dir = Path(__file__).parent / "fixtures" / "claims_real"
    expected = json.loads((fixture_dir / "mahakali.expected.json").read_text(encoding="utf-8"))
    components = expected["amount_reconciliation"]
    calculated = sum(float(components[key]) for key in ("principal_outstanding", "total_interest_due", "late_charge_due"))
    assert calculated == pytest.approx(float(components["calculated_total"]))
    assert abs(calculated - float(components["form_total"])) <= 1
    assert {"SECURITY_STATUS_CONFLICT", "INTEREST_RATE_CONFLICT", "AWARD_VS_CLAIM_RECONCILIATION"} <= set(expected["expected_discrepancies"])
    source = fixture_dir / expected["fixture"]["file_name"]
    if source.exists():
        assert sha256(source.read_bytes()).hexdigest() == expected["fixture"]["sha256"]
        assert len(PdfReader(str(source)).pages) == expected["fixture"]["page_count"]


def test_mahakali_style_rounding_reconciles_without_conflating_award_or_security():
    form = {"total_claimed": fact("6,08,55,576", "form", "CLAIM_FORM"),
            "secured_status_as_claimed": fact("UNSECURED", "form", "CLAIM_FORM"),
            "principal_claimed": fact(None, "form", "CLAIM_FORM"),
            "interest_claimed": fact(None, "form", "CLAIM_FORM"),
            "other_amount_claimed": fact(None, "form", "CLAIM_FORM"), "guarantors_as_claimed": []}
    security = {"security_evidenced": fact("YES", "loan", "HYPOTHECATION"),
                "security_type": fact("HYPOTHECATION", "loan", "HYPOTHECATION")}
    award = {"award_amount": fact("4,02,52,695", "award", "ARBITRATION_AWARD")}
    components = [
        {"component_type": "PRINCIPAL", "operation": "ADD", "amount": fact("2,86,48,224", "working", "INTEREST_CALCULATION")},
        {"component_type": "CONTRACTUAL_INTEREST", "operation": "ADD", "amount": fact("2,32,10,701.49", "working", "INTEREST_CALCULATION")},
        {"component_type": "PENAL_INTEREST", "operation": "ADD", "amount": fact("89,96,650.84", "working", "INTEREST_CALCULATION")},
    ]
    result = reconcile(form, [{"facilities": [], "security": [security], "guarantees": [],
                               "proceedings": [award], "amount_components": components}],
                       [{"document_type": "INTEREST_CALCULATION", "status": "FOUND"}])
    assert result["status"] == "RECONCILED" and result["difference"] == "0.33"
    conflicts = {item["conflict_type"] for item in result["conflicts"]}
    assert {"SECURITY_STATUS_CONFLICT", "AWARD_VS_CLAIM_RECONCILIATION"} <= conflicts
    assert "CLAIM_VS_LEDGER_MISMATCH" not in conflicts
