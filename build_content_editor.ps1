# Собирает ContentEditor.exe (один файл) из tools/content_editor.py через
# PyInstaller и кладёт его прямо в корень репозитория, рядом с main.py - этот
# exe специально портативный dev-инструмент ДЛЯ ЭТОГО репозитория (редактирует
# реальный game/content.json), а не то, что отправляется другу (см. докстринг
# tools/content_editor.py и content_editor.spec). В git не коммитится
# (см. .gitignore).
#
# Запуск (из корня репозитория):
#   powershell -ExecutionPolicy Bypass -File build_content_editor.ps1
#
# Один раз перед первой сборкой нужны PyInstaller и зависимости игры (то же,
# что для build_exe.ps1):
#   pip install pyinstaller -r requirements.txt

$ErrorActionPreference = "Stop"

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller не найден. Установи его командой:"
    Write-Host "    pip install pyinstaller"
    exit 1
}

python -m PyInstaller content_editor.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Сборка не удалась (код $LASTEXITCODE)."
    exit $LASTEXITCODE
}

$builtExe = Join-Path $PSScriptRoot "dist\ContentEditor.exe"
$targetExe = Join-Path $PSScriptRoot "ContentEditor.exe"
Move-Item -Path $builtExe -Destination $targetExe -Force

Write-Host ""
Write-Host "Готово! ContentEditor.exe лежит в корне репозитория - можно запускать двойным кликом оттуда."
