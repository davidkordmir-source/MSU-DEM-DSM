@echo off
setlocal
cd /d "%~dp0"
title MSU Terrain - Offline
if not exist "runtime\python.exe" (
  echo The portable runtime was not found. Extract the complete Windows ZIP first.
  echo This launcher does not download or install anything.
  pause
  exit /b 1
)
"runtime\python.exe" -I "run_offline.py"
if errorlevel 1 (
  echo.
  echo The application could not start. Copy the error above when requesting support.
  pause
  exit /b 1
)
endlocal
