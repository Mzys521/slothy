# Build-only specification. Neither this file nor the builder is distributed.
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

root = Path(SPECPATH).parent
datas = []
for package in ('langchain_core', 'langchain_openai', 'langchain_anthropic', 'langgraph',
                'langgraph_checkpoint', 'langgraph_checkpoint_sqlite', 'langsmith', 'openai',
                'anthropic', 'pydantic', 'uvicorn', 'fastapi', 'python-docx', 'tiktoken', 'mcp',
                'jsonschema', 'referencing', 'jsonschema-specifications'):
    datas += copy_metadata(package)
for package in ('certifi', 'docx', 'openpyxl', 'reportlab', 'pptx', 'tiktoken_ext'):
    datas += collect_data_files(package, include_py_files=False)

hiddenimports = ['app.tools.python_worker', 'uvicorn.logging', 'uvicorn.loops.asyncio',
                 'uvicorn.protocols.http.h11_impl', 'uvicorn.lifespan.on', 'sqlite3',
                 'docx', 'openpyxl', 'reportlab.pdfgen.canvas', 'pptx', 'tiktoken_ext.openai_public']
hiddenimports += collect_submodules('langgraph', filter=lambda name: '.test' not in name)
datas += collect_data_files('jsonschema_specifications', include_py_files=False)

analysis = Analysis(
    [str(root / 'desktop' / 'entry.py')], pathex=[str(root)], binaries=[], datas=datas,
    hiddenimports=hiddenimports,
    excludes=['pytest', '_pytest', 'pytest_asyncio', 'ruff', 'pip', 'ensurepip', 'setuptools',
              'wheel', 'PyInstaller', 'tests', 'unittest', 'tkinter', 'idlelib', 'pdb', 'bdb',
              'IPython', 'jupyter', 'matplotlib', 'numpy', 'pandas', 'pygments', 'doctest',
              'langsmith.testing', 'lxml.doctestcompare', 'lxml.html.usedoctest',
              'lxml.usedoctest', 'reportlab.graphics.testshapes'],
    noarchive=False, optimize=1,
)
# Third-party hooks can collect their own test/fixture files. They never belong in production.
def production_file(row):
    parts = Path(row[0]).parts
    return not any(part.lower() in {'tests', 'test', 'testing', '__pycache__', 'fixtures'} for part in parts)
analysis.datas = [row for row in analysis.datas if production_file(row)]
analysis.binaries = [row for row in analysis.binaries if production_file(row)]
analysis.pure = [row for row in analysis.pure if not any(
    part in {'tests', 'test', 'fixtures'} or part.startswith('fake')
    for part in row[0].split('.')
)]
pyz = PYZ(analysis.pure)
exe = EXE(pyz, analysis.scripts, [], exclude_binaries=True, name='slothy-service',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=True, disable_windowed_traceback=False,
          icon=str(root.parent / 'web' / 'electron' / 'assets' / 'icon.ico'))
coll = COLLECT(exe, analysis.binaries, analysis.datas, strip=False, upx=False, name='slothy-service')
