#!/bin/bash
echo "Starting CodeBuddy2API..."

# Check if Python is installed
if ! command -v python3 &>/dev/null; then
    echo "Python3 is not installed or not in PATH"
    exit 1
fi

# Check if virtual environment exists
if [ ! -d "venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv venv
fi

# Activate virtual environment
source venv/bin/activate

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# .env file will be loaded by Python (python-dotenv) at runtime
if [ -z "$CODEBUDDY_PASSWORD" ]; then
    if [ -f ".env" ]; then
        echo "Configuration will be loaded from .env file by python-dotenv"
    else
        echo "WARNING: .env file not found, CODEBUDDY_PASSWORD may not be set"
    fi
fi

# Start service
echo "Starting CodeBuddy2API service..."
python web.py
