@echo off
echo Starting CodeBuddy2API...

REM Check if Python is installed
python --version >nul 2>&1
if errorlevel 1 (
    echo Python is not installed or not in PATH
    pause
    exit /b 1
)

REM Check if virtual environment exists
if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

REM Activate virtual environment
call venv\Scripts\activate.bat

REM Install dependencies
echo Installing dependencies...
pip install -r requirements.txt

REM .env file will be loaded by Python (python-dotenv) at runtime
if not defined CODEBUDDY_PASSWORD (
    if exist ".env" (
        echo Configuration will be loaded from .env file by python-dotenv
    ) else (
        echo WARNING: .env file not found, CODEBUDDY_PASSWORD may not be set
    )
)

REM Start service
echo Starting CodeBuddy2API service...
python web.py

pause