@echo off
echo ==========================================
echo   CMMS Backend Setup ^& Start
echo ==========================================

cd /d "%~dp0backend"

:: Check Python
python --version >nul 2>&1
if errorlevel 1 (
    echo ERROR: Python not found. Please install Python 3.12 from https://python.org
    pause
    exit /b 1
)

:: Create virtual environment if not exists
if not exist "venv\" (
    echo Creating virtual environment...
    python -m venv venv
)

:: Activate venv
call venv\Scripts\activate.bat

:: Install dependencies
echo Installing dependencies...
pip install -r requirements.txt --quiet

:: Run seed if database doesn't exist
if not exist "cmms.db" (
    echo Running seed script...
    python seed.py
)

:: Start backend
echo.
echo ==========================================
echo   Backend running at http://localhost:8000
echo   API Docs at   http://localhost:8000/docs
echo   Press CTRL+C to stop
echo ==========================================
echo.
uvicorn main:app --host 0.0.0.0 --port 8000 --reload