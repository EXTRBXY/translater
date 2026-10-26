@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
git pull --ff-only
if errorlevel 1 (
    echo Не удалось обновить исходники командой git pull. Сборка остановлена.
    pause
    exit /b 1
)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\build.ps1" %*
set "code=%errorlevel%"
if not "%code%"=="0" echo Сборка не удалась, код %code%.
pause
exit /b %code%
