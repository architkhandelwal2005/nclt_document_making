# Office UAT Installation Checklist

Server computer: ____________________  Installer: ____________________  Date: __________

- [ ] Current 64-bit Python 3 installed on the server computer
- [ ] `INSTALL_NCLT_UAT.bat` completed and backend dependencies validated
- [ ] Application folder copied to a fixed server-computer location
- [ ] Compiled `frontend\build\index.html` available
- [ ] `SETUP_NCLT_UAT.bat` completed and private `.env.uat` created
- [ ] Separate UAT database initialized at `data\nclt_uat.db`
- [ ] Shared upload directory initialized at `data\uat_documents\`
- [ ] First Administrator login created and tested
- [ ] Insolvency Professional, Manager, Associate, and Viewer accounts created
- [ ] Windows network profile is Private
- [ ] Windows Firewall limited to TCP 8001, Private profile, LocalSubnet
- [ ] `START_NCLT_UAT.bat` works
- [ ] Server URL opens locally
- [ ] Server URL opens from a second office PC
- [ ] Login works from the second PC
- [ ] Optional synthetic case opens
- [ ] Case assignments configured
- [ ] Viewer can read but cannot create, edit, upload, or delete
- [ ] Restricted document download denied to an unauthorized account
- [ ] PRA/Resolution Plan workspace denied to Associate and Viewer
- [ ] Same harmless update visible to two authorized users
- [ ] `BACKUP_BEFORE_TESTING.bat` creates a complete encrypted backup
- [ ] `STOP_NCLT_UAT.bat` stops only the UAT server
- [ ] Internal support contact completed in `OFFICE_UAT_SETUP.md`
- [ ] Staff reminded: UAT test data only, not production
