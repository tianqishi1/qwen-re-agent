@echo off
setlocal
rem ============================================================
rem  Stop Qwencode services: agent-service (8800) and
rem  Python executor (8910), matched by listening port.
rem ============================================================

for %%P in (8800 8910) do (
  for /f "tokens=5" %%a in ('netstat -ano ^| findstr "LISTENING" ^| findstr ":%%P "') do (
    taskkill /F /PID %%a >nul 2>&1
    if not errorlevel 1 echo [INFO] stopped process %%a on port %%P
  )
)

echo [INFO] done
exit /b 0
