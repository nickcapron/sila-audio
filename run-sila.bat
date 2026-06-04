@echo off
REM Launch SILA and open it in your browser automatically.
REM Double-click this file, or run it from a terminal.
cd /d "%~dp0"
python -m sila.main --open
pause
