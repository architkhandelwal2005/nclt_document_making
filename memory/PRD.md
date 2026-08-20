# Casefile — NCLT & Insolvency Document Drafting Portal

## Problem
Chartered accountant working NCLT / insolvency cases makes recurring documents with the same format. Fill fixed DOCX templates safely (preserve legal formatting/bold/italics), export as DOCX + PDF, allow tabular schedules (creditors, suspended mgmt, expenses), and be deployment-ready.

## User persona
- Solo chartered accountant + admin (single-user workspace, JWT login).

## Core requirements (static)
- Fill 5 fixed NCLT templates from a guided form.
- Preserve DOCX formatting via run-level replacement (no `paragraph.text = ...` flattening).
- Support tables: creditors list, suspended management, CIRP expenses.
- Export Word + PDF.
- Simple JWT login (admin seeded from env).
- Deploy-ready on Emergent.

## Architecture
- FastAPI backend (`/app/backend/server.py`): auth (login/me/logout, brute-force lockout), templates listing with column defs, document generation & export (`/download/{fmt}` Bearer + `/export/{fmt}?token=` for browser downloads).
- Motor + MongoDB collections: `users`, `login_attempts`, `generated_documents`.
- python-docx run-level replacement + reportlab for PDF.
- React frontend (`App.js`): AuthContext via localStorage token, axios interceptor, inline editable TableEditor with CSV import, live preview.

## Templates (5)
Voting Agenda · Constitution of CoC · Notice of 1st CoC · Notice of 2nd CoC · LOC Filing & CoC Report.

## Completed
- 2026-02 (v1): Auth (JWT + bcrypt + lockout), inline editable table editor with CSV import, /export?token= for browser downloads, admin seed, deployment health-check pass. Testing agent: 100% backend + 100% frontend.
- 2026-02 (v2): Template Uploader (drop a DOCX → detects `{{placeholders}}`, user labels fields/tables, saves to library). Save Case Drafts (persist partial matter, resume from Drafts view). Backend endpoints: `/api/templates/inspect`, `POST/DELETE /api/templates`, `/api/drafts` CRUD. Testing agent iteration 3: 14/14 backend + 100% frontend.
- 2026-02 (v3): **Removed MongoDB entirely.** Generated documents live in an in-memory TTL cache (30 min) and are streamed on download. Custom templates persisted as `.docx` + `.json` files on disk under `templates/custom/`. Drafts moved to browser `localStorage`. Admin login checks env-based credentials with bcrypt (no user table). Login attempts tracked in memory. Removes need for MongoDB Atlas — the app can now be hosted on just Render (backend) + Vercel (frontend) free tiers.

## Backlog
- P1: Persist "case profiles" so a user can reopen a partially filled case and its table data.
- P2: Template Manager UI to upload/map new DOCX templates without code changes.
- P2: Signed short-lived download URLs (avoid full JWT in query string).
- P3: Better PDF fidelity (currently plain text pass) or headless DOCX→PDF conversion.
