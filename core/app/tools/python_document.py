"""Generate document files by executing Python code in a separate process. /
在独立进程中执行 Python 代码生成文档文件。"""

import re
import sys
from pathlib import Path
from typing import Any
from uuid import uuid4

from app.config import ToolSettings
from app.infrastructure.processes import ProcessRunner


class PythonDocumentExecutor:
    """
    Execute document code in a separate process and output directory. /
    在独立进程与独立产物目录中执行文档代码。

    Args:
        settings: Workspace, source, timeout and artifact limits. /
        工作区、源码、超时与产物限制。
        process_runner: Optional injectable process executor. / 可选的可替换进程执行器。

    Code runs with Python audit policies for locally trusted document tasks; this
    is not an OS-level sandbox. / 代码对本地可信文档任务运行并启用 Python
    审计策略；这不是操作系统级沙箱。
    """

    def __init__(self, settings: ToolSettings, process_runner: ProcessRunner | None = None) -> None:
        """Configure the separate document-code process. / 配置独立的文档代码进程。

        Args:
            settings: Workspace, artifact root, code cap, timeout and output limits. /
                工作区、产物根目录、源码上限、超时与输出限制。
            process_runner: Optional injectable process executor. / 可选的可替换进程执行器。
        """
        self.settings = settings
        self.root = settings.workspace_path
        self.artifacts = settings.artifacts_path or self.root
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_runner = process_runner if process_runner is not None else ProcessRunner()

    async def run(self, code: str, *, run_id: str | None = None) -> dict[str, Any]:
        """
        Run generated code and collect its bounded artifacts. / 执行生成代码并收集有界产物。

        Args:
            code: Python source. Relative outputs write run files; input_dir is read-only. /
                Python 源码；相对输出写入本次文件目录，input_dir 只读。
            run_id: Optional 64-character journal identity reused for this run. /
            可选的本次运行 64 位日志标识。

        Returns:
            Process mapping with run_id, file list and status fields. /
                含 run_id、文件列表与状态字段的进程结果映射。

        Raises:
            ValueError: If the source is empty, over the cap or run_id is invalid. /
                源码为空、超长或 run_id 非法时抛出。
        """
        if (
            not isinstance(code, str)
            or not code.strip()
            or len(code) > self.settings.max_code_chars
        ):
            raise ValueError("Python 代码为空或超长 / Python source is empty or too long")
        if run_id is not None and not re.fullmatch(r"[0-9a-f]{64}", run_id):
            raise ValueError("Invalid internal artifact identity")
        run_id = run_id or uuid4().hex
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
