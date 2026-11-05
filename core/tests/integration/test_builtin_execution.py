"""真实本地进程与文档文件测试。 / Real local processes and generated document files."""

import asyncio
import sys
from pathlib import Path

import pytest
from docx import Document

from app.agent.tools import create_builtin_tools
from app.config import ToolSettings
from app.infrastructure.processes import ProcessRunner
from app.tools.command import CommandExecutor
from app.tools.python_document import PythonDocumentExecutor


async def test_portable_commands_create_list_and_read_files(tmp_path: Path):
    executor = CommandExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    assert (await executor.run("mkdir", ["reports"]))["ok"]
    (tmp_path / "reports" / "readme.txt").write_text("你好", encoding="utf-8")
    assert (await executor.run("ls", ["reports"]))["output"] == "readme.txt"
    assert (await executor.run("cat", ["reports/readme.txt"]))["output"] == "你好"
    assert (await executor.run("pwd"))["output"] == str(tmp_path)


@pytest.mark.parametrize(
    "command", ["rm", "del", "rmdir", "Remove-Item", "cmd", "powershell", "python"]
)
async def test_deletion_and_interpreter_commands_are_unavailable(tmp_path: Path, command: str):
    executor = CommandExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    original = tmp_path / "keep.txt"
    original.write_text("keep", encoding="utf-8")
    with pytest.raises(ValueError, match="not allowed"):
        await executor.run(command, ["keep.txt"])
    assert original.read_text(encoding="utf-8") == "keep"


async def test_shell_metacharacters_are_literal_and_paths_are_bounded(tmp_path: Path):
    root = tmp_path / "workspace"
    executor = CommandExecutor(ToolSettings(workspace_path=root, _env_file=None))
    payload = "hello; Remove-Item keep.txt && echo bad"
    assert (await executor.run("echo", [payload]))["output"] == payload
    with pytest.raises(ValueError, match="outside"):
        await executor.run("cat", ["../outside.txt"])
    with pytest.raises(ValueError, match="private"):
        await executor.run("cat", [".env"])
    with pytest.raises(ValueError, match="cwd"):
        await executor.run("pwd", cwd="..")


async def test_native_rg_search_is_read_only_and_reports_no_matches(tmp_path: Path):
    executor = CommandExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    if executor.search_executable is None:
        pytest.skip("rg is not installed")
    (tmp_path / "data.txt").write_text("needle\n", encoding="utf-8")
    result = await executor.run("rg", ["needle", "data.txt"])
    assert result["ok"] and "needle" in result["output"]
    result = await executor.run("rg", ["not-found", "data.txt"])
    assert result["ok"] and result["matched"] is False


async def test_process_timeout_output_limit_and_secret_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    runner = ProcessRunner()
    timeout = await runner.run(
        [sys.executable, "-I", "-c", "import time; time.sleep(3)"],
        cwd=tmp_path,
        timeout_seconds=0.1,
        max_output_chars=256,
    )
    assert timeout["error"] == "TIMEOUT" and not timeout["ok"]
    excessive = await runner.run(
        [sys.executable, "-I", "-c", "print('x' * 1000000)"],
        cwd=tmp_path,
        timeout_seconds=3,
        max_output_chars=256,
    )
    assert excessive["error"] == "OUTPUT_LIMIT" and len(excessive["output"]) == 256
    monkeypatch.setenv("DEEPSEEK_API_KEY", "private-test-key")
    result = await runner.run(
        [sys.executable, "-I", "-c", "import os; print(os.getenv('DEEPSEEK_API_KEY', 'missing'))"],
        cwd=tmp_path,
        timeout_seconds=3,
        max_output_chars=256,
    )
    assert result["output"].strip() == "missing"


async def test_cancelling_execution_terminates_and_reaps_the_child(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    created = asyncio.Event()
    children = []
    original_create = asyncio.create_subprocess_exec

    async def capture_process(*args, **kwargs):
        child = await original_create(*args, **kwargs)
        children.append(child)
        created.set()
        return child

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture_process)
    task = asyncio.create_task(
        ProcessRunner().run(
            [sys.executable, "-I", "-c", "import time; time.sleep(30)"],
            cwd=tmp_path,
            timeout_seconds=60,
            max_output_chars=256,
        )
    )
    await asyncio.wait_for(created.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert children[0].returncode is not None


async def test_python_code_creates_real_word_and_text_documents(tmp_path: Path):
    executor = PythonDocumentExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    result = await executor.run("""from pathlib import Path
from decimal import Decimal
from docx import Document
document = Document()
document.add_heading("测试报告", 0)
document.add_paragraph(str(Decimal("0.10") + Decimal("0.20")))
document.save("report.docx")
Path("notes.md").write_text("# 文档说明", encoding="utf-8")
print("文档已生成")
""")
    assert result["ok"], result["output"]
    assert len(result["files"]) == 2
    assert "文档已生成" in result["output"]
    report = next(
        tmp_path / item["path"] for item in result["files"] if item["path"].endswith(".docx")
    )
    assert Document(report).paragraphs[1].text == "0.30"


@pytest.mark.parametrize(
    "code",
    [
        "import os\nos.remove('keep.txt')",
        "from pathlib import Path\nPath('keep.txt').unlink()",
        "from pathlib import Path\nPath(input_dir / 'keep.txt').write_text('changed')",
        "from pathlib import Path\nprint(Path(input_dir / '.env').read_text())",
        "import subprocess\nsubprocess.run(['echo', 'bad'])",
    ],
)
async def test_document_code_cannot_use_declared_forbidden_actions(tmp_path: Path, code: str):
    (tmp_path / "keep.txt").write_text("keep", encoding="utf-8")
    (tmp_path / ".env").write_text("private-test-value", encoding="utf-8")
    executor = PythonDocumentExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    result = await executor.run(code)
    assert not result["ok"]
    assert (tmp_path / "keep.txt").read_text(encoding="utf-8") == "keep"
    assert "private-test-value" not in result["output"]


async def test_document_input_templates_are_read_only_and_artifacts_are_new(tmp_path: Path):
    template = tmp_path / "template.docx"
    original = Document()
    original.add_paragraph("模板")
    original.save(template)
    executor = PythonDocumentExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    result = await executor.run("""from docx import Document
document = Document(input_dir / "template.docx")
document.add_paragraph("新增内容")
document.save("filled.docx")
""")
    assert result["ok"], result["output"]
    assert len(Document(template).paragraphs) == 1
    assert len(Document(tmp_path / result["files"][0]["path"]).paragraphs) == 2


async def test_document_timeout_and_artifact_budget_are_reported(tmp_path: Path):
    executor = PythonDocumentExecutor(
        ToolSettings(workspace_path=tmp_path, python_timeout_seconds=0.1, _env_file=None)
    )
    result = await executor.run("for i in range(1000000000000):\n    pass")
    assert not result["ok"] and result["error"] == "TIMEOUT"
    executor = PythonDocumentExecutor(
        ToolSettings(workspace_path=tmp_path, max_artifact_bytes=4, _env_file=None)
    )
    result = await executor.run("from pathlib import Path\nPath('big.txt').write_text('12345')")
    assert not result["ok"] and result["error"] == "ARTIFACT_LIMIT"


async def test_registered_tools_keep_float_and_string_results_and_command_schema(tmp_path: Path):
    tools = {
        tool.name: tool
        for tool in create_builtin_tools(ToolSettings(workspace_path=tmp_path, _env_file=None))
    }
    result = tools["calculate"].invoke({"operation": "add", "a": 1.5, "b": 2.0})
    assert type(result) is float and result == 3.5
    result = tools["calculate_financial"].invoke({"operation": "add", "a": "0.1", "b": "0.2"})
    assert type(result) is str and result == "0.30"
    result = await tools["run_command"].ainvoke({"command": "mkdir", "arguments": ["reports"]})
    assert result["ok"]
    rejected = await tools["run_command"].ainvoke({"command": "del", "arguments": ["reports"]})
    assert isinstance(rejected, str) and "pwd/ls/cat/mkdir/echo/rg" in rejected


@pytest.mark.parametrize(
    ("module", "code", "filename"),
    [
        (
            "openpyxl",
            "from openpyxl import Workbook\nbook = Workbook()\n"
            'book.active["A1"] = "0.30"\nbook.save("report.xlsx")',
            "report.xlsx",
        ),
        (
            "reportlab",
            'from reportlab.pdfgen import canvas\ndocument = canvas.Canvas("report.pdf")\n'
            'document.drawString(72, 720, "Amount: 0.30")\ndocument.save()',
            "report.pdf",
        ),
        (
            "pptx",
            "from pptx import Presentation\nslides = Presentation()\n"
            "slide = slides.slides.add_slide(slides.slide_layouts[0])\n"
            'slide.shapes.title.text = "Report"\nslides.save("report.pptx")',
            "report.pptx",
        ),
    ],
)
async def test_optional_document_formats_are_generated_by_python(
    tmp_path: Path,
    module: str,
    code: str,
    filename: str,
):
    library = pytest.importorskip(module)
    executor = PythonDocumentExecutor(ToolSettings(workspace_path=tmp_path, _env_file=None))
    result = await executor.run(code)
    assert result["ok"], result["output"]
    assert len(result["files"]) == 1
    path = tmp_path / result["files"][0]["path"]
    assert path.name == filename and path.stat().st_size > 100
    if module == "openpyxl":
        workbook = library.load_workbook(path)
        try:
            assert workbook.active["A1"].value == "0.30"
        finally:
            workbook.close()
    elif module == "pptx":
        assert library.Presentation(path).slides[0].shapes.title.text == "Report"
    else:
        content = path.read_bytes()
        assert content.startswith(b"%PDF-") and content.rstrip().endswith(b"%%EOF")
