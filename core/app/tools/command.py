"""结构化基础命令，不提供删除。 / Basic structured commands without deletion."""

import shutil
from itertools import islice
from pathlib import Path
from typing import Any, Literal

from app.config import ToolSettings
from app.infrastructure.processes import ProcessRunner


class CommandExecutor:
    """提供可移植文件命令与只读 rg。 / Portable filesystem commands and read-only rg.

    Args:
        settings: 工作区、超时和输出上限。 / Workspace, timeout and output limits.
        process_runner: 可替换的外部进程执行器。 / Injectable external-process execution.
    """

    def __init__(self, settings: ToolSettings, process_runner: ProcessRunner | None = None) -> None:
        self.settings = settings
        self.root = settings.workspace_path
        self.root.mkdir(parents=True, exist_ok=True)
        self.process_runner = process_runner if process_runner is not None else ProcessRunner()
        executable = shutil.which("rg")
        self.search_executable = str(Path(executable).resolve()) if executable else None
        if self.search_executable and Path(self.search_executable).suffix.lower() in (
            ".bat",
            ".cmd",
            ".ps1",
        ):
            self.search_executable = None

    async def run(
        self,
        command: Literal["pwd", "ls", "cat", "mkdir", "echo", "rg"],
        arguments: list[str] | None = None,
        cwd: str = ".",
    ) -> dict[str, Any]:
        """白名单命令使用字面参数。 / Execute allowed commands with literal arguments.

        Args:
            command: pwd/ls/cat/mkdir/echo/rg。 / Allowed name; no deletion or interpreters.
            arguments: 字面参数列表；rg 为 [pattern, optional_path]。 / Literal argument list.
            cwd: 相对工具工作区的目录。 / Directory inside the tool workspace.
        """
        if command not in {"pwd", "ls", "cat", "mkdir", "echo", "rg"}:
            raise ValueError("命令不在白名单中 / command is not allowed")
        args = [] if arguments is None else arguments
        if len(args) > 20 or any(
            not isinstance(arg, str) or len(arg) > 4096 or "\0" in arg for arg in args
        ):
            raise ValueError("无效命令参数 / invalid command arguments")
        directory = (self.root / cwd).resolve()
        if not directory.is_relative_to(self.root) or not directory.is_dir():
            raise ValueError("工作目录必须在工具工作区内 / cwd must be inside the tool workspace")
        if command == "pwd":
            if args:
                raise ValueError("pwd 不接受参数 / pwd accepts no arguments")
            text = str(directory)
        elif command == "echo":
            text = " ".join(args)
        else:
            if command == "rg":
                if len(args) not in (1, 2) or len(args[0]) > 1024:
                    raise ValueError(
                        "rg 参数为 pattern 与可选 path / rg requires pattern and optional path"
                    )
                target = (directory / (args[1] if len(args) == 2 else ".")).resolve()
            else:
                if len(args) > 1 or (command in ("cat", "mkdir") and len(args) != 1):
                    raise ValueError("命令需要一个路径参数 / command requires a single path")
                target = (directory / (args[0] if args else ".")).resolve()
            if not target.is_relative_to(self.root):
                raise ValueError("路径越出工具工作区 / path is outside the tool workspace")
            parts = target.relative_to(self.root).parts
            if any(
                part.lower() in {".git", ".aws", ".codex"}
                or (part.lower().startswith(".env") and part.lower() != ".env.example")
                for part in parts
            ):
                raise ValueError(
                    "不读取私密配置路径 / private configuration paths are not readable"
                )
            if command == "mkdir":
                target.mkdir(parents=True, exist_ok=True)
                text = str(target.relative_to(self.root))
            elif command == "ls":
                if not target.is_dir():
                    raise ValueError("ls 需要目录 / ls requires a directory")
                entries = list(islice(target.iterdir(), 1001))
                text = "\n".join(sorted(entry.name for entry in entries[:1000]))
                if len(entries) > 1000:
                    text += "\n[目录条目已截断 / directory entries truncated]"
            elif command == "cat":
                if not target.is_file():
                    raise ValueError("cat 需要文本文件 / cat requires a text file")
                try:
                    with target.open(encoding="utf-8") as stream:
                        text = stream.read(self.settings.max_output_chars + 1)
                except UnicodeError as exc:
                    raise ValueError("cat 仅支持 UTF-8 文本 / cat supports UTF-8 text") from exc
            else:
                if self.search_executable is None:
                    raise ValueError("未找到可用的 rg 程序 / rg executable is not available")
                # 中文：固定选项，-- 后的模式与路径不可能成为额外选项或 Shell。
                # English: Fixed options; pattern/path after -- cannot become flags or shell syntax.
                result = await self.process_runner.run(
                    [
                        self.search_executable,
                        "--line-number",
                        "--no-heading",
                        "--color=never",
                        "--max-count=200",
                        "--max-columns=2000",
                        "--",
                        args[0],
                        str(target),
                    ],
                    cwd=directory,
                    timeout_seconds=self.settings.command_timeout_seconds,
                    max_output_chars=self.settings.max_output_chars,
                )
                if result["return_code"] == 1 and result["error"] is None:
                    result["ok"] = True
                    result["matched"] = False
                return result
        truncated = len(text) > self.settings.max_output_chars
        return {
            "ok": not truncated,
            "return_code": 0,
            "output": text[: self.settings.max_output_chars],
            "error": "OUTPUT_LIMIT" if truncated else None,
        }
