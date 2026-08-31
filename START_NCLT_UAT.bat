@echo off
setlocal
title NCLT CIRP SOFTWARE - UAT SERVER
set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if not exist "%CASEFILE_PYTHON%" (
  echo ERROR: The Casefile Python runtime is missing.
  echo Expected: %CASEFILE_PYTHON%
  echo Complete the server installation checklist before trying again.
  pause
  exit /b 1
)

if not exist "%ROOT%frontend\build\index.html" (
  echo ERROR: The compiled website is missing.
  echo Ask the installer to run the frontend production build.
  pause
  exit /b 1
)

if not exist "%ROOT%.env.uat" (
  echo First-time UAT configuration is required.
  "%CASEFILE_PYTHON%" "%ROOT%backend\uat_tools.py" configure
  if errorlevel 1 (
    pause
    exit /b 1
  )
)

"%CASEFILE_PYTHON%" -u "%ROOT%backend\uat_launcher.py"
if errorlevel 1 pause
endlocal
