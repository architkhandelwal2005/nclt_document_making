# Casefile — Product Requirements

## Original problem statement
My dad is a chartered accountant, and he works in NCLT and insolvency cases, and he has to make multiple documents, but most documents have the same format. So I want to create for him something via which he can just put in the main terms, and then the document will be made automatically. I tried to make this a while back. I will also share with you the code I have and the kind of documents I want to make. And basically, we have to make a system in which he just has to enter, suppose, the company name, the office address, et cetera, and the document will au-automatically be made, something like that.

## Product decisions
- Audience: chartered accountants, insolvency professionals, and legal operations staff.
- Fixed-template filling only in v1; no AI-assisted rewriting.
- Guided sections with required fields plus optional notes.
- Downloadable Word and PDF outputs.
- Light, paper-like Casefile workspace designed for long drafting sessions.

## Architecture
- React JavaScript frontend with a persistent Casefile shell and three-step generator.
- FastAPI backend using existing MongoDB connection settings.
- Seeded template definitions in the backend until the user's real Word/PDF samples are uploaded.
- Generated document records stored in MongoDB with UUID identifiers and ObjectId excluded from responses.
- Server-side DOCX generation using python-docx and PDF generation using reportlab.

## Core requirements (static)
1. Select a repeatable NCLT or insolvency template.
2. Enter case particulars through clearly labelled, required form fields.
3. Add optional matter-specific notes.
4. See a live paper preview while entering details.
5. Generate and persist a completed document.
6. Download editable Word and print-ready PDF files.
7. Reopen a list of recent generated documents.

## Implemented
### 2026-08-19
- Replaced starter screen with Casefile workspace navigation, template library, recent documents, settings placeholder, and responsive styling.
- Added NCLT Company Particulars and Insolvency Notice starter templates with grouped fields.
- Added required-field validation, live preview, optional notes, MongoDB persistence, and recent-document status.
- Added working DOCX and PDF export endpoints and browser-tested the complete flow on desktop and mobile.

## Backlog
### P0 — next required product work
- Upload the family's real Word/PDF samples and map their placeholders to template fields.
- Add a template administration screen for creating, editing, and retiring fixed templates.

### P1 — workflow improvements
- Add firm profile details and reusable signatory/address defaults.
- Add document reopen/edit flow from Recent documents.
- Add better PDF pagination and preserve original Word template styles when sample templates are supplied.

### P2 — later enhancements
- Add document naming conventions and matter folders.
- Add print-friendly preview controls and a template version history.

## Next tasks
1. Collect the first real sample Word/PDF documents.
2. Identify each replaceable field and its required/optional status.
3. Convert the first sample into a production template.
4. Validate the generated file against the original office format.