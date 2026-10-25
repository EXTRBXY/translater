<#
Собирает dist\Translater\Translater.exe и проверяет его самопроверкой — так же, как workflow релиза.
Запуск: powershell -ExecutionPolicy Bypass -File tools\build.ps1
#>
param(
    [string]$Python = "",
    [switch]$SkipTests
)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

function Invoke-Step([string]$Title, [scriptblock]$Action) {
    Write-Host "== $Title"
    & $Action
    if ($LASTEXITCODE) { throw "$Title завершилось с кодом $LASTEXITCODE" }
}

if (-not $Python) {
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        Invoke-Step "Окружение .venv (Python 3.14)" { py -3.14 -m venv .venv }
    }
    $Python = ".venv\Scripts\python.exe"
}

Invoke-Step "Зависимости" { & $Python -m pip install --disable-pip-version-check -q -e ".[dev,build]" }
if (-not $SkipTests) {
    Invoke-Step "Тесты" { & $Python -m pytest -q }
}
Invoke-Step "Сборка exe" {
    & $Python -m PyInstaller --noconfirm --clean --windowed --onedir --log-level WARN `
        --name Translater --icon assets/translater.ico `
        --collect-data faster_whisper --collect-binaries ctranslate2 `
        src/translater/__main__.py
}

Write-Host "== Самопроверка exe"
$report = Join-Path ([IO.Path]::GetTempPath()) "translater-selftest.json"
Remove-Item $report, "$report.worker.log" -ErrorAction SilentlyContinue
$process = Start-Process -FilePath "dist\Translater\Translater.exe" -ArgumentList "--self-test", "`"$report`"" -PassThru
$finished = $process.WaitForExit(180000)
if (-not $finished) { taskkill /PID $process.Id /T /F | Out-Null }
if (Test-Path $report) { Get-Content $report }
if (-not $finished -or $process.ExitCode -ne 0) {
    foreach ($file in @("$report.worker.log", "$env:LOCALAPPDATA\Translater\logs\translater.log")) {
        if (Test-Path $file) { Write-Host "== $file"; Get-Content $file -Tail 50 }
    }
    throw "Самопроверка не прошла"
}
Write-Host "Готово: $((Resolve-Path 'dist\Translater\Translater.exe').Path)"
