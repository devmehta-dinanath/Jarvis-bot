# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

hiddenimports = []
hiddenimports += collect_submodules("paddleocr")
hiddenimports += collect_submodules("paddle")
hiddenimports += collect_submodules("paddlepaddle")
hiddenimports += collect_submodules("chromadb")
hiddenimports += collect_submodules("uvicorn")
hiddenimports += collect_submodules("googleapiclient")

datas = []
datas += collect_data_files("paddleocr")
datas += collect_data_files("chromadb")

a = Analysis(
    ["app/desktop_entrypoint.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
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
