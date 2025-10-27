@echo off
title NVIDIA Open SWE Server Manager

echo.
echo ============================================
echo   NVIDIA Open SWE - Server Manager
echo   With LLM Gateway Integration
echo ============================================
echo.

REM Kill all Node processes automatically
echo [1/3] Stopping existing processes...
taskkill /F /IM node.exe >nul 2>&1
timeout /t 2 /nobreak >nul
echo       Done
echo.

REM Build project
echo [2/3] Building project...
call yarn build
if %errorlevel% neq 0 (
    echo       [ERROR] Build failed!
    echo.
    echo Press any key to exit...
    pause >nul
    exit /b 1
)
echo       Build successful
echo.

REM Start server
echo [3/3] Starting server...
echo.
echo ============================================
echo   Server URLs:
echo     Web UI:  http://localhost:3000
echo     API:     http://localhost:2024
echo.
echo   Configuration:
echo     Provider: NVIDIA LLM Gateway
echo     Models:   All GPT-4o
echo.
echo   Press Ctrl+C to stop
echo ============================================
echo.

call yarn dev

REM On exit - keep window open
echo.
echo ============================================
echo   Server Stopped
echo ============================================
echo.
echo Press any key to close...
pause >nul
exit /b 0