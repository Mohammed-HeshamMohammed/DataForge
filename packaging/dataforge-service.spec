# PyInstaller spec for the DataForge application service (one-folder build for fast startup).
# Build from the repository root:  python -m PyInstaller packaging/dataforge-service.spec --distpath build/service --workpath build/pyinstaller
# The same executable is also the Scrapy engine child process (`dataforge-service --engine scrapy --job <file>`).
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, collect_submodules

root = Path(SPECPATH).parent  # noqa: F821 - provided by PyInstaller

datas, binaries, hiddenimports = [], [], [
    "dataforge_matching.engine", "dataforge_matching.ranking", "dataforge_scraping.packages", "openpyxl", "rapidfuzz.fuzz",
    *collect_submodules("dataforge_scraping"),
]
# Packages that load modules by name (Scrapy settings, Twisted reactors) or ship data files (models, stoplists,
# public-suffix snapshot, phone metadata, schema.org mappings).
for package in (
    "scrapy_deltafetch", "spidermon", "itemadapter", "itemloaders", "w3lib", "parsel", "protego", "queuelib",
    "extruct", "pyRdfa", "rdflib", "mf2py", "html_text", "jstyleson", "lxml_html_clean", "trafilatura", "justext", "htmldate", "courlan",
    "dateparser", "tldextract", "phonenumbers", "usaddress", "price_parser", "selectolax", "pdfplumber", "pdfminer", "pypdfium2", "pypdfium2_raw",
    "autoscraper", "feedparser", "markdownify", "warcio", "hishel", "truststore", "babel", "dataforge_scraping",
):
    package_datas, package_binaries, package_hidden = collect_all(package)
    datas += package_datas
    binaries += package_binaries
    hiddenimports += package_hidden

# Scrapy and Twisted import components by dotted path; their test suites are left out.
for package in ("scrapy", "twisted"):
    hiddenimports += collect_submodules(package, filter=lambda name: ".test" not in name and "iocpreactor" not in name and ".conch" not in name)
    datas += collect_data_files(package)

a = Analysis(  # noqa: F821
    [str(root / "packaging" / "service_entry.py")],
    pathex=[str(root / "services/application/src"), str(root / "workers/matching/src"), str(root / "workers/scraping/src")],
    datas=datas,
    binaries=binaries,
    hiddenimports=hiddenimports,
    # rfc3987 is GPL-3.0-or-later and only an optional jsonschema format checker; it must never be bundled.
    excludes=["tkinter", "pytest", "IPython", "matplotlib", "numpy", "pandas", "rfc3987", "scrapy.commands.shell", "IPython.terminal"],
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
