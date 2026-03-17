@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Running simplify mode
echo Uses ACTIVE_CREATOR and DOSSIER_SOURCE from

echo config\settings.txt

echo ========================================

if not exist .venv (
    py -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe youtube_mass_transcriber.py --mode simplify

if errorlevel 1 (
    echo ========================================
    echo SIMPLIFY FAILED
    echo ========================================
    pause
    endlocal
    exit /b 1
)

echo ========================================
echo SIMPLIFY DONE
echo ========================================
pause
endlocal
