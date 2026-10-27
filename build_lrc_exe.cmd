@echo off
setlocal
chcp 65001 >nul

rem Build lrc_gui.py into a single-file Pebby-Lyrics.exe.
rem PyInstaller 6.16.0 runs inside the isolated env .lrc-env created by prepare_lrc.cmd.
rem Usage: double-click, or run build_lrc_exe.cmd [--no-pause]
rem Output: build\output\Pebby-Lyrics.exe (git-ignored; CI publishes it as a Release asset).
rem Keep this file ASCII-only: cmd.exe reads it in the OEM code page.

set "ROOT=%~dp0"
set "PY=%ROOT%.lrc-env\Scripts\python.exe"
set "OUT=%ROOT%build\output"
set "WORK=%ROOT%build\pyinstaller"

if not exist "%PY%" (
    echo [error] missing isolated env: %PY%
    echo         run prepare_lrc.cmd once to install Python and dependencies.
    goto :fail
)

"%PY%" -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [info] PyInstaller not found in the isolated env, installing 6.16.0 ...
    "%PY%" -m pip install --disable-pip-version-check --no-input "pyinstaller==6.16.0"
    if errorlevel 1 (
        echo [error] PyInstaller installation failed, check the network and retry.
        goto :fail
    )
)

for /f "delims=" %%V in ('"%PY%" -c "from version import __version__; print(__version__)"') do set "VER=%%V"

echo [info] building Pebby-Lyrics v%VER% ...
"%PY%" -m PyInstaller --noconfirm --clean --onefile --windowed --name Pebby-Lyrics ^
    --runtime-tmpdir . ^
    --collect-data opencc ^
    --distpath "%OUT%" --workpath "%WORK%" --specpath "%WORK%" ^
    "%ROOT%lrc_gui.py"
if errorlevel 1 (
    echo [error] build failed, see the PyInstaller output above.
    goto :fail
)

for %%F in ("%OUT%\Pebby-Lyrics.exe") do set "SIZE=%%~zF"
echo [done] %OUT%\Pebby-Lyrics.exe  (%SIZE% bytes)
echo        Tag it as v%VER% and push to let GitHub Actions publish the Release asset.
if /i not "%~1"=="--no-pause" pause
exit /b 0

:fail
if /i not "%~1"=="--no-pause" pause
exit /b 1
