# Existing Capability Map

This is the pre-workflow-engine capability snapshot used for the CIRP backend-core phase. `REUSE` identifies an existing model or service that the workflow engine links to instead of duplicating.

| CIRP capability | Status | Existing implementation to preserve or reuse |
|---|---|---|
| Authentication and signed sessions | BUILT / REUSE | `backend/server.py`, `backend/database.py` users |
| Roles, case assignments and case isolation | BUILT / REUSE | `get_current_user`, `require_case_access`, `case_user_assignments` |
| Case workspaces and case master | BUILT / REUSE | `cases`, case CRUD and reporting APIs |
| Admission-order upload, extraction, mandatory review and import | BUILT / REUSE | `AdmissionIntakeService`, structured admission parser |
| Admission AI provider abstraction | BUILT / REUSE | `backend/ai` provider, routing, validation and review services |
| NCLT Order Fetcher | BUILT / REUSE | `NcltOrderFetcherService` |
| Public Announcement / Form A | BUILT / REUSE | `public_announcements`, Form A generator and two-stage publication review |
| Claims register and verification | BUILT / PARTIAL / REUSE | `claims_workflow.py`, claim tables and APIs |
| Claims AI bundle analysis | PARTIAL / REUSE | Checkpointed `backend/ai/claim_bundle_*` implementation |
| CoC notices and minutes | BUILT / REUSE | `CocDocumentService`, CoC meeting records and versioned documents |
| Documents and file repository | BUILT / REUSE | `documents`, generated/uploaded files and version links |
| Tasks and staff assignment | BUILT / REUSE | `tasks`, assignment fields and firm views |
| Activity and audit history | BUILT / REUSE | `activity_events`, `audit_logs`, `CasefileDatabase.audit` |
| Local SQLite backup and restore | BUILT / REUSE | consistent database backup and protected restore |
| Workflow master definitions | NOT_BUILT | Added by backend core phase |
| Case-specific workflow execution | NOT_BUILT | Added by backend core phase |
| Event engine and legal chronology | NOT_BUILT | Added by backend core phase |
| Versioned workflow deadline engine | NOT_BUILT | Added by backend core phase |
| Workflow approval/evidence enforcement | NOT_BUILT | Added by backend core phase |
| Valuation / IM / VDR workflow | NOT_BUILT / PARTIAL | Existing generic valuation/documents only; business workflow deferred |
| Avoidance-transaction workflow | NOT_BUILT | Deferred after core proof |
| EOI / PRA / Resolution Plan workflow | NOT_BUILT | Deferred after core proof |
| Closure and archive workflow | NOT_BUILT / PARTIAL | Case archive exists; CIRP closure workflow deferred |

## Reuse boundary

- A workflow step is a case-process control; it links to the existing `tasks` table and is not a second task system.
- Workflow evidence links to the existing `documents` table; it is not a second document repository.
- Confirmed admission and Public Announcement records emit case events; their mature data and document-generation flows remain authoritative.
- The deterministic engine uses versioned database rules. It does not use an AI model for activation, deadlines, transitions, permissions or evidence checks.
