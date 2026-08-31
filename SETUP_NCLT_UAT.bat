@echo off
setlocal
set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if not exist "%CASEFILE_PYTHON%" (
  echo ERROR: The Casefile Python runtime is missing.
  echo Expected: %CASEFILE_PYTHON%
  pause
  exit /b 1
)

"%CASEFILE_PYTHON%" "%ROOT%backend\uat_tools.py" configure
if errorlevel 1 pause
endlocal
