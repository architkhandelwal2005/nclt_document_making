# Claims-to-CoC Phase 2 Implementation Note

## REUSE

- `claims` remains the canonical Claim Register; existing revisions, decisions, checklists, queries and documents remain authoritative.
- `contacts`, `communications`, `tasks`, `documents`, `activity_events`, `audit_logs`, `case_events`, `case_workflow_steps` and `case_deadlines` remain shared infrastructure.
- `coc_members` remains the operational membership source for existing CoC meeting functionality, but formal members are now populated from confirmed Constitution snapshots.
- The existing `constitution-coc` office template is reused for Constitution Report DOCX artifacts.

## EXTEND

- Claim acknowledgement, classification confirmation, scrutiny, exact paise mirrors, late-Claim review, related-party and security review metadata.
- Immutable List of Creditors snapshots and filing/display records.
- Versioned CoC eligibility, voting calculations, Constitutions, report artifacts and reconstitutions.
- CIRP workflow master and domain events through CIRP-045.

## DEPRECATE / UNUSED

- Free-standing manually entered CoC amounts are not accepted as a formal Constitution source.
- Existing manual `coc_members` records are retained for compatibility; new formal Constitution versions derive members only from admitted Financial Creditor Claim decisions.
- Actual email, regulatory-site and NCLT filing automation remains outside this phase.
