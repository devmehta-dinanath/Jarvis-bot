# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec for the desktop client (APP_ROLE=client).

import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

spec_dir = os.path.dirname(os.path.abspath(SPEC))

hiddenimports = []
hiddenimports += collect_submodules("app")
hiddenimports += collect_submodules("paddleocr")
hiddenimports += collect_submodules("paddle")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("googleapiclient")

datas = []
datas += collect_data_files("paddleocr")

a = Analysis(
    ["app/desktop_entrypoint.py"],
    pathex=[spec_dir],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["chromadb", "openai"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
