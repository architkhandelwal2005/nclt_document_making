@echo off
setlocal

set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if not exist "%CASEFILE_PYTHON%" (
  echo ERROR: The Casefile Python environment is missing.
  echo Expected: %CASEFILE_PYTHON%
  pause
  exit /b 1
)

echo Installing the NCLT Order Fetcher dependency...
"%CASEFILE_PYTHON%" -m pip install -r "%ROOT%backend\requirements.txt"
if errorlevel 1 goto :failed

echo Installing the Chromium browser used by the visible NCLT workflow...
"%CASEFILE_PYTHON%" -m playwright install chromium
if errorlevel 1 goto :failed

echo.
echo NCLT Order Fetcher setup completed successfully.
pause
exit /b 0

:failed
echo.
echo ERROR: NCLT Order Fetcher setup did not complete.
pause
exit /b 1
