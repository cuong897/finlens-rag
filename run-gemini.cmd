@echo off
cd /d "%~dp0"
python -m finlens gemini --env-file .env %*
