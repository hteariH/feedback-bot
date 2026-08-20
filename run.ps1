# Запуск бота локально (Windows).
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path ".venv")) {
    py -m venv .venv
    .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}
if (-not (Test-Path ".env")) {
    Copy-Item .env.example .env
    Write-Host "Создан .env — впиши BOT_TOKEN и ADMIN_IDS, потом запусти снова." -ForegroundColor Yellow
    exit 1
}
.\.venv\Scripts\python.exe bot.py
