# Cloud office deployment (Render)

## Purpose and boundary

This configuration deploys the existing Casefile application as **one Render web service**. React and FastAPI share one address, so the browser calls `/api` on the same origin and does not need a cross-origin login exception.

It is suitable for controlled office UAT and small-office use while one paid Render instance is used. It is not yet the final long-term records architecture: SQLite remains a single-file database and the next production-hardening stage should migrate case data to PostgreSQL and uploaded files to managed object storage.

## What persists

The `render.yaml` service mounts Render persistent disk storage at `/var/data`:

- `/var/data/casefile.db` — the SQLite database
- `/var/data/files/` — case uploads and generated operational files
- `/var/data/custom_templates/` — custom office DOCX templates
- `/var/data/backups/` — encrypted database backups downloaded from Settings

Never choose Render's free web service for this deployment. Its filesystem is temporary, so case data and uploads would be lost after a restart or deployment. The persistent disk also means this service must remain a single instance; do not enable scaling.

## Publish procedure

1. Push the approved commits to the existing GitHub repository.
2. In Render, choose **New → Blueprint**, select `architkhandelwal2005/nclt_document_making`, and accept `render.yaml`.
3. Enter only these Blueprint values in Render's secret form:
   - `ADMIN_EMAIL`
   - `ADMIN_PASSWORD` — at least 12 unique characters
   - `ADMIN_NAME`
4. Keep Render-generated `JWT_SECRET` and `CASEFILE_BACKUP_KEY` private. They must not be copied into GitHub or the browser.
5. Create the service on a paid plan with the declared disk. Wait for the build to complete, then open `https://<render-service>.onrender.com/api/health`. It should return `status: ok` and `environment: PRODUCTION`.
6. Open `https://<render-service>.onrender.com`, sign in as the configured administrator, create separate office accounts, and run the UAT checklist.

## Backup and restore

Use **Settings → Back up database now** before testing sessions and download the encrypted file to an access-controlled office location. That browser action is database-only; it does not include case uploads. Preserve uploaded-file copies separately from the persistent disk.

Cloud database backups use Fernet encryption, an authenticated symmetric-encryption format, with a key derived from the Render `CASEFILE_BACKUP_KEY` secret. A backup can be restored only by a deployment configured with the same secret. Do not regenerate that secret after data has been entered. Windows-local DPAPI backups and cloud backups are deliberately incompatible.

Before the office enters real records, test a complete restore into a separate non-production Render service. Confirm both a restored case record and a downloaded document open correctly.

## Operational restrictions

- Do not run more than one web-service instance against this SQLite disk.
- Do not edit, upload, or download `casefile.db` directly from the Render shell.
- Do not put credentials, uploaded documents, database files, or backups in GitHub.
- Treat AI extraction as disabled unless a separate approved key and cost decision is made.
- Keep the service URL access-controlled by application accounts. A future hardening phase can add organisation SSO, audit retention policy, managed database, and object-storage backups.
