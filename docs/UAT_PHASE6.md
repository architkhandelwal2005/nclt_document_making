# Phase 6 User Acceptance Testing - EOI, PRA and Resolution Plan Process

Scope: CIRP-085 through CIRP-105 only. Automated tests verify software controls; they do not constitute UAT (User Acceptance Testing, meaning confirmation by office users that the workflow matches actual practice).

Reference status:

- Keshav EOI PDF, including Annexure A, Annexure B and Annexure C: inspected and used as an operational specification.
- Existing Information Memorandum, confidentiality, VDR, CoC, voting, application, hearing, document, event and deadline infrastructure: reused.
- Editable EOI, RFRP, Evaluation Matrix, Resolution Plan, professional Section 30 checklist, Performance Security, Plan Approval Application and compliance-certificate specimens: `TEMPLATE_REQUIRED` where no genuine editable office specimen was located.

| Area | Office test | Expected control | User Tested |
|---|---|---|---|
| EOI Process | Create a case-specific draft with dates, modes, deposit and consortium settings | Values remain case/version-specific; no Keshav values appear unless entered | PENDING |
| Eligibility Criteria | Enter numeric and descriptive CoC-approved criteria | Software stores entered criteria and does not invent thresholds | PENDING |
| EOI Publication | Approve and publish with final EOI and proof | Published version becomes immutable | PENDING |
| EOI Receipt | Register email/physical receipt twice with the same idempotency key | One logical submission and one receipt event | PENDING |
| PRA Master | Create company, LLP, fund, individual and consortium records | Canonical PRA identity is reused throughout | PENDING |
| Consortium | Add lead/member percentages, evidence and separate reviews | Lead result does not automatically qualify other members | PENDING |
| EOI Checklist | Record received, deficient, pending and not-applicable documents | Summary exactly matches the entered item statuses | PENDING |
| Deposit/BG | Record DD, bank guarantee or transfer and supporting document | No automatic forfeiture; professional decision required | PENDING |
| Section 29A | Record YES/NO/UNKNOWN evidence and legal-review flags | UNKNOWN blocks final ELIGIBLE conclusion until resolved | PENDING |
| Connected Persons | Record relationship, beneficial ownership, source and verification | No AI inference or automatic eligibility conclusion | PENDING |
| Provisional List | Generate, approve and issue from reviewed PRA results | Issued snapshot and dispatch evidence remain immutable | PENDING |
| Objections | Record objection, evidence and reasoned decision | Provisional snapshot remains unchanged | PENDING |
| Final List | Generate a fresh list after objections/reviews | Only final-eligible PRAs can proceed to issue package | PENDING |
| Confidentiality | Create PRA/member undertaking and verify signed evidence | Existing Phase 4 confidentiality register is used | PENDING |
| RFRP | Upload/version and approve the office document | No fabricated legal RFRP; approved version is immutable | PENDING |
| Evaluation Matrix | Enter approved criteria, formulas, maximum scores and manual fields | Approved criteria are locked | PENDING |
| Issue Package | Select exact RFRP, Matrix and IM versions | Package is blocked without final eligibility and verified undertaking | PENDING |
| VDR Access | Issue a package after undertaking verification | Existing VDR grant/log is created with exact scope | PENDING |
| PRA Queries | Record private/shared query and reviewed response | Private response is not exposed to other PRAs | PENDING |
| Site Visit | Record date, location, attendees, scope and approval | Minimal visit record is retained | PENDING |
| Addendum | Issue a process addendum to relevant PRAs | Earlier document version remains unchanged; dispatch log is retained | PENDING |
| Resolution Plan Receipt | Receive Plan V1 and revised V2 | Both timestamps/documents remain; V1 becomes superseded, not overwritten | PENDING |
| Plan Versioning | Link review, evaluation and vote to a selected version | Exact version is visible in every downstream record | PENDING |
| Section 30(2) | Record clause/page references and an unresolved item | COMPLIANT conclusion is blocked until professional resolution | PENDING |
| Final Section 29A | Add new connected-person information after initial eligibility | New review version is required; initial review remains unchanged | PENDING |
| Plan Queries | Raise and answer RFRP/Section 30/Section 29A/security query | Query and response chronology is retained | PENDING |
| Evaluation | Score 60-point deterministic and 40-point manual criteria | Decimal arithmetic is exact; manual score needs scorer and reason | PENDING |
| Negotiation | Configure and close a revised-plan round | Round, timestamps and submissions remain versioned | PENDING |
| CoC Agenda Link | Place exact compliant Plan version in an existing agenda | Existing CoC meeting/agenda engine is used | PENDING |
| Plan Voting | Conduct vote using frozen member shares | Existing voting result is linked to exact Plan version | PENDING |
| Successful RA | Select the approved Plan/PRA | PRA identity remains unchanged; separate selection record is created | PENDING |
| Performance Security | Record required BG and verify it | SRA stays pending until verified; no automatic invocation/forfeiture | PENDING |
| Plan Approval Application | Assemble selected Plan, reviews, vote, security and application | Existing application record is used; pleading template stays required | PENDING |
| Hearing | Schedule the Plan Approval hearing and record later directions/order | Existing hearing/order infrastructure is used | PENDING |
| Restricted Documents | Attempt PRA/Plan/evaluation access as Viewer | Backend returns access denied; professional succeeds | PENDING |
| Case Isolation | Submit a PRA/Plan/evaluation ID from another case | Backend rejects the cross-case identifier | PENDING |

Sign-off:

- Tested by: ____________________
- Office role: ____________________
- Test date: ____________________
- Case used: ____________________
- Overall result: PENDING
- Defects/change requests: ________________________________________________
