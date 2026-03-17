@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Running scrape mode
echo Uses config\channels.txt, config\searches.txt,
echo config\filters.txt, and config\settings.txt
echo ========================================

if not exist .venv (
    py -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
.venv\Scripts\python.exe youtube_mass_transcriber.py --mode scrape

if errorlevel 1 (
    echo ========================================
    echo SCRAPE FAILED
    echo ========================================
    pause
    endlocal
    exit /b 1
)

echo ========================================
echo SCRAPE DONE
echo ========================================
pause
endlocal
