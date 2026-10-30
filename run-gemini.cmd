@echo off
setlocal
cd /d "%~dp0"
if exist "%~dp0.venv\Scripts\python.exe" (
  "%~dp0.venv\Scripts\python.exe" -m finlens gemini --env-file .env %*
  exit /b
)
python -m finlens gemini --env-file .env %*
