"""Build-only inspection of modules embedded in the production executable."""

import json
import sys
import zipfile
from pathlib import Path

from PyInstaller.archive.readers import CArchiveReader

binary = Path(sys.argv[1])
archive = CArchiveReader(str(binary))
modules = list(archive.open_embedded_archive("PYZ.pyz").toc)
forbidden = {
    "pytest",
    "_pytest",
    "pytest_asyncio",
    "pip",
    "ensurepip",
    "setuptools",
    "PyInstaller",
    "debugpy",
    "pdb",
    "bdb",
    "doctest",
    "pygments",
    "unittest",
    "tests",
    "fixtures",
}
bad = [
    name
    for name in modules
    if any(part in forbidden for part in name.split("."))
    or name.startswith(
        (
            "langsmith.testing",
            "lxml.usedoctest",
            "lxml.html.usedoctest",
            "lxml.doctestcompare",
            "reportlab.graphics.testshapes",
        )
    )
]
if bad:
    raise RuntimeError("Non-production Python modules: " + ", ".join(bad[:30]))
with zipfile.ZipFile(binary.parent / "_internal" / "base_library.zip") as base:
    if any(Path(name).parts[0] in forbidden for name in base.namelist()):
        raise RuntimeError("The base library contains a testing/development package.")
print(json.dumps({"embedded_modules": len(modules), "prohibited_modules": 0}))
