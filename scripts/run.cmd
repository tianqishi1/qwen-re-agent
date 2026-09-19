@echo off
rem ============================================================
rem  Qwencode Coding Agent - launcher (Windows)
rem
rem  Config : edit qwencode.properties at project root (api-key etc.)
rem           or override via CLI args (e.g. --api-key sk-xxx)
rem
rem  Usage  :
rem    run.cmd -p "task description" [options]
rem    run.cmd                       (interactive REPL)
rem ============================================================
setlocal

set "ROOT=%~dp0.."
set "JAR=%ROOT%\java-core\target\qwencode-agent-0.2.0-alpha.jar"
set "CONFIG=%ROOT%\qwencode.properties"

if not exist "%JAR%" (
    echo [ERROR] build artifact not found. Run scripts\build.cmd first.
    exit /b 1
)

if exist "%ProgramFiles%\Java\jdk-1.8\bin\java.exe" (
    set "JAVA=%ProgramFiles%\Java\jdk-1.8\bin\java.exe"
) else (
    set "JAVA=java"
)

rem Switch console to UTF-8 codepage for CJK output
chcp 65001 >nul

"%JAVA%" -jar "%JAR%" --config "%CONFIG%" %*
endlocal
