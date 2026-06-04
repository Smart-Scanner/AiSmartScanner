@echo off
title Smart Screener Launcher
echo ===================================================
echo   Advanced Quant-Based AI Smart Scanner Launcher
echo ===================================================
echo.

:: Check if port 5050 is already listening
netstat -ano | findstr LISTENING | findstr :5050 >nul
if %errorlevel% equ 0 (
    echo [INFO] Smart Screener is already running on port 5050.
    echo [INFO] Opening dashboard in Google Chrome...
    goto open_browser
)

echo [INFO] Smart Screener is not running. Starting Flask server...
:: Run in a new command window so it stays open in the background
start "Smart Screener Server" cmd /k "cd /d c:\Users\91971\Downloads\smart-screener-deploy && python app.py"

echo [INFO] Waiting for server to initialize...
timeout /t 4 /nobreak >nul

:open_browser
:: Try to open in Chrome specifically, fallback to default browser
where chrome >nul 2>nul
if %errorlevel% equ 0 (
    start chrome http://localhost:5050
) else (
    if exist "C:\Program Files\Google\Chrome\Application\chrome.exe" (
        start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" http://localhost:5050
    ) else if exist "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" (
        start "" "C:\Program Files (x86)\Google\Chrome\Application\chrome.exe" http://localhost:5050
    ) else (
        echo [WARNING] Google Chrome not found. Opening in default browser...
        start http://localhost:5050
    )
)

echo.
echo [SUCCESS] Done! You can close this window now.
timeout /t 3 >nul
