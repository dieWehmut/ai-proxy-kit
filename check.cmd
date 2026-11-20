@echo off
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0proxykit.ps1" -Action check %*
if errorlevel 1 pause
