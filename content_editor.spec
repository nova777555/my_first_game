# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для сборки ContentEditor.exe (один файл, --onefile) из
# tools/content_editor.py. Собирать через build_content_editor.ps1 или
# напрямую: pyinstaller content_editor.spec
#
# В отличие от web_ui.spec (Igra.exe - портативная сборка для друга, с
# вмороженной копией content.json), этот exe - dev-инструмент: НЕ содержит
# content.json как data и ожидает жить прямо в корне репозитория, рядом с
# main.py, чтобы редактировать РЕАЛЬНЫЙ game/content.json (см. докстринг
# tools/content_editor.py). --onefile, а не --onedir - чтобы это был один
# файл, который можно один раз положить в корень репозитория, а не папка с
# кучей DLL прямо вперемешку с исходниками проекта.
import os

ROOT = SPECPATH

a = Analysis(
    [os.path.join(ROOT, "tools", "content_editor.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # content_editor.py ищет content_editor.html через
        # Path(__file__).with_name(...) - должен лежать рядом с самим
        # content_editor.py внутри сборки (корень бандла).
        (os.path.join(ROOT, "tools", "content_editor.html"), "."),
        # Общий с игрой код оформления механизма (превью «Оформление»), тот же приём.
        (os.path.join(ROOT, "tools", "mechanism_fx.js"), "."),
        (os.path.join(ROOT, "tools", "hunt_fx.js"), "."),
        (os.path.join(ROOT, "tools", "quest_fx.js"), "."),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name="ContentEditor",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
)
