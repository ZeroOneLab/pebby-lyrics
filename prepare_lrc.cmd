@echo off
setlocal
chcp 65001 >nul

rem Keep this CMD, prepare_lrc.ps1, prepare_lrc.py and requirements-lyrics.txt together.
rem Double-click, drag a music folder here, or pass prepare_lrc.py arguments.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0prepare_lrc.ps1" %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo LRC preparation did not finish successfully. Exit code: %RC%
if "%~1"=="" pause
exit /b %RC%
