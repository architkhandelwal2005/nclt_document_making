@echo off
setlocal
set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if not exist "%CASEFILE_PYTHON%" (
  echo ERROR: The Casefile Python runtime is missing.
  pause
  exit /b 1
)

"%CASEFILE_PYTHON%" "%ROOT%backend\uat_tools.py" synthetic-case
pause
endlocal
