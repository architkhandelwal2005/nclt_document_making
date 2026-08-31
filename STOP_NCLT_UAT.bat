@echo off
setlocal
set "ROOT=%~dp0"
set "PROJECT_ROOT=%ROOT:~0,-1%"
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%ROOT%tools\stop_nclt_uat.ps1" -ProjectRoot "%PROJECT_ROOT%"
if errorlevel 1 pause
endlocal
