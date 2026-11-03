@echo off
REM Run the automated test suite and the Evaluation Lab checks (Windows)
cd /d "%~dp0"
python -m unittest discover -s tests -v
python src\evaluation.py
pause
