# Builds "Chest Tracker.exe" in this folder. Only needed if you change the code.
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path .venv-app\Scripts\python.exe)) { python -m venv .venv-app }
.venv-app\Scripts\python.exe -m pip install -q --disable-pip-version-check -r requirements-dev.txt

.venv-app\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name "Chest Tracker" `
    --icon "$PSScriptRoot\assets\icon.ico" `
    --add-data "$PSScriptRoot\assets\icon.png;assets" `
    --exclude-module tkinter `
    --distpath . --workpath build --specpath build `
    app.py
