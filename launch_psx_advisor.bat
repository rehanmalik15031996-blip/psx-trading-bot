@echo off
REM ==========================================================================
REM PSX Advisor — local launcher
REM
REM Pulls any new data from GitHub (committed by the daily CI workflows),
REM then launches the Streamlit UI. Output stays in this console so you can
REM see errors and copy the localhost URL.
REM
REM Double-click this file, or use the "PSX Advisor" desktop shortcut.
REM ==========================================================================

setlocal enabledelayedexpansion
title PSX Advisor

REM Always run from the project directory regardless of where the shortcut
REM was clicked from.
cd /d "%~dp0"

echo.
echo  PSX Advisor -- launching
echo  Project:  %CD%
echo.

REM ---- 1) Pull latest data from GitHub (best-effort; never blocks launch) ----
where git >nul 2>&1
if %errorlevel% neq 0 (
    echo  git not found in PATH - skipping data refresh
    echo.
    goto :check_python
)

echo  Pulling latest data from GitHub...

REM If .env contains GITHUB_TOKEN, use it as a bearer header for HTTPS auth.
REM This avoids the Git Credential Manager popup on private repos and works
REM even when the user has never authenticated to GitHub on this machine.
set "PSX_GH_TOKEN="
if exist ".env" (
    for /f "usebackq tokens=1,* delims==" %%A in (`findstr /b /c:"GITHUB_TOKEN=" .env`) do (
        set "PSX_GH_TOKEN=%%B"
    )
)
REM Strip surrounding quotes and trailing whitespace/CR, if any.
if defined PSX_GH_TOKEN (
    set "PSX_GH_TOKEN=!PSX_GH_TOKEN:"=!"
    for /f "tokens=* delims= " %%A in ("!PSX_GH_TOKEN!") do set "PSX_GH_TOKEN=%%A"
)

if defined PSX_GH_TOKEN (
    REM Bypass any cached/broken credentials in the Windows credential
    REM manager by pulling from a one-shot URL that embeds the token.
    REM Works for both classic and fine-grained PATs and never writes the
    REM token back to disk.
    set "PSX_REMOTE_URL="
    for /f "delims=" %%A in ('git remote get-url origin') do set "PSX_REMOTE_URL=%%A"
    set "PSX_BRANCH=main"
    for /f "delims=" %%A in ('git rev-parse --abbrev-ref HEAD') do set "PSX_BRANCH=%%A"
    REM Strip https:// so we can splice "x-access-token:TOKEN@" in front of the host.
    set "PSX_REMOTE_TAIL=!PSX_REMOTE_URL:https://=!"
    git -c credential.helper= pull "https://x-access-token:!PSX_GH_TOKEN!@!PSX_REMOTE_TAIL!" !PSX_BRANCH! --ff-only --no-rebase
    set "PSX_REMOTE_URL="
    set "PSX_REMOTE_TAIL="
    set "PSX_BRANCH="
) else (
    git pull --ff-only --no-rebase
)
set "PSX_PULL_RC=%errorlevel%"
REM Clear the token from the environment as soon as it is no longer needed.
set "PSX_GH_TOKEN="

if %PSX_PULL_RC% neq 0 (
    echo.
    echo    git pull returned an error (see above^) - continuing with local data.
    if not defined PSX_GH_TOKEN_WAS_SET (
        echo    Tip: put a line   GITHUB_TOKEN=ghp_xxx   in your .env file so
        echo         the launcher can auto-authenticate without popups.
    )
)
echo.

:check_python
REM ---- 2) Verify Python is available ----
where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo  ERROR: python is not on PATH.
    echo  Install Python 3.11+ from https://www.python.org/downloads/
    echo  and make sure "Add python.exe to PATH" is ticked during setup.
    echo.
    pause
    exit /b 1
)

REM ---- 3) Verify streamlit is installed ----
python -c "import streamlit" >nul 2>&1
if errorlevel 1 (
    echo.
    echo  streamlit is not installed. Installing requirements now...
    python -m pip install --user -r requirements.txt
    if errorlevel 1 (
        echo  Installation failed. Run manually: pip install -r requirements.txt
        pause
        exit /b 1
    )
)

REM ---- 4) Launch the UI ----
echo  Starting Streamlit on http://localhost:8501 ...
echo  (Close this window to stop the app^)
echo.
python -m streamlit run ui\app.py

REM If Streamlit exits with an error, keep the window open so the user can
REM read the traceback.
if errorlevel 1 (
    echo.
    echo  Streamlit exited with an error. Press any key to close.
    pause >nul
)

endlocal
