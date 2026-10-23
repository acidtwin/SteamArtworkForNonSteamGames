# PyInstaller spec for the portable Windows build: a folder with the .exe and everything it
# needs, zipped by the workflow. Run from the repo root after make_icon.py:
#
#   pyinstaller --noconfirm packaging/windows/SteamArtworkForNonSteamGames.spec

import re
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files
from PyInstaller.utils.win32.versioninfo import (
    FixedFileInfo, StringFileInfo, StringStruct, StringTable, VarFileInfo, VarStruct, VSVersionInfo)

ROOT = Path(SPECPATH).parents[1]
NAME = "SteamArtworkForNonSteamGames"
version = re.search(r'__version__ = "([^"]+)"', (ROOT / "steam_artwork_finder/__init__.py").read_text())[1]
nums = tuple(int(n) for n in re.findall(r"\d+", version)[:3]) + (0,)

version_info = VSVersionInfo(
    ffi=FixedFileInfo(filevers=nums, prodvers=nums),
    kids=[
        StringFileInfo([StringTable("080904B0", [
            StringStruct("CompanyName", "Acid Design"),
            StringStruct("FileDescription", "Steam Artwork for Non-Steam Games"),
            StringStruct("FileVersion", version),
            StringStruct("InternalName", NAME),
            StringStruct("LegalCopyright", "MIT licence, Acid Design"),
            StringStruct("OriginalFilename", f"{NAME}.exe"),
            StringStruct("ProductName", "Steam Artwork for Non-Steam Games"),
            StringStruct("ProductVersion", version),
        ])]),
        VarFileInfo([VarStruct("Translation", [0x0809, 1200])]),
    ],
)

# Qt modules the app never uses; PySide6's hooks would otherwise pull some in through plugins.
UNUSED = [
    "tkinter", "unittest", "pydoc", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel", "PySide6.QtPdf",
    "PySide6.Qt3DCore", "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtGraphs",
    "PySide6.QtDesigner", "PySide6.QtHelp", "PySide6.QtSql", "PySide6.QtTest", "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets", "PySide6.QtBluetooth", "PySide6.QtPositioning", "PySide6.QtLocation",
    "PySide6.QtSerialPort", "PySide6.QtSensors", "PySide6.QtTextToSpeech", "PySide6.QtRemoteObjects",
]

a = Analysis(
    [str(ROOT / "packaging/windows/launch.py")],
    pathex=[str(ROOT)],
    datas=collect_data_files("steam_artwork_finder"),
    hiddenimports=["PySide6.QtMultimedia", "PySide6.QtSvg"],
    excludes=UNUSED,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=NAME,
    icon=str(ROOT / "build/app.ico"),
    version=version_info,
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name=NAME, upx=False)
