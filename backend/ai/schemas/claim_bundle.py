"""Strict schemas for staged claim-bundle extraction.

Every material fact carries documentary evidence.  Values remain strings at
the AI boundary so deterministic validators can parse dates, money and rates
without floating-point or locale assumptions.
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


SCHEMA_VERSION = "claim_bundle_schema_v1"

DOCUMENT_TYPES = (
    "CLAIM_FORM", "DECLARATION", "VERIFICATION", "AUTHORIZATION",
    "SANCTION_LETTER", "LOAN_AGREEMENT", "FACILITY_AGREEMENT",
    "BOARD_RESOLUTION", "LEDGER", "STATEMENT_OF_ACCOUNT",
    "INTEREST_CALCULATION", "BANK_STATEMENT", "INVOICE",
    "SECURITY_DOCUMENT", "HYPOTHECATION", "MORTGAGE", "GUARANTEE",
    "ARBITRATION_AWARD", "COURT_ORDER", "RECALL_NOTICE",
    "CORRESPONDENCE", "TAX_DOCUMENT", "KYC", "OTHER", "UNKNOWN",
)
DocumentType = Literal[*DOCUMENT_TYPES]
EvidenceBasis = Literal["EXPLICIT", "DERIVED_FROM_MULTIPLE_DOCUMENTS", "INFERRED", "NOT_FOUND"]
Confidence = Literal["HIGH", "MEDIUM", "LOW", "NOT_FOUND"]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceFact(StrictModel):
    value: Optional[str]
    document_id: Optional[str]
    bundle_file: Optional[str]
    document_type: DocumentType
    page: Optional[int]
    source_text: Optional[str]
    basis: EvidenceBasis
    confidence: Confidence


class NamedEvidence(StrictModel):
    name: str
    document_id: str
    bundle_file: str
    page: int
    source_text: str
    document_type: DocumentType
    basis: Literal["EXPLICIT", "DERIVED_FROM_MULTIPLE_DOCUMENTS", "INFERRED"]
    confidence: Confidence


class PageSegment(StrictModel):
    start_page: int = Field(ge=1)
    end_page: int = Field(ge=1)
    document_type: DocumentType
    confidence: Confidence
    title_text: str
    evidence_text: str


class ClaimBundleClassification(StrictModel):
    segments: list[PageSegment]


class ClaimFormExtraction(StrictModel):
    claim_form_type: EvidenceFact
    claim_submission_date: EvidenceFact
    claim_as_on_date: EvidenceFact
    claim_reference: EvidenceFact
    creditor_name: EvidenceFact
    creditor_legal_name: EvidenceFact
    creditor_identifier: EvidenceFact
    creditor_address: EvidenceFact
    email: EvidenceFact
    phone: EvidenceFact
    contact_person: EvidenceFact
    authorized_representative: EvidenceFact
    authorized_representative_designation: EvidenceFact
    creditor_category: EvidenceFact
    total_claimed: EvidenceFact
    principal_claimed: EvidenceFact
    interest_claimed: EvidenceFact
    other_amount_claimed: EvidenceFact
    currency: EvidenceFact
    corporate_debtor_name: EvidenceFact
    nature_of_debt: EvidenceFact
    basis_of_claim: EvidenceFact
    facility_type: EvidenceFact
    secured_status_as_claimed: EvidenceFact
    security_description_as_claimed: EvidenceFact
    guarantors_as_claimed: list[NamedEvidence]
    bank_details: EvidenceFact


class FacilityEvidence(StrictModel):
    facility_type: EvidenceFact
    original_facility_amount: EvidenceFact
    sanction_letter_reference: EvidenceFact
    sanction_date: EvidenceFact
    agreement_date: EvidenceFact
    disbursement_details: EvidenceFact
    contractual_interest_rate: EvidenceFact
    default_interest_rate: EvidenceFact
    penal_interest_terms: EvidenceFact
    repayment_terms: EvidenceFact
    due_date: EvidenceFact
    default_date: EvidenceFact
    npa_date: EvidenceFact
    recall_notice_date: EvidenceFact
    acceleration_date: EvidenceFact
    facility_purpose: EvidenceFact


class SecurityEvidence(StrictModel):
    security_evidenced: EvidenceFact
    security_type: EvidenceFact
    hypothecation_details: EvidenceFact
    mortgage_details: EvidenceFact
    charge_details: EvidenceFact
    collateral: EvidenceFact
    security_value: EvidenceFact


class GuaranteeEvidence(StrictModel):
    guarantor: NamedEvidence
    guarantee_amount: EvidenceFact
    guarantee_date: EvidenceFact
    guarantee_type: EvidenceFact


class ProceedingEvidence(StrictModel):
    proceeding_type: EvidenceFact
    forum: EvidenceFact
    case_reference: EvidenceFact
    notice_date: EvidenceFact
    award_or_order_date: EvidenceFact
    award_amount: EvidenceFact
    costs_awarded: EvidenceFact
    post_award_interest_rate: EvidenceFact
    interest_start_date: EvidenceFact
    status: EvidenceFact


class AmountComponent(StrictModel):
    component_type: Literal["OPENING", "PRINCIPAL", "CONTRACTUAL_INTEREST", "DEFAULT_INTEREST", "PENAL_INTEREST", "COSTS", "OTHER", "PAYMENT", "CREDIT", "RECOVERY"]
    amount: EvidenceFact
    operation: Literal["ADD", "SUBTRACT"]


class AnnexureExtraction(StrictModel):
    facilities: list[FacilityEvidence]
    security: list[SecurityEvidence]
    guarantees: list[GuaranteeEvidence]
    proceedings: list[ProceedingEvidence]
    amount_components: list[AmountComponent]
    referenced_documents: list[str]


class RelationshipSuggestion(StrictModel):
    topic: str
    description: str
    supporting_pages: list[int]
    confidence: Confidence


class MissingDocumentSuggestion(StrictModel):
    document_type: DocumentType
    reason: str
    referenced_on_pages: list[int]


class QuerySuggestion(StrictModel):
    subject: str
    text: str
    reason: str
    supporting_pages: list[int]


class ClaimReconcileExtraction(StrictModel):
    relationships: list[RelationshipSuggestion]
    missing_documents: list[MissingDocumentSuggestion]
    query_suggestions: list[QuerySuggestion]
