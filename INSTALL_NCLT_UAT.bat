@echo off
setlocal
title NCLT CIRP SOFTWARE - SERVER INSTALLATION
set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if exist "%CASEFILE_PYTHON%" (
  "%CASEFILE_PYTHON%" -c "import fastapi, uvicorn, bcrypt, jwt, docx, lxml, pypdf, reportlab" >nul 2>&1
  if not errorlevel 1 (
    echo NCLT UAT server runtime is already installed and valid.
    pause
    exit /b 0
  )
)

where py.exe >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python 3 is not installed on this server computer.
  echo Install the current 64-bit Python 3 release from python.org, then run this file again.
  echo Select the installer option to add Python to PATH.
  pause
  exit /b 1
)

if exist "%ROOT%backend\venv" (
  echo ERROR: An incomplete backend\venv folder already exists.
  echo Ask the installer to rename that folder before retrying. It was not deleted automatically.
  pause
  exit /b 1
)

echo Creating the private server runtime...
py.exe -3 -m venv "%ROOT%backend\venv"
if errorlevel 1 goto :failed

echo Installing required server components. Internet access is needed for this one-time step...
"%CASEFILE_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 goto :failed
"%CASEFILE_PYTHON%" -m pip install -r "%ROOT%backend\requirements.txt"
if errorlevel 1 goto :failed

"%CASEFILE_PYTHON%" -c "import fastapi, uvicorn, bcrypt, jwt, docx, lxml, pypdf, reportlab"
if errorlevel 1 goto :failed

echo.
echo NCLT UAT server runtime installed successfully.
echo Next: double-click SETUP_NCLT_UAT.bat.
pause
exit /b 0

:failed
echo.
echo ERROR: Server installation did not complete.
echo Check the internet connection and ask the internal installer for assistance.
pause
exit /b 1
