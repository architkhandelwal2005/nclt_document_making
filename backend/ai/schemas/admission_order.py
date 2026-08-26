"""Strict Structured Outputs schema for admission-order extraction."""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict


SCHEMA_VERSION = "admission_order_schema_v1"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceValue(StrictModel):
    value: Optional[str]
    page: Optional[int]
    source_text: Optional[str]
    basis: Literal["EXPLICIT", "INFERRED", "NOT_FOUND"]


class CaseExtraction(StrictModel):
    bench: EvidenceValue
    court_number: EvidenceValue
    case_number: EvidenceValue
    case_type: EvidenceValue
    section: EvidenceValue
    order_date: EvidenceValue
    cirp_commencement_date: EvidenceValue
    order_upload_date: EvidenceValue


class PartyExtraction(StrictModel):
    name: EvidenceValue
    cin: EvidenceValue
    address: EvidenceValue


class IrpExtraction(StrictModel):
    name: EvidenceValue
    registration_number: EvidenceValue
    address: EvidenceValue
    email: EvidenceValue
    afa_details: EvidenceValue
    afa_valid_until: EvidenceValue


class AdmissionOrderExtraction(StrictModel):
    case: CaseExtraction
    applicant: PartyExtraction
    corporate_debtor: PartyExtraction
    proposed_irp: IrpExtraction
    appointed_irp: IrpExtraction
