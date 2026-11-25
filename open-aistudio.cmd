@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0proxykit.ps1" -Action access
if errorlevel 1 (
    pause
    exit /b 1
)
start "" "%~dp0runtime\open-aistudio.html"
