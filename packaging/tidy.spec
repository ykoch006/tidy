# PyInstaller spec for the Tidy desktop app (Windows one-folder build).
#   pyinstaller packaging/tidy.spec --noconfirm
# Output: dist/Tidy/Tidy.exe
# ruff: noqa
from pathlib import Path

ROOT = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller
SRC = ROOT / "src"

a = Analysis(  # noqa: F821
    [str(ROOT / "packaging" / "launch.py")],
    pathex=[str(SRC)],
    datas=[(str(SRC / "tidy" / "ui" / "assets"), "tidy/ui/assets")],
    hiddenimports=["tidy.ui.features", "tidy.ui.ai_panel", "tidy.ui.pages.duplicates",
                   "docx", "pymupdf"],
    excludes=[
        "tkinter", "unittest", "pytest", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets",
        "PySide6.Qt3DCore", "PySide6.QtQuick", "PySide6.QtQml", "PySide6.QtMultimedia",
        "PySide6.QtCharts", "PySide6.QtDataVisualization", "PySide6.QtPdf",
    ],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="Tidy",
    icon=str(ROOT / "assets" / "tidy.ico"),
    console=False,
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="Tidy", upx=False)  # noqa: F821
