@echo off
cd /d "%~dp0.."
set "VENV_DIR="
if exist "venv\Scripts\activate.bat" set "VENV_DIR=venv"
if exist ".venv\Scripts\activate.bat" set "VENV_DIR=.venv"
if not defined VENV_DIR (
  echo Create a venv first: python -m venv .venv
  exit /b 1
)
call "%VENV_DIR%\Scripts\activate.bat"
uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
