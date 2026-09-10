# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec для сборки Igra.exe (папка --onedir) из tools/web_ui.py.
# Собирать через build_exe.ps1 или напрямую: pyinstaller web_ui.spec
import os

ROOT = SPECPATH

a = Analysis(
    [os.path.join(ROOT, "tools", "web_ui.py")],
    pathex=[ROOT],
    binaries=[],
    datas=[
        # web_ui.py ищет web_ui.html через Path(__file__).with_name(...) -
        # должен лежать рядом с самим web_ui.py внутри сборки (корень бандла).
        (os.path.join(ROOT, "tools", "web_ui.html"), "."),
        # game/content.py ищет content.json тем же паттерном - должен лежать
        # рядом с game/content.py внутри сборки, то есть в подпапке game/.
        (os.path.join(ROOT, "game", "content.json"), "game"),
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
    [],
    exclude_binaries=True,
    name="Igra",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name="Igra",
)
