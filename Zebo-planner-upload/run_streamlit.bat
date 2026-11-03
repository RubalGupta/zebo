@echo off
REM Norbae AI - FY27 Planning Cockpit (Streamlit) launcher for Windows.
REM First run creates a private Python environment (.venv) and installs Streamlit (free). Later runs start fast.
REM Everything is also written to streamlit_log.txt so problems can be diagnosed.
setlocal
cd /d "%~dp0"
set "LOG=%~dp0streamlit_log.txt"
echo ==== %date% %time% ==== > "%LOG%"

set "PY="
where py >nul 2>&1
if not errorlevel 1 set "PY=py -3"
if not defined PY (
  where python >nul 2>&1
  if not errorlevel 1 set "PY=python"
)
if not defined PY goto nopython

if exist ".venv\Scripts\python.exe" goto haveenv
echo Setting up for the first time (1-3 minutes)...
%PY% -m venv .venv >> "%LOG%" 2>&1
if not exist ".venv\Scripts\python.exe" goto venvfail
:haveenv
set "VPY=%~dp0.venv\Scripts\python.exe"

"%VPY%" -c "import streamlit" >nul 2>&1
if not errorlevel 1 goto havest
echo Installing Streamlit (free, open source)...
"%VPY%" -m pip install --upgrade pip >> "%LOG%" 2>&1
"%VPY%" -m pip install -r requirements-streamlit.txt >> "%LOG%" 2>&1
"%VPY%" -c "import streamlit" >nul 2>&1
if errorlevel 1 goto installfail
:havest

"%VPY%" -c "import sys, streamlit; print('Python', sys.version.split()[0], '| Streamlit', streamlit.__version__)" >> "%LOG%" 2>&1
"%VPY%" -c "import sys, streamlit; print('Python', sys.version.split()[0], '| Streamlit', streamlit.__version__)"
echo.
echo Starting the FY27 Planning Cockpit...
echo Your browser should open at http://localhost:8501  (if it doesn't, open that address yourself)
echo Keep this window open while you use the app. Close it to stop the app.
echo.
"%VPY%" -m streamlit run streamlit_app.py --server.headless false --server.address localhost
echo.
echo The app has stopped. If that was unexpected, please send a screenshot of this window
echo (setup details are saved in streamlit_log.txt in this folder).
pause
exit /b 0

:nopython
echo Python was not found on this computer.
echo Install it free from https://www.python.org/downloads/ and tick "Add python.exe to PATH", then run this again.
echo Python not found >> "%LOG%"
pause
exit /b 1

:venvfail
echo Could not create the Python environment. Details are in streamlit_log.txt.
pause
exit /b 1

:installfail
echo Streamlit could not be installed (check your internet connection). Details are in streamlit_log.txt.
pause
exit /b 1
