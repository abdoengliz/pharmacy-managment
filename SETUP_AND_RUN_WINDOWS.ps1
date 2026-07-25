$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".\venv\Scripts\python.exe")) {
    Write-Host "Creating Python virtual environment..."
    python -m venv venv
}

Write-Host "Installing requirements..."
& ".\venv\Scripts\python.exe" -m pip install --upgrade pip
& ".\venv\Scripts\python.exe" -m pip install -r requirements.txt

Write-Host "Starting Pharma ERP..."
& ".\venv\Scripts\python.exe" app.py
