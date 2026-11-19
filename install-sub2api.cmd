@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-sub2api.ps1" %*
if errorlevel 1 (
    echo.
    echo Installation failed. Read the error above, fix it, and run this file again.
    pause
    exit /b 1
)
echo.
echo Installation complete. The login page is open and credentials are in LOGIN.txt in the installation directory.
pause
