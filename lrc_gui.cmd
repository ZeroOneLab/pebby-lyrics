@echo off
setlocal
chcp 65001 >nul

rem Keep this CMD, lrc_gui.py and prepare_lrc.py in the same tools directory.
set "SCRIPT=%~dp0lrc_gui.py"

if exist "%~dp0.lrc-env\Scripts\pythonw.exe" (
    start "" "%~dp0.lrc-env\Scripts\pythonw.exe" "%SCRIPT%"
    exit /b 0
)

where pythonw >nul 2>nul
if errorlevel 1 (
    echo 未找到 Python。请先安装 Python 3.10+，或使用打包后的 EXE。
    pause
    exit /b 1
)

start "" pythonw "%SCRIPT%"
exit /b 0
