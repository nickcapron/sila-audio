@echo off
rem Copies SILA.vst3 (next to this script) into the system VST3 folder that every
rem DAW scans:  C:\Program Files\Common Files\VST3\SILA.vst3
rem Needs administrator rights for that folder; asks for them if it doesn't have them.

setlocal
set "SRC=%~dp0SILA.vst3"
set "DST=%CommonProgramFiles%\VST3"

if not exist "%SRC%" (
    echo Could not find SILA.vst3 next to this script.
    pause
    exit /b 1
)

net session >nul 2>&1
if %errorlevel% neq 0 (
    echo Asking for administrator rights to write to "%DST%"...
    powershell -NoProfile -Command "Start-Process -FilePath '%~f0' -Verb RunAs"
    exit /b 0
)

if not exist "%DST%" mkdir "%DST%"
robocopy "%SRC%" "%DST%\SILA.vst3" /E /NFL /NDL /NJH /NJS /NP >nul
if %errorlevel% geq 8 (
    echo Copy failed.
    pause
    exit /b 1
)

echo.
echo SILA.vst3 installed to "%DST%\SILA.vst3".
echo Rescan plugins in your DAW (or restart it) and look for "SILA" under instruments.
echo.
pause
