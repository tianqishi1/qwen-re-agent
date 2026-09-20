@echo off
rem ============================================================
rem  Qwencode Coding Agent - build script (Windows)
rem  Uses bundled portable Maven + system JDK 8
rem ============================================================
setlocal

set "ROOT=%~dp0.."
set "MAVEN=%ROOT%\tools\apache-maven-3.9.9\bin\mvn.cmd"

rem Prefer a full JDK that ships javac
if exist "%ProgramFiles%\Java\jdk-1.8\bin\javac.exe" (
    set "JAVA_HOME=%ProgramFiles%\Java\jdk-1.8"
) else if exist "C:\Program Files\Java\jdk-1.8\bin\javac.exe" (
    set "JAVA_HOME=C:\Program Files\Java\jdk-1.8"
)
if not defined JAVA_HOME (
    echo [ERROR] JDK not found (javac required). Install JDK 8 or set JAVA_HOME.
    exit /b 1
)

echo Using JAVA_HOME=%JAVA_HOME%
call "%MAVEN%" -f "%ROOT%\java-core\pom.xml" clean package -DskipTests %*
if errorlevel 1 (
    echo [ERROR] build failed
    exit /b 1
)

echo.
echo Build OK: %ROOT%\java-core\target\qwencode-agent-0.2.0-alpha.jar
endlocal
