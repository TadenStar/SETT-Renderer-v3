# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller-сборка BRM в один exe (раздел 8 спеки, M8).

Запуск: pyinstaller --noconfirm brm.spec  →  dist\\BRM.exe

brm/scripts (override_template.py и другие) и brm/resources (пресеты,
иконка) идут через datas: это не Python-модули, которые PyInstaller
подхватил бы сам, а файлы, которые приложение читает по пути в рантайме
(core/app_paths.py резолвит их через sys._MEIPASS в собранном виде).
"""

block_cipher = None

a = Analysis(
    ["brm/__main__.py"],
    pathex=[],
    binaries=[],
    datas=[
        ("brm/scripts", "brm/scripts"),
        ("brm/resources", "brm/resources"),
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    cipher=block_cipher,
)
pyz = PYZ(a.pure, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="BRM",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon="brm/resources/icon/brm.ico",
)
