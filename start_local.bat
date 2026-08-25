@echo off
setlocal

set "ROOT=%~dp0"
set "CASEFILE_PYTHON=%ROOT%backend\venv\Scripts\python.exe"

if not exist "%CASEFILE_PYTHON%" (
  echo ERROR: The Casefile Python environment is missing.
  echo Expected: %CASEFILE_PYTHON%
  echo Reinstall the backend dependencies before starting Casefile.
  pause
  exit /b 1
)

echo ===================================================
echo   NCLT CASEFILE - STARTING LOCAL WORKSTATION
echo ===================================================

echo [1/2] Starting Python Backend on http://127.0.0.1:8001...
cd /d "%ROOT%backend"
start "NCLT Backend Server" cmd /k ""%CASEFILE_PYTHON%" -m uvicorn server:app --host 127.0.0.1 --port 8001"

echo [2/2] Starting Frontend Client on http://127.0.0.1:3000...
cd /d "%ROOT%frontend"
start "NCLT Frontend Client" cmd /k ""%CASEFILE_PYTHON%" -m http.server 3000 --directory build --bind 127.0.0.1"

echo.
echo ===================================================
echo   Servers running! 
echo   - Backend:  http://127.0.0.1:8001
echo   - Frontend: http://127.0.0.1:3000
echo   Opening http://127.0.0.1:3000 in your browser...
echo ===================================================

ping 127.0.0.1 -n 3 >nul
start http://127.0.0.1:3000

endlocal
