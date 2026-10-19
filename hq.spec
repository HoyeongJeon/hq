# -*- mode: python ; coding: utf-8 -*-
# HQ exe: uv run pyinstaller hq.spec --noconfirm --clean  →  dist\hq.exe
import importlib.util
import os

# pywinpty에는 PyInstaller hook이 없다. _winpty.pyd는 conpty.dll·winpty.dll을 읽고, 그 둘은 자기 옆의 OpenConsole.exe·
# winpty-agent.exe를 띄운다. 개발 때와 같이 네 파일을 모두 winpty/ 안(_winpty.pyd 옆)에 둔다.
WINPTY_DIR = os.path.dirname(importlib.util.find_spec("winpty").origin)
WINPTY_NATIVE = ["OpenConsole.exe", "conpty.dll", "winpty-agent.exe", "winpty.dll"]

a = Analysis(
    ["src/hq/__main__.py"],
    pathex=["src"],
    binaries=[(os.path.join(WINPTY_DIR, name), "winpty") for name in WINPTY_NATIVE],
    datas=[
        ("src/hq/static", "hq/static"),
        ("src/hq/ps", "hq/ps"),
        ("src/hq/assets", "hq/assets"),
    ],
    hiddenimports=["webview.platforms.winforms"],
    excludes=["tkinter", "PIL"],
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="hq",
    console=False,
    icon="src/hq/assets/hq.ico",
    upx=False,
)
