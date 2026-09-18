#!/bin/bash
# E2E test environment setup — runs on WINDOWS HOST (Git Bash), NOT inside Docker.
# The .venv/Scripts/activate path is correct for Windows Python venvs.
# Safe to re-run after full_reset (pip cache + Chromium binary persist in AppData).
set -e

cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python -m venv .venv
fi

source .venv/Scripts/activate
pip install -r requirements.txt
playwright install chromium webkit   # webkit = the mobile lane's Safari engine (B31)
echo ""
echo "Setup complete. Activate venv with: source .venv/Scripts/activate"
