@echo off
REM Norbae AI - records the 20-second VP demo flow in the real FY27 cockpit (free, local).
REM Needs: run_streamlit.bat to have been run once (creates .venv). Installs Playwright (free) into .venv on first run.
REM Uses your installed Microsoft Edge / Chrome - no browser download. Your own draft plan is not touched.
setlocal
cd /d "%~dp0"
set "VPY=%~dp0.venv\Scripts\python.exe"
if not exist "%VPY%" (
  echo Please run run_streamlit.bat once first, then run this again.
  pause
  exit /b 1
)
"%VPY%" -c "import playwright" >nul 2>&1
if errorlevel 1 (
  echo Installing Playwright - free, open source, about 40 MB...
  "%VPY%" -m pip install playwright
)
echo Checking the small video encoder Playwright uses - free, about 2 MB, first run only...
"%VPY%" -m playwright install ffmpeg
echo.
echo Recording the demo - a browser window will open and drive itself. Please don't touch it (about 1-2 minutes).
"%VPY%" tools\record_cockpit_demo.py
echo.
echo Done. The raw recording is in Marketing\raw\  - tell Claude it's ready.
pause
