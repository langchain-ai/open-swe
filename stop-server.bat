@echo off
REM ========================================
REM NVIDIA NVCRM Agent Swarm - Stop Server
REM Stops all server processes
REM Created: October 7, 2025
REM ========================================

setlocal enabledelayedexpansion

echo.
echo ========================================
echo   NVIDIA NVCRM Agent Swarm
echo   Stop Server v1.0
echo ========================================
echo.

REM Configuration
set "PORT_WEB=3000"
set "PORT_AGENT=2024"

echo [INFO] Stopping all server processes...
echo.

REM ========================================
REM Kill port 3000
REM ========================================
echo [1] Checking port %PORT_WEB% (Web UI)...

set "KILLED_3000=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT_WEB%" ^| findstr "LISTENING"') do (
    set "PID=%%a"
    echo [INFO] Killing process !PID! on port %PORT_WEB%...
    taskkill /F /PID !PID! >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK] Killed PID !PID!
        set "KILLED_3000=1"
    )
)

if "%KILLED_3000%"=="0" (
    echo [INFO] No process found on port %PORT_WEB%
)

echo.

REM ========================================
REM Kill port 2024
REM ========================================
echo [2] Checking port %PORT_AGENT% (Agent Server)...

set "KILLED_2024=0"
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT_AGENT%" ^| findstr "LISTENING"') do (
    set "PID=%%a"
    echo [INFO] Killing process !PID! on port %PORT_AGENT%...
    taskkill /F /PID !PID! >nul 2>&1
    if !errorlevel! equ 0 (
        echo [OK] Killed PID !PID!
        set "KILLED_2024=1"
    )
)

if "%KILLED_2024%"=="0" (
    echo [INFO] No process found on port %PORT_AGENT%
)

echo.

REM ========================================
REM Summary
REM ========================================
echo ========================================
if "%KILLED_3000%"=="1" (
    echo [OK] Port %PORT_WEB% freed
) else (
    echo [INFO] Port %PORT_WEB% was already free
)

if "%KILLED_2024%"=="1" (
    echo [OK] Port %PORT_AGENT% freed
) else (
    echo [INFO] Port %PORT_AGENT% was already free
)
echo ========================================
echo.
echo [DONE] All server processes stopped
echo.
pause


