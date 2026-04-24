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

title PSX Advisor

REM Always run from the project directory regardless of where the shortcut
REM was clicked from.
cd /d "%~dp0"

echo.
echo  PSX Advisor — launching
echo  Project:  %CD%
echo.

REM ---- 1) Pull latest data from GitHub (best-effort; never blocks launch) ----
where git >nul 2>&1
if %errorlevel% == 0 (
    echo  Pulling latest data from GitHub...
    git pull --ff-only --no-rebase 2>nul
    if errorlevel 1 (
        echo    ^(git pull failed or no upstream - continuing with local data^)
    )
    echo.
) else (
    echo  git not found in PATH - skipping data refresh
    echo.
)

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
echo  ^(Close this window to stop the app^)
echo.
python -m streamlit run ui\app.py

REM If Streamlit exits with an error, keep the window open so the user can
REM read the traceback.
if errorlevel 1 (
    echo.
    echo  Streamlit exited with an error. Press any key to close.
    pause >nul
)
