@echo off
cd /d "%~dp0..\frontend"
if not exist "node_modules" (
  echo Run npm install in frontend first.
  exit /b 1
)
npm run dev
