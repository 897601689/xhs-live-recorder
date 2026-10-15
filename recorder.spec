# -*- mode: python ; coding: utf-8 -*-
"""打包配置：pyinstaller recorder.spec

产物是单文件 dist/小红书直播录制器.exe。三点注意都写在下面的字段旁边：
- console=False：双击不弹黑框；终端模式（--cli）请用源码跑
- icon：app_icon.ico 由 tools/make_icon.py 生成，仓库里带着，打包时不用再画
- ffmpeg 不进包：它是外部可执行文件，放在 exe 同目录或系统 PATH 里（见 xhs/config.py）
"""

a = Analysis(
    ["main.py"],
    pathex=[],
    binaries=[],
    datas=[],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "pytest"],
    noarchive=False,
)

pyz = PYZ(a.pure)     # 只装纯 Python 模块；a.scripts 是入口脚本，塞进 PYZ 会在第一行就断言失败

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="小红书直播录制器",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=["app_icon.ico"],
)
