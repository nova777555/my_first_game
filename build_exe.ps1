# Собирает Igra.exe (папка dist/Igra) из tools/web_ui.py через PyInstaller
# и упаковывает её в dist/Igra.zip - этот zip и отправляй другу.
#
# Запуск (из корня репозитория):
#   powershell -ExecutionPolicy Bypass -File build_exe.ps1
#
# Один раз перед первой сборкой нужны PyInstaller и зависимости игры (только
# у тебя, не у друга - в exe всё уже упаковано):
#   pip install pyinstaller -r requirements.txt

$ErrorActionPreference = "Stop"

python -m PyInstaller --version | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Host "PyInstaller не найден. Установи его командой:"
    Write-Host "    pip install pyinstaller"
    exit 1
}

python -m PyInstaller web_ui.spec --noconfirm --distpath dist --workpath build
if ($LASTEXITCODE -ne 0) {
    Write-Host "Сборка не удалась (код $LASTEXITCODE)."
    exit $LASTEXITCODE
}

$distFolder = Join-Path $PSScriptRoot "dist\Igra"
$zipPath = Join-Path $PSScriptRoot "dist\Igra.zip"

# Папка saves рядом с exe - чтобы другу было сразу видно, куда кидать файл
# сохранения (движок и так создаёт её сам при первом "Сохранить как", но
# тогда её не видно сразу при распаковке архива). Совсем пустую папку
# Compress-Archive из архива молча выбрасывает (ZIP без единого файла внутри
# папки её не сохраняет) - кладём внутрь короткую подсказку, она же не даёт
# папке пропасть при упаковке.
$savesDir = Join-Path $distFolder "saves"
New-Item -ItemType Directory -Force -Path $savesDir | Out-Null
Set-Content -Path (Join-Path $savesDir "Сюда кладите файлы сохранений (.json).txt") -Encoding UTF8 -Value @'
Сюда можно положить файл сохранения (.json), которым с тобой поделились -
он появится в списке "Открыть" внутри игры.

Свои сохранения ("Сохранить как") игра кладёт сюда же автоматически.
'@

# Сразу после сборки антивирус ненадолго блокирует свежесозданные файлы (в
# частности _internal\base_library.zip) - Compress-Archive в этом случае
# молча пропускает залоченный файл и НЕ считает это ошибкой (не кидает
# исключение), так что просто проверить "не упало" недостаточно - нужно
# сверить число файлов в архиве с числом файлов на диске и, если не сошлось,
# подождать и попробовать снова.
$expectedCount = (Get-ChildItem -Path $distFolder -Recurse -File).Count
Add-Type -AssemblyName System.IO.Compression.FileSystem

$zipOk = $false
for ($attempt = 1; $attempt -le 5; $attempt++) {
    if (Test-Path $zipPath) {
        Remove-Item $zipPath -Force -Confirm:$false
    }
    Compress-Archive -Path (Join-Path $distFolder "*") -DestinationPath $zipPath -Force -ErrorAction SilentlyContinue

    if (Test-Path $zipPath) {
        $zip = [System.IO.Compression.ZipFile]::OpenRead($zipPath)
        $actualCount = $zip.Entries.Count
        $zip.Dispose()
        if ($actualCount -eq $expectedCount) {
            $zipOk = $true
            break
        }
    }
    Write-Host "Часть файлов ещё занята (вероятно, антивирусом) - жду и пробую снова (попытка $attempt/5)..."
    Start-Sleep -Seconds 3
}

Write-Host ""
if (-not $zipOk) {
    Write-Host "Не получилось собрать полный архив - какие-то файлы остаются занятыми."
    Write-Host "Заархивируй папку $distFolder вручную (правой кнопкой -> Отправить -> Сжатая ZIP-папка)."
    exit 1
}

Write-Host "Готово!"
Write-Host "Папка: $distFolder"
Write-Host "Архив: $zipPath - отправь его другу."
