@echo off
setlocal
set "ROOT=%~dp0"
set "PROJECT_ROOT=%ROOT:~0,-1%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\build_office_uat_package.ps1" -Destination "%PROJECT_ROOT%\office_uat_package\NCLT_CIRP_UAT"
if errorlevel 1 pause
endlocal
