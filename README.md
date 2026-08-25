# Casefile — Local Insolvency Practice Management

Casefile is a local business-management application for Insolvency Professionals and NCLT practitioners. The firm dashboard is the entry point; every operational record belongs to a selected company/case workspace.

## Implemented product structure

Firm-wide views:

- Dashboard with portfolio, overdue work, upcoming tasks, and hearings
- Companies and case selection
- Consolidated task queue
- Consolidated calendar
- Shared contacts, versioned compliance rules, and document template masters
- NCLT case-history lookup and new-order download utility
- Professional profile
- Users, roles, encrypted backups, and restore controls

Case workspace modules:

- Structured Case Master and case-level reporting
- Two-stage Public Announcement workflow with editable Form A, published-copy review, provenance, and confirmation gating
- Contacts and stakeholder roles
- Tasks and workflow-generated actions
- Compliance deadlines generated from effective-dated rules
- Hearings, applications, orders, and directions
- Claims and claim-document checklists
- CoC membership, meetings, and historical voting records
- Document upload, register, and case-context document generation
- Communications
- Assets and financial records
- Valuation
- Expenses and CoC contributions
- Chronological activity and administrator audit history

## Local architecture

- Frontend: React 18 at `http://127.0.0.1:3000`
- Backend: FastAPI at `http://127.0.0.1:8001`
- Database: SQLite at `backend/data/casefile.db`
- Case files: `backend/data/files/<case-id>/`
- Authentication: bcrypt password hashing and signed, expiring JWT sessions
- Authorization: administrator/insolvency-professional firm access plus backend-enforced case assignments for managers, associates, and viewers
- Backups: SQLite online backup encrypted with Windows DPAPI for the current Windows account
- Existing DOCX engine retained inside each case's Documents module

MongoDB and cloud deployment are intentionally outside the current local build.

## Start the application

Double-click `start_local.bat`. It serves the latest compiled frontend, starts both local servers, and opens:

`http://127.0.0.1:3000`

Close the two minimized server windows to stop the application.

## Configuration

Copy `backend/.env.example` to `backend/.env` and configure:

- `JWT_SECRET`: random value containing at least 32 characters
- `ADMIN_EMAIL`: environment administrator email
- `ADMIN_PASSWORD`: strong local password
- `ADMIN_NAME`: administrator display name
- `STORAGE_MODE=local`

The environment administrator is restored at startup and cannot be disabled. Additional office users and their case assignments are administered in Settings.

## NCLT Order Fetcher

The fetcher opens the public NCLT case-number search in a visible Chromium
window. It never bypasses CAPTCHA (the portal's human-verification challenge):

1. Before first use on a computer, close Casefile and double-click
   `setup_nclt_fetcher.bat`. This installs the pinned Playwright package and its
   Chromium browser into the existing Python environment.
2. Start Casefile normally with `start_local.bat` and sign in.
3. Open **NCLT Orders** from the main navigation, or open a company and select
   its **NCLT Orders** tab for case-prefilled operation.
4. Enter or review the case number and year, then press **Fetch NCLT case**.
5. Complete the CAPTCHA in the opened Chromium window. Return to Casefile and
   press **Continue after manual verification**.
6. Review the validated case and every proceeding row. If the portal returns
   multiple exact candidates, select the correct one; the software does not
   guess.
7. Press **Download all new orders**. Valid PDF files are recorded only after
   content and size checks; previously seen sources or file hashes are skipped.

Standalone downloads are stored under `backend/data/nclt_orders/`. A fetch
started inside an authorized company workspace stores validated files under
`backend/data/files/<case-id>/nclt-orders/` and adds them to that company's
Documents register with category **NCLT Order**. Automation failures save a
screenshot, page address, stage, error, and page HTML under
`backend/data/debug/nclt_fetcher/` for maintenance.

Visible-browser mode is the supported first-version default. It can be changed
with `NCLT_FETCHER_HEADLESS`, but headless mode should not be used when the
public portal requires manual verification.

## Roles and case access

- `admin`: all cases, user administration, case assignments, backup and restore.
- `professional`: all cases; intended for the Insolvency Professional.
- `manager`: assigned cases; may create a case and is automatically assigned to it.
- `associate`: assigned cases with operational write access.
- `viewer`: assigned cases with read-only access.

Legacy `staff`, `reviewer`, and `read-only` user records remain supported. Case permissions are enforced by the backend for case lists, direct URLs, modules, files, admission-order imports, firm tasks, dashboard results, and calendar results. Frontend filtering is not treated as a security control.

Administrators should assign at least one manager or associate to every active case through **Settings → Case assignments**.

## Existing-data migration

On the first SQLite startup, the application imports compatible profiles, matters, and generated-document records from `backend/data/casefile.json`. The JSON source is never modified or deleted and the migration is marked so it cannot run twice.

## CoC meeting workflow

CoC records are company-specific. Open a company, select **CoC**, and proceed in
this order:

1. Add historically effective CoC members and voting shares.
2. Add the meeting schedule, mode, venue/link, voting window, quorum threshold,
   actual start/end time, and chair.
3. Select that meeting in **Meeting control** and add agenda items in order.
4. Generate a notice for review, then use **Issue and freeze notice**. Issuing
   stores an immutable agenda/member snapshot for the notice-to-minutes chain.
5. Record actual attendance. Quorum is displayed as a calculation from the
   attendance voting-share snapshots; the professional remains responsible for
   the legal conclusion.
6. Enter the actual discussion for every agenda item. The software refuses to
   generate minutes if any discussion is blank and never invents that content.
7. Record member-wise votes in the voting register, generate minutes for review,
   then store the final minutes.
8. Retrieve every review/final version from the company’s **Documents** tab.

The four retained source packages are stored under `backend/templates/coc` as
exact copies of the authoritative files in `reference documents`. Generation
uses document-specific OOXML mutation so native page size, styles, numbering,
headers, footers, page borders, fields, tables, and package relationships remain
available. Do not edit or replace an authoritative source without repeating the
structural and rendered-page audit.

## Public Announcement workflow

Open a company and select **Public Announcement**. The generated Form A and the
newspaper's published copy are separate, permanently retained records:

1. Confirm the NCLT admission order.
2. Review the pre-populated Form A fields. Values identify whether they came
   from the admission order, company master, professional master, user review,
   or a deterministic date calculation.
3. Generate the editable two-page legal-size Form A DOCX. Each generation is a
   new version in the company's **Documents** register.
4. Mark Form A **Ready for publication**, then **Sent for publication**.
5. Upload the PDF returned by the newspaper. The original PDF is stored as a
   separate published-document record and never overwrites Form A.
6. Review and correct every extracted field. Searchable PDFs use local PDF text
   extraction. Scans use local Tesseract OCR when installed; otherwise the UI
   explicitly requires manual entry. OCR means optical character recognition,
   which converts text visible in an image into editable text.
7. Resolve every displayed existing-versus-published conflict and save the
   mandatory review.
8. An administrator, professional, or manager confirms publication. Only this
   confirmation changes the canonical publication date, claims deadline,
   supporting deadline evidence, case stage, and downstream claims task.

No external AI or paid extraction service is called. The generator currently
produces DOCX only because the application does not yet have a reliable free
local DOCX-to-PDF conversion engine for end users. Microsoft Word can be used
by the office to save the reviewed DOCX as PDF when required.

## Verification

Backend:

```powershell
cd backend
$env:TEMP="E:\nclt document\.pytest_tmp"
$env:TMP=$env:TEMP
.\venv\Scripts\python.exe -m pytest tests -q -n 0 --basetemp ..\.pytest_tmp\manual
```

Frontend:

```powershell
cd frontend
npm install --legacy-peer-deps
npm run build
```

`start_local.bat` serves the most recent successful build; rebuild after changing frontend source.

## Regulatory limitation

Seeded CIRP dates are a model starting set for application testing and workflow initialization. The compliance-rule master explicitly labels them as requiring legal review. Current regulations, case-specific orders, exclusions, extensions, and effective dates must be verified by the responsible professional before reliance.

## Backup limitation

DPAPI-encrypted backups can normally be decrypted only by the same Windows user account on the same Windows installation. Maintain additional firm-approved disaster-recovery arrangements before using Casefile as the sole repository for live professional records.

## Updating the local application

1. Close both Casefile server windows.
2. Preserve `backend/data`, `backend/.env`, and the templates directory.
3. Copy or obtain the updated source.
4. Run `backend\venv\Scripts\python.exe -m pip install -r backend\requirements.txt`.
5. Run `npm install --legacy-peer-deps` and `npm run build` inside `frontend`.
6. Start with `start_local.bat`; additive SQLite schema initialization runs automatically.
7. Sign in as administrator and verify case assignments before other users resume work.

## Local office deployment status

The current launcher binds to `127.0.0.1`, so it remains a single-PC test deployment. Authentication, case permissions, server-side files, and the application data model are foundations for multiple users, but this build is not yet the final office-network deployment.

Before office-network use, complete the PostgreSQL migration, a unified internal hostname/reverse proxy, HTTPS or an equivalently protected trusted-network setup, document-directory backups, and a restore rehearsal. Do not expose the SQLite file or its directory as a Windows network share.

## Disaster recovery

### Database backup

An administrator can download an online SQLite backup from **Settings**. It is encrypted with Windows DPAPI, the Windows Data Protection API, and normally decrypts only for the same Windows user on the same Windows installation.

### Document backup

Uploaded files are stored under `backend/data/files/<case-id>/`. They are not included in the current database-only download. Until automated document backup is implemented, copy the complete `backend/data/files` directory nightly to a second firm-controlled disk while preserving its directory structure.

### Restore procedure

1. Stop Casefile and copy the complete `backend/data` directory to a safe location.
2. Start Casefile under the Windows account that created the encrypted backup.
3. Use **Settings → Restore encrypted backup**. SQLite integrity is validated and a safety database backup is made before replacement.
4. Restore the matching `files` directory separately if documents were lost.
5. Restart Casefile and verify users, case assignments, representative cases, tasks, and several downloaded files.

Rehearse this procedure on a non-production copy before Casefile becomes the office's sole record system.

## Troubleshooting

- **Admission upload says Not Found:** close both old server windows, restart with `start_local.bat`, and press `Ctrl+F5`.
- **User sees no cases:** assign that user under Settings. No assignment is the secure default.
- **Viewer cannot edit:** expected; Viewer is read-only.
- **Frontend change is missing:** run `npm run build`, restart, and hard-refresh the browser.
- **Scanned publication shows manual review required:** install the free local Tesseract OCR executable under `C:\Program Files\Tesseract-OCR`, restart Casefile, then upload the published copy again. Searchable PDFs do not require Tesseract.
- **Backup will not restore on another PC/account:** expected for the current DPAPI backup; use the original Windows account/installation or a separate disaster-recovery copy.

## External-service and cost status

The default application uses free/open-source libraries and requires no AI service, paid OCR, Google API, MCA data provider, or other paid SaaS subscription. Google Calendar and Gmail are not implemented. The manual MCA provider has no external dependency. Optional future hosting, Google Workspace, off-site storage, or cloud infrastructure may incur costs only if the firm chooses them later.
