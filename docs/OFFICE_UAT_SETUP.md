# NCLT CIRP Software — Office UAT Setup and Use

## 1. What is this?

This is the office test version of the NCLT CIRP case-management software. UAT means **user acceptance testing**: staff use test information to confirm that the software matches office practice before production use.

The orange banner must say `UAT ENVIRONMENT — TEST DATA ONLY`.

Do not treat this installation as production. Do not enter live client data unless the Insolvency Professional has expressly approved it.

## 2. Which computer is the server?

Choose one Windows computer in the office as the **server computer**. The server computer stores the single UAT database and all shared UAT uploads. Other office computers only use Chrome.

The server computer must remain switched on and connected to the trusted office LAN/Wi-Fi, contain the complete application folder and installed Python runtime, and never place the SQLite database on a shared network drive.

LAN means **local area network**: the private office network connecting the server and staff computers.

**SERVER COMPUTER MUST REMAIN ON WHILE THE OFFICE IS USING THE SOFTWARE.**

## 3. How to start the software

First-time setup on the server computer:

1. Copy the complete application folder to a fixed location. Do not run it from a removable drive during multi-user testing.
2. Install current 64-bit Python 3 from python.org on the server only, selecting the installer option to add Python to PATH.
3. Double-click `INSTALL_NCLT_UAT.bat`. Internet access is needed for this one-time server installation.
4. Complete `docs/OFFICE_UAT_INSTALL_CHECKLIST.md`.
5. Double-click `SETUP_NCLT_UAT.bat`.
6. Enter the first Administrator's name, email, and a password of at least 12 characters. Password characters are not displayed.
7. Keep `.env.uat` private. It contains UAT secrets and must never be emailed, committed to Git, or copied to staff PCs.
8. Double-click `START_NCLT_UAT.bat`.
9. Keep the server window open.

Normal daily start:

1. Double-click `START_NCLT_UAT.bat` on the server computer.
2. Wait for `NCLT CIRP SOFTWARE — UAT SERVER RUNNING`.
3. Note the displayed Office URL.
4. Keep that window open while staff are testing.

No React development server, Node command, terminal, VS Code, Python command, or developer tool is required on a staff computer.

## 4. What URL should staff open?

The start window displays a URL similar to `http://192.168.1.25:8001`. Every staff computer on the same trusted office LAN/Wi-Fi opens that one URL in Chrome.

Do not use `127.0.0.1` on a staff computer; it refers to the staff computer itself. The server computer can verify the software locally at `http://127.0.0.1:8001`.

If automatic detection fails, run `ipconfig` on the server and use the active Wi-Fi or Ethernet `IPv4 Address` followed by `:8001`.

Later, ask the network administrator for a **DHCP reservation**, meaning the router always assigns the same private IP address to the server. Do not enable router port forwarding and do not expose port 8001 to the internet.

## 5. How staff log in

The Administrator creates separate accounts at `Settings → Office users → Add user` for the Insolvency Professional, Manager, Associate, and Viewer. Use a unique temporary password of at least 12 characters for each person. Do not share the Administrator login.

At `Settings → Case assignments`, assign each Manager, Associate, and Viewer only to the cases they should access.

Normal route: `LOGIN → CASES → SELECT COMPANY → CASE CONTROL AREA`.

Phase 6 route: `Open Casefile → Select Company → Resolution Process`.

## 6. What if the page does not open?

Check in this order:

1. The server computer is on and connected to the office network.
2. The `START_NCLT_UAT.bat` window remains open without an error.
3. `http://127.0.0.1:8001` opens on the server computer.
4. The staff computer is on the same office LAN/Wi-Fi, not guest Wi-Fi.
5. The Office URL exactly matches the current server display.
6. The Windows Firewall rule for private networks and TCP port 8001 is installed.
7. If the IP address changed, use the newly displayed URL.

TCP means **Transmission Control Protocol**, the network connection used by the browser and server on port 8001.

## 7. What if the server computer restarts?

After Windows restarts, sign in, double-click `START_NCLT_UAT.bat`, wait for the Office URL, and tell staff if the IP address changed. The UAT database and uploads remain in the application `data` folder.

## 8. How to stop the software

Double-click `STOP_NCLT_UAT.bat` on the server. It reads the recorded UAT process identifier, verifies the process is the NCLT UAT launcher, and stops only that process. `Ctrl+C` in the visible start window also stops the server.

## 9. How to back up UAT

Before office testing, double-click `BACKUP_BEFORE_TESTING.bat`. After each test session, double-click `BACKUP_AFTER_TESTING.bat`.

The complete encrypted backup is saved under `data\uat_backups\` and includes a transaction-consistent SQLite database snapshot and the shared `data\uat_documents\` directory. SQLite is the embedded database file; transaction-consistent means the snapshot is a complete valid database state even while the app is running.

The `Settings → Back up database now` browser action is **database-only and does not include uploaded documents**. It is not a complete system backup.

Complete UAT backups use Windows data protection and should be restored only by the same trusted Windows account on the server. The installer must test restoration before production use.

## 10. Important security notes

- Keep authentication enabled and use separate accounts.
- Viewer accounts are read-only.
- Case assignments restrict Managers, Associates, and Viewers.
- Restricted PRA, Resolution Plan, evaluation, transaction-audit, valuation, and legal documents remain protected by server authorization.
- Never expose SQLite on a network share. Only FastAPI—the application server—accesses SQLite; browsers communicate with FastAPI.
- Never send `.env.uat`, passwords, client files, or backups by ordinary email.
- Permit port 8001 only on the trusted Windows **Private** profile and only from `LocalSubnet`, meaning local office devices.
- Do not configure public access or router port forwarding.

Run this once as Administrator in PowerShell on the server:

`New-NetFirewallRule -DisplayName "NCLT CIRP UAT (TCP 8001)" -Direction Inbound -Protocol TCP -LocalPort 8001 -Action Allow -Profile Private -RemoteAddress LocalSubnet`

To remove it later: `Remove-NetFirewallRule -DisplayName "NCLT CIRP UAT (TCP 8001)"`.

## 11. This is UAT — not production

The orange UAT banner must remain visible. Use fake or specifically approved test data. `CREATE_SYNTHETIC_UAT_CASE.bat` optionally creates one clearly fake company and never runs automatically.

Do not use this checkpoint as a public service, internet deployment, or production records system.

## 12. Who to contact internally if something fails

Office internal contact: ______________________________

Phone / extension: ___________________________________

Record the date/time, user role (never the password), case name, module, action, exact error, and a suitably redacted screenshot.

## Second-computer acceptance test

1. Start UAT on the server.
2. Open the displayed Office URL on a second computer.
3. Log in with a non-Administrator test account.
4. Open the synthetic case.
5. Make one harmless update as an Associate or Manager.
6. Refresh that case on the server and confirm the same update appears.
7. Log in as Viewer and confirm the assigned case opens but a change is denied.

This proves both computers use the same server database.

## 13. Updating an existing UAT installation without replacing its data

For a frontend-only correction such as the same-origin login fix, do not replace the complete installed folder and do not run first-time setup again.

1. On the installed server, run `BACKUP_BEFORE_TESTING.bat` and confirm that it creates a backup.
2. Run `STOP_NCLT_UAT.bat`.
3. In `C:\NCLT_CIRP_UAT\frontend\`, rename the existing `build` folder to `build_before_same_origin_fix`.
4. Copy only the new package's `frontend\build` folder into `C:\NCLT_CIRP_UAT\frontend\`.
5. Do not delete, replace, rename, or copy over `C:\NCLT_CIRP_UAT\data`, `C:\NCLT_CIRP_UAT\.env.uat`, or `C:\NCLT_CIRP_UAT\backend\venv`.
6. Run `START_NCLT_UAT.bat`, open the displayed URL, and test login.
7. Keep `build_before_same_origin_fix` until the corrected login has been accepted. It can be restored if necessary without touching UAT data.
