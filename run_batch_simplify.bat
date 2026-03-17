@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Running batch_simplify mode
echo Uses DATASET_ROOT from config\settings.txt
echo ========================================

if not exist .venv (
    py -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe youtube_mass_transcriber.py --mode batch_simplify

if errorlevel 1 (
    echo ========================================
    echo BATCH SIMPLIFY FAILED
    echo ========================================
    pause
    endlocal
    exit /b 1
)

echo ========================================
echo BATCH SIMPLIFY DONE
echo ========================================
pause
endlocal
