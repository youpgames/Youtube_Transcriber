@echo off
setlocal
cd /d "%~dp0"

echo ========================================
echo Running batch_dossiers mode
echo Uses DATASET_ROOT, DOSSIER_SOURCE, and

echo DOSSIER_IF_EXISTS from config\settings.txt
echo ========================================

if not exist .venv (
    py -m venv .venv
)

.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

where ollama >nul 2>nul
if errorlevel 1 (
    echo ERROR: Ollama is not installed or not in PATH.
    pause
    endlocal
    exit /b 1
)

curl -s http://localhost:11434/api/tags >nul 2>nul
if errorlevel 1 (
    echo Ollama server is not running. Starting it now...
    start "Ollama Server" cmd /k ollama serve
    timeout /t 6 /nobreak >nul
)

.venv\Scripts\python.exe youtube_mass_transcriber.py --mode batch_dossiers
if errorlevel 1 (
    echo ========================================
    echo BATCH DOSSIERS FAILED
    echo ========================================
    pause
    endlocal
    exit /b 1
)

echo ========================================
echo BATCH DOSSIERS DONE

echo ========================================
pause
endlocal
