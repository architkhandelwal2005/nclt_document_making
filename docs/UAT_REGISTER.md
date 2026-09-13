# User Acceptance Testing Register

Automated testing verifies software behaviour but does not replace office-user acceptance testing (UAT), meaning confirmation by the intended users that the workflow fits their real work.

Use the in-app **Test Feedback** area for reproducible defects. The reporting
format and access rules are in `docs/UAT_FEEDBACK_GUIDE.md`.

| Area | Scope for user testing | User Tested | Notes |
|---|---|---|---|
| Claims Register | Columns, filters, search, status labels, summary figures and opening a claim | PENDING | |
| New Claim Intake | One-minute manual entry, component mismatch warning and mandatory original attachment | PENDING | |
| Claim Documents | Multiple uploads, categories, downloads, checklist and case isolation | PENDING | |
| Claim Verification | Claimed-versus-admitted comparison, conclusions, validations and override reasons | PENDING | |
| Claim Query/Response | Standard editable wording, manual-send record, response notes and linked supporting documents | PENDING | |
| Claim Admission | Fully admitted, partly admitted, rejected and revised decisions | PENDING | |
| Claim Revision History | Original and revised amounts, documents, decisions and chronological timeline | PENDING | |
| List of Creditors | Derived screen, totals and professional DOCX export | PENDING | |
| Claim Bundle Upload | Create one submission from the complete Claim PDF and later-added documents | PENDING | |
| AI Page/Document Classification | Logical page ranges, confidence, titles and user corrections | PENDING | |
| AI Form C Extraction | Form assertions, creditor, category, dates and total without invented breakup | PENDING | |
| AI Annexure Extraction | Facility, security, guarantee, statement and award evidence remain separate | PENDING | |
| AI Claim Reconciliation | Python arithmetic against supporting components and rounding | PENDING | |
| AI Security Conflict Detection | Form unsecured assertion versus hypothecation/security evidence | PENDING | |
| AI Amount Reconciliation | Claim total versus statement, award and supporting computation | PENDING | |
| AI Document Inventory | Found/not found pages and important references | PENDING | |
| AI Missing-Document Suggestions | Add to query, ignore, or mark not required | PENDING | |
| AI Query Suggestions | Explicitly add a draft; confirm that no email is sent | PENDING | |
| AI Claim Review | Nine sections, source/page/evidence and mandatory user confirmation | PENDING | |
| AI Claim Re-analysis | Reuse cache, retain history and surface changed/new evidence | PENDING | |
| CIRP Workflow Initialization | Initialize one versioned workflow per case without duplicate case steps | PENDING | |
| CIRP Event Engine | Record, review and confirm case events; verify repeated events do not duplicate work | PENDING | |
| CIRP Deadline Engine | Confirm T, inspect calculated dates and verify reviewed overrides remain unchanged | PENDING | |
| CIRP Workflow Steps 1–23 | Review admission, takeover and Public Announcement step content and statuses | PENDING | |
| Workflow Task Linkage | Verify active workflow steps reuse/create ordinary assigned tasks correctly | PENDING | |
| Workflow Evidence Controls | Attach proof, test mandatory-evidence blocking and authorised override reasons | PENDING | |
| Workflow Summary API | Verify counts, overdue indicators, approvals and next statutory deadline | PENDING | |
| Case Event Ledger | Verify chronological source, document, actor and confirmation traceability | PENDING | |
| Admission → Workflow Integration | Confirm an admission import initializes and activates the CIRP workflow | PENDING | |
| Public Announcement → Workflow Integration | Confirm Form A states advance CIRP-021–023 without duplicating Form A data | PENDING | |
| Claim Register Workflow | Confirm Public Announcement activates the canonical case Claims Register | PENDING | |
| Claim Acknowledgement | Confirm stable per-case Claim ID and acknowledgement status/communication record | PENDING | |
| Claim Classification | Confirm creditor category and Form B/C/CA/D/E/F/Other review | PENDING | |
| Claim Scrutiny | Confirm configurable checklist and NOT_STARTED/IN_REVIEW/COMPLETE/DEFICIENCY_FOUND states | PENDING | |
| Claim Deficiency/Query | Confirm deficiency activation, linked query, response and closure without automatic email | PENDING | |
| Claim Deadline/Late Claim | Confirm configured deadline, late flag/days and no automatic rejection | PENDING | |
| Claim Verification | Confirm claimed/admitted separation, exact totals and mandatory human conclusions | PENDING | |
| Related Party Review | Confirm UNKNOWN blocks eligibility and reason/evidence history is retained | PENDING | |
| Claim Decision | Confirm admitted, partly admitted and not admitted decisions preserve history | PENDING | |
| Claim Decision Communication | Confirm draft/prepared/approved/sent states and service-proof requirement | PENDING | |
| List of Creditors | Confirm current dynamic view derives all rows and totals from Claims | PENDING | |
| List of Creditors Versioning | Confirm formal versions remain unchanged after later Claim changes | PENDING | |
| List of Creditors Filing Record | Confirm mechanism/date/evidence records without external filing | PENDING | |
| Claim Revision | Confirm original submission remains and downstream CoC review is raised | PENDING | |
| Security Review | Confirm claimed/verified security remains human-reviewed with evidence | PENDING | |
| CoC Eligibility | Confirm only admitted Financial Creditor Claims become candidates | PENDING | |
| CoC Voting Share | Confirm exact debt calculations, deterministic rounding and 100% display total | PENDING | |
| Creditors in Class | Confirm class membership matrix remains linked to Claims | PENDING | |
| AR Workflow Shell | Confirm requirement, candidate/selection, filing need and evidence fields | PENDING | |
| CoC Constitution | Confirm validation and immutable Version 1 member snapshot | PENDING | |
| CoC Constitution Report | Confirm retained-template DRAFT/FINAL DOCX versions and professional approval | PENDING | |
| CoC Reconstitution | Confirm Claim changes preserve Version 1 and produce Version 2 only after RP confirmation | PENDING | |
| First CoC Meeting Creation | Confirm confirmed Constitution linkage, deterministic number and no duplicate number in one case | PENDING | |
| First CoC Deadline | Confirm DeadlineEngine date or REVIEW_REQUIRED state; test reviewed override trail | PENDING | |
| Meeting Scheduling | Confirm scheduled versus actual times, mode, venue/link and status changes | PENDING | |
| First CoC Notice Generation | Confirm retained first-Notice template and deterministic case/meeting values | PENDING | |
| Notice Versioning | Confirm DRAFT V1/V2 and issued Notice immutability | PENDING | |
| Notice Issue | Confirm professional approval/issue control and agenda/member snapshot freeze | PENDING | |
| Notice Dispatch Record | Confirm manual method, recipient, status, date and service proof record without Gmail | PENDING | |
| CoC Member Snapshot | Confirm issued recipient, debt and voting share remain historical after reconstitution | PENDING | |
| Agenda Versioning | Confirm add/edit/reorder in draft and controlled new version after freeze | PENDING | |
| Agenda Freeze | Confirm issued Notice remains tied to its exact Agenda version | PENDING | |
| Supporting Papers | Confirm Agenda items link only documents from the same case | PENDING | |
| Attendance / Roll Call | Confirm member, representative and invitee attendance fields | PENDING | |
| Representative Authorization | Confirm VALID/REVIEW_REQUIRED/NOT_APPLICABLE and evidence linking | PENDING | |
| Quorum Calculation | Confirm frozen voting shares and a configured versioned quorum rule are used | PENDING | |
| No-Quorum / Adjournment | Confirm attendance/history stays preserved and ordinary completion is blocked | PENDING | |
| RP Appointment Resolution | Confirm continuation/replacement wording is professional-controlled | PENDING | |
| CIRP Cost Statement | Confirm exact paise totals, rows and supporting papers | PENDING | |
| Operations Update | Confirm structured meeting support without starting the operations engine | PENDING | |
| Professional Appointment / Fee Agenda | Confirm scope, fee, tax, support and linked resolution | PENDING | |
| Resolution Management | Confirm resolution lifecycle and source Agenda linkage | PENDING | |
| Final Resolution Wording | Confirm proposed and final wording remain separately preserved | PENDING | |
| Minutes Text Entry | Confirm staff-entered factual text is retained verbatim with no automated rewriting | PENDING | |
| Agenda-wise Minutes Entry | Confirm issued Agenda order drives all Minutes entry fields | PENDING | |
| Minutes DOCX Generation | Confirm retained first/subsequent Minutes template output | PENDING | |
| Minutes Versioning | Confirm draft/final versions and historical snapshot preservation | PENDING | |
| Minutes Finalization | Confirm missing substantive text/disposition blocks finalization | PENDING | |
| Minutes Circulation Record | Confirm manual circulation status, recipients and proof without Gmail | PENDING | |
| E-voting Session | Confirm DRAFT/OPEN/CLOSED lifecycle and frozen eligible-voter snapshot | PENDING | |
| Vote Recording | Confirm actual member/source/time and frozen share are captured | PENDING | |
| Vote Correction | Confirm closed vote correction requires reason and retains audit history | PENDING | |
| Voting Result | Confirm exact FOR/AGAINST/ABSTAIN/NOT_VOTED result and rule review block | PENDING | |
| Action Items | Confirm decisions link to existing Tasks and completion evidence | PENDING | |
| Action Taken Report | Confirm ATR reflects linked Task state rather than copied status | PENDING | |
| Membership Change After Notice | Confirm post-notice reconstitution raises professional review without changing Notice snapshot | PENDING | |
