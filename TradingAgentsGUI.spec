# -*- mode: python ; coding: utf-8 -*-

import os


a = Analysis(
    [os.path.join('gui', 'app.py')],
    pathex=[],
    binaries=[],
    datas=[('assets/*.png', 'assets')],
    hiddenimports=['certifi'],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='TradingAgentsGUI',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='TradingAgentsGUI',
)
app = BUNDLE(
    exe,
    name='TradingAgentsGUI.app',
    icon=None,
    bundle_identifier='com.tauricresearch.tradingagents',
)
