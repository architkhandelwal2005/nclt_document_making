# CIRP-057--076 implementation note

## Scope

This release implements the case-isolated backend controls for operational
management, valuation, Information Memorandum (IM) and a Virtual Data Room
(VDR). It stops at CIRP-076. It does not implement transaction audit, EOI,
RFRP, resolution-plan selection, deployment, email or calendar automation.

## Reference handling

The supplied office files are read-only references and are intentionally not
included in source control or modified by this release.

| Reference | Determination | Implementation use |
| --- | --- | --- |
| Valuer appointment letters (PDF) | Real office format, not safely editable | Structured appointment/fee/acceptance controls; formal generator is `TEMPLATE_REQUIRED`. |
| Valuation disclosures (`.doc`) | Legacy binary Word format | Declaration review register; conversion and visual template verification are required before document generation. |
| IM (`.doc`) | Legacy binary Word format | Versioned source-linked IM checklist; conversion and verification are required before document generation. |
| Section 19(2) application (`.docx`) | Real editable office pleading | Fact-linked chronology and annexure register; generation remains professional/template review work, not automatic fact invention. |
| CoC confidentiality undertaking (`.docx`) | Real editable undertaking | Undertaking issuance/signature/verification record. |
| CIRP Cost Sheet (`.xlsx`) | Real workbook layout | Existing CoC cost statement is extended with period, type, approval and payment metadata; allocation snapshot uses confirmed CoC voting shares. |
| Valuer quotation email | No body supplied (placeholder only) | `TEMPLATE_REQUIRED`; no language has been fabricated. |

## Confidentiality boundary

`RESTRICTED_VALUATION` and `RESTRICTED_RESOLUTION_PLAN` documents are blocked
from generic viewer downloads. VDR access is a separate recipient grant with a
verified undertaking, explicit folder scope, audit log and revocation path.

## Data rules

- Money is stored as integer paise and displayed as two-decimal INR values.
- A `REVIEW_REQUIRED` status is used where law, eligibility, exception or
  timing needs professional confirmation; the system does not guess a date.
- Final, issued and approved Phase-4 records are immutable unless a controlled
  new version is created.
- VDR metadata indexes existing documents only; it never duplicates document
  bytes.
