"""执行 Python 生成文档文件。 / Generate document files by executing Python code."""

import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import ToolSettings
from app.infrastructure.processes import ProcessRunner


class PythonDocumentExecutor:
    """在独立进程和独立产物目录执行代码。 / Separate Python process and output directory.

    Args:
        settings: 工作目录、代码、时间和产物限制。 / Workspace, source, time and artifact limits.
        process_runner: 可替换进程执行器。 / Injectable process executor.

    中文：用于本地可信文档代码，不把 Python 审计策略声称为 OS 安全沙箱。
    English: For locally trusted document code; Python audit policies are not an OS sandbox.
    """

    def __init__(self, settings: ToolSettings, process_runner: ProcessRunner | None = None) -> None:
        self.settings = settings
        self.root = settings.workspace_path
        self.artifacts = settings.artifacts_path or self.root
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_runner = process_runner if process_runner is not None else ProcessRunner()

    async def run(self, code: str) -> dict[str, Any]:
        """生成文件并返回路径，不提供每种格式的专用写入 API。 / Execute code and return artifacts.

        Args:
            code: Python 源码；相对输出路径写入本次文件目录，input_dir 指向工具输入工作区。
                Python source; relative paths write this run's files; input_dir reads inputs.
        """
        if (
            not isinstance(code, str)
            or not code.strip()
            or len(code) > self.settings.max_code_chars
        ):
            raise ValueError("Python 代码为空或超长 / Python source is empty or too long")
        run_id = uuid4().hex
        run_dir = self.artifacts / "runs" / run_id
        output_dir, temp_dir = run_dir / "files", run_dir / "temp"
        output_dir.mkdir(parents=True)
        temp_dir.mkdir()
        source = run_dir / "program.py"
        source.write_text(code, encoding="utf-8")
        worker = Path(__file__).with_name("python_worker.py")
        executable = (
            [sys.executable, "--document-worker"]
            if getattr(sys, "frozen", False)
            else [sys.executable, "-I", "-B", "-u", "-X", "utf8", str(worker)]
        )
        result = await self.process_runner.run(
            [
                *executable,
                str(source),
                str(output_dir),
                str(temp_dir),
                str(self.root),
            ],
            cwd=output_dir,
            timeout_seconds=self.settings.python_timeout_seconds,
            max_output_chars=self.settings.max_output_chars,
            environment={"TMP": str(temp_dir), "TEMP": str(temp_dir), "TMPDIR": str(temp_dir)},
        )
        files = []
        total_bytes = 0
        for path in output_dir.rglob("*"):
            if not path.is_file():
                continue
            resolved = path.resolve()
            if not resolved.is_relative_to(output_dir) or path.is_symlink():
                result.update(ok=False, error="ARTIFACT_PATH_REJECTED")
                break
            size = path.stat().st_size
            total_bytes += size
            if (
                len(files) >= self.settings.max_artifact_files
                or total_bytes > self.settings.max_artifact_bytes
            ):
                result.update(ok=False, error="ARTIFACT_LIMIT")
                break
            files.append({"path": path.relative_to(self.root).as_posix(), "bytes": size})
        if result["ok"] and not files:
            result.update(ok=False, error="NO_DOCUMENT_FILES")
        return {**result, "run_id": run_id, "files": files}
