@echo off
setlocal
rem ============================================================
rem  Qwencode Agent Web Service launcher
rem  Starts agent-service (Spring Boot, port 8800).
rem  The Python executor (port 8910) is auto-started by the service.
rem  Page: http://127.0.0.1:8800/
rem ============================================================

set "ROOT=%~dp0.."
set "JAR=%ROOT%\agent-service\target\qwencode-agent-service-0.3.0.jar"

if not exist "%JAR%" (
  echo [ERROR] jar not found: %JAR%
  echo         build first: mvn -f "%ROOT%\pom.xml" package
  exit /b 1
)

if "%JAVA_HOME%"=="" set "JAVA_HOME=C:\Program Files\Java\jdk-1.8"
if not exist "%JAVA_HOME%\bin\java.exe" (
  echo [ERROR] java not found under JAVA_HOME: %JAVA_HOME%
  exit /b 1
)

cd /d "%ROOT%"
echo [INFO] starting qwencode-agent-service ...
start "qwencode-agent-service" "%JAVA_HOME%\bin\java.exe" -jar "%JAR%"
echo [INFO] started. wait a few seconds then open: http://127.0.0.1:8800/
exit /b 0
