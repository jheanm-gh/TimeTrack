@echo off
REM Builds TimeTrack.
REM
REM Windows blocks unsigned PowerShell scripts by default (the execution
REM policy is "Restricted" on Windows 10 and 11 client editions), so
REM running build.ps1 directly fails with a security error on a clean
REM machine. This wrapper bypasses the policy for this one command only -
REM it changes no system setting and leaves the machine exactly as it was.
REM
REM Double-click this file, or run it from a prompt:   build.cmd
REM Any switches are passed through:                   build.cmd -SkipTests

setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0build.ps1" %*
set BUILD_EXIT=%ERRORLEVEL%

if not "%BUILD_EXIT%"=="0" (
    echo.
    echo The build did not finish. The reason is above.
)

echo.
pause
exit /b %BUILD_EXIT%
