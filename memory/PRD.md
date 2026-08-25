# Casefile — Insolvency Practice Management System

## Product objective

Provide an Insolvency Professional and NCLT practice with one local operating system for company-specific work, statutory obligations, hearings, claims, CoC administration, documents, communications, assets, valuation, and costs.

Document generation is a supporting case function, not the product's top-level purpose.

## Information scopes

1. Firm-wide: dashboard, consolidated tasks, calendar, users, and reporting.
2. Case-specific: all operational work, always protected by `case_id` at the API and database layers.
3. Shared masters: contacts, compliance rules, professionals, and document templates.

## Core domain chain

Case → People → Claims → Tasks → Deadlines → Hearings → Applications → Orders → CoC → Documents → Communications → Expenses → Assets → Valuation → Compliance

## Functional modules

- Case Master
- Stakeholders and contacts
- Tasks and workflow automation
- Effective-dated compliance rules and calculated deadlines
- Hearings, applications, orders, and directions
- Claims, deficiencies, document checks, and admission decisions
- CoC membership, meetings, and historical voting shares
- Case document management and context-aware generation
- Communications register
- Assets and financial information
- Valuation
- Expenses and CoC contributions
- Activity, audit, reports, firm dashboard, and calendar
- Local users, roles, encrypted backup, and restore

## Non-functional requirements

- Local-first operation without MongoDB or a cloud dependency
- SQLite foreign keys, transactions, migrations, and soft archival
- Backend-enforced case isolation
- Bcrypt password hashing, expiring signed sessions, login throttling, and role-based access
- Before/after audit entries for material mutations
- Safe file names, file type restrictions, 25 MB case-file limit, and case-bound downloads
- Windows-user-encrypted backups and verified restoration
- Accessible visible labels, keyboard focus, stable test identifiers, and responsive layouts

## Workflow automations

- New case: initial setup task and applicable compliance deadlines
- New hearing: hearing-preparation task
- New claim: verification task
- Deficient claim: deficiency-communication task
- New CoC meeting: notice/agenda and minutes tasks
- New order direction: linked compliance task

## Regulatory boundary

Rules are stored with process type, trigger, offset, calculation method, version, and effective dates. Seeded rules are model data and must be professionally verified before operational reliance.

## Deferred beyond Phase 15

- Cloud deployment and external database hosting
- Live email, WhatsApp, portal, and calendar provider synchronization
- AI extraction, drafting, summaries, and deadline-risk suggestions
- Production malware-scanning service integration
