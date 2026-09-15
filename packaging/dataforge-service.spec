# PyInstaller spec for the DataForge application service (one-folder build for fast startup).
# Build from the repository root:  python -m PyInstaller packaging/dataforge-service.spec --distpath build/service --workpath build/pyinstaller
from pathlib import Path

root = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller

a = Analysis(  # noqa: F821
    [str(root / "packaging" / "service_entry.py")],
    pathex=[str(root / "services/application/src"), str(root / "workers/matching/src"), str(root / "workers/scraping/src")],
    hiddenimports=["dataforge_matching.engine", "dataforge_matching.ranking", "dataforge_scraping.packages", "openpyxl", "rapidfuzz.fuzz"],
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy", "pandas"],
    noarchive=False,
)
pyz = PYZ(a.pure)  # noqa: F821
exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="dataforge-service",
    console=True,  # stdio is the IPC channel; the host hides the console window
    upx=False,
)
coll = COLLECT(exe, a.binaries, a.datas, name="dataforge-service", upx=False)  # noqa: F821
