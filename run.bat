@echo off
setlocal
cd /d "%~dp0"
set "PYTHONPATH=%~dp0src"

if exist "%~dp0.venv\Scripts\pythonw.exe" (
  start "" "%~dp0.venv\Scripts\pythonw.exe" -m bogus_security_answers
  exit /b 0
)

where pyw >nul 2>&1
if %errorlevel%==0 (
  start "" pyw -3 -m bogus_security_answers
  exit /b 0
)

py -3 -m bogus_security_answers
