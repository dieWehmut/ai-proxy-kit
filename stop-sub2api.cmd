@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop-sub2api.ps1" %*
if errorlevel 1 (
    echo.
    echo Sub2API could not stop. Read the error above.
    pause
    exit /b 1
)
