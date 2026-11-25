@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0import-aistudio2api-session.ps1" %*
set "importExitCode=%errorlevel%"
if "%~1"=="" pause
exit /b %importExitCode%
