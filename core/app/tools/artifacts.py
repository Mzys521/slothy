"""Crash-aware invocation journals; uncertain file effects are never replayed. /
崩溃感知的调用日志；不确定的文件副作用绝不重放。"""

import hashlib
import json
import os
import re
import shutil
import tempfile
from collections.abc import Awaitable, Callable
from pathlib import Path
from time import perf_counter, time

from app.config import ToolSettings
from app.tools.context import ToolExecutionContext
from app.tools.contracts import Artifact, ToolOperationError, ToolResult

MARKER = "slothy-tool-layer-v2"
KEY = re.compile(r"^[0-9a-f]{64}$")


def invocation_key(state: dict, call: dict) -> str:
    """
    Build the stable identity of one tool invocation. / 生成单次工具调用的稳定标识。

    Args:
        state: Graph state supplying user, thread, run and messages. /
        提供用户、会话、运行与消息的图状态。
        call: Tool call mapping with id, name and args. / 包含 id、名称与参数的模型工具调用。

    Returns:
        SHA-256 hex digest of the normalized identity. / 规范化标识的 SHA-256 摘要。
    """
    messages = state.get("messages", [])
    start = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if getattr(messages[index], "type", None) == "human"
            and messages[index].id == f"{state.get('run_id')}:user"
        ),
        0,
    )
    turn = sum(getattr(item, "type", None) == "ai" for item in messages[start:])
    value = [
        state.get("user_id"),
        state.get("thread_id"),
        state.get("run_id"),
        turn,
        call.get("id"),
        call["name"],
        call.get("args", {}),
    ]
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class ArtifactStore:
    """
    Per-invocation artifact journals and validated artifact lookups. /
    每次调用的产物日志与经过校验的产物查询。

    Journals live under the artifact root, stay inside the workspace and are never
    re-executed once completed; pruning keeps only the newest runs. /
    日志位于产物根目录且不离开工作区；完成后不会重放，清理时仅保留最近的运行。
    """

    def __init__(self, settings: ToolSettings):
        """
        Bind artifact storage to the tool settings. / 将产物存储绑定到工具配置。

        Args:
            settings: Workspace, artifact root, size caps and retained runs. /
                工作区、产物根目录、大小上限与保留运行数。

        Raises:
            ValueError: If the artifact root is not inside the tool workspace. /
            产物根目录不在工具工作区内时抛出。
        """
        self.settings = settings
        self.workspace = settings.workspace_path.resolve()
        self.root = (settings.artifacts_path or self.workspace).resolve() / "runs"
        if not self.root.is_relative_to(self.workspace):
            raise ValueError("Artifact storage must be inside its workspace")

    def directory(self, key: str) -> Path:
        """
        Return the journal directory for one invocation key. /
        返回单次调用标识对应的日志目录。

        Args:
            key: 64-character lowercase hexadecimal invocation identity. / 64
            位小写十六进制调用标识。

        Returns:
            Journal directory path inside the artifact root. / 产物根目录内的日志目录路径。

        Raises:
            ValueError: If the key is not a valid journal identity. /
            标识不是合法日志身份时抛出。
            ToolOperationError: If the journal path or root is a linked path. /
            日志路径或根目录为链接路径时抛出。
        """
        if not KEY.fullmatch(key):
            raise ValueError("Invalid journal identity")
        path = self.root / key
        if path.resolve() != path.absolute() or not path.resolve().is_relative_to(
            self.root.resolve()
        ):
            raise ToolOperationError("OUT_OF_WORKSPACE", "Artifact journal path is linked.")
        if self.root.resolve() != self.root.absolute():
            raise ToolOperationError("OUT_OF_WORKSPACE", "Artifact root is linked.")
        return path

    def validate_artifact(self, artifact: Artifact) -> Path:
        """Validate a recorded artifact before reusing it. / 复用前校验已记录的产物。

        Args:
            artifact: Recorded artifact metadata including size and optional digest. /
                含大小与可选摘要的已记录产物元数据。

        Returns:
            Absolute path of the validated artifact. / 校验通过的产物绝对路径。

        Raises:
            ToolOperationError: If the file is missing, changed, linked or over the limit. /
                文件缺失、变化、为链接或超限时抛出。
        """
        path = self.workspace / artifact.rel_path
        if (
            not path.resolve().is_relative_to(self.workspace)
            or path.resolve() != path.absolute()
            or not path.is_file()
            or path.stat().st_size != artifact.bytes
            or artifact.bytes > self.settings.max_artifact_bytes
        ):
            raise ToolOperationError(
                "RECOVERY_REQUIRED", "A recorded artifact is missing or changed."
            )
        if artifact.sha256:
            with path.open("rb") as stream:
                data = stream.read(self.settings.max_artifact_bytes + 1)
            if hashlib.sha256(data).hexdigest() != artifact.sha256:
                raise ToolOperationError(
                    "RECOVERY_REQUIRED", "A recorded artifact is missing or changed."
                )
        return path

    @staticmethod
    def write(path: Path, record: dict):
        """
        Atomically replace a journal manifest with durable contents. /
        以持久化内容原子替换日志清单。

        Args:
            path: Manifest path to replace. / 需要替换的清单路径。
            record: JSON-serializable journal record. / 可 JSON 序列化的日志记录。
        """
        descriptor, temporary = tempfile.mkstemp(prefix="manifest-", suffix=".tmp", dir=path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(record, stream, ensure_ascii=False, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    async def execute(
        self,
        context: ToolExecutionContext,
        tool: str,
        invoke: Callable[[], Awaitable[ToolResult]],
        *,
        version="1",
    ) -> tuple[ToolResult, bool, int]:
        """
        Run one side-effecting invocation behind its journal. /
        在日志保护下执行一次带副作用的调用。

        Args:
            context: Trusted execution identity for this invocation. /
            本次调用的可信执行身份。
            tool: Registered tool name recorded in the journal. / 写入日志的已注册工具名称。
            invoke: Coroutine performing the actual tool work. / 执行实际工具工作的协程。

        Returns:
            Tuple of result, whether it was reused, and duration in ms. /
            结果、是否复用及毫秒耗时的元组。

        Raises:
            ToolOperationError: If a previous execution is unfinished or needs recovery. /
                上次执行未完成或需要恢复时抛出。
        """
        directory = self.directory(context.key)
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "manifest.json"
        identity = {
            "managed_by": MARKER,
            "key": context.key,
            "user_id": context.user_id,
            "thread_id": context.thread_id,
            "run_id": context.run_id,
            "tool": tool,
            "tool_version": version,
        }
        if path.exists():
            try:
                if path.stat().st_size > 1_000_000 or path.resolve() != path.absolute():
                    raise ValueError("Invalid journal")
                record = json.loads(path.read_text(encoding="utf-8"))
                if any(record.get(key) != value for key, value in identity.items()):
                    raise ValueError("Journal identity mismatch")
                if record.get("status") not in {"completed", "failed"} or not record.get("result"):
                    raise ValueError("Uncertain execution")
                result = ToolResult.model_validate(record["result"])
                for artifact in result.artifacts:
                    self.validate_artifact(artifact)
                if isinstance(result.data, dict) and result.data.get("kind") == "directory":
                    effect = self.workspace / result.data["path"]
                    if (
                        not effect.resolve().is_relative_to(self.workspace)
                        or effect.resolve() != effect.absolute()
                        or not effect.is_dir()
                    ):
                        raise ValueError("The recorded directory is missing or linked")
                return result, True, int(record.get("duration_ms", 0))
            except (ValueError, OSError) as error:
                raise ToolOperationError(
                    "RECOVERY_REQUIRED",
                    "A previous execution needs inspection before retry.",
                    hint="Inspect this run's artifacts; start a new approved request to rerun.",
                ) from error
        record = {**identity, "status": "running", "created_at": time()}
        try:
            # English: Exclusive creation protects callers outside the HTTP thread lock as well.
            # 中文：独占创建同样保护不经过 HTTP 线程锁的调用方。
            with path.open("x", encoding="utf-8") as stream:
                json.dump(record, stream)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError as error:
            raise ToolOperationError(
                "RECOVERY_REQUIRED", "This execution is already in progress."
            ) from error
        started = perf_counter()
        try:
            result = await invoke()
        except BaseException:
            self.write(path, {**record, "status": "interrupted"})
            raise
        elapsed = round((perf_counter() - started) * 1000)
        self.write(
            path,
            {
                **record,
                "status": "completed" if result.ok else "failed",
                "result": result.model_dump(),
                "duration_ms": elapsed,
            },
        )
        try:
            self.prune(exclude=context.key)
        except OSError:
            pass  # Retention cleanup must not turn a committed execution into failure.
        return result, False, elapsed

    def prune(self, *, exclude: str):
        """
        Delete the oldest completed journals beyond the retention limit. /
        删除超出保留上限的最旧已完成日志。

        Args:
            exclude: Journal key kept even when it is the oldest. /
            即使最旧也必须保留的日志标识。
        """
        if not self.root.is_dir():
            return
        completed = []
        for directory in self.root.iterdir():
            if not KEY.fullmatch(directory.name) or directory.name == exclude:
                continue
            try:
                directory = self.directory(directory.name)
                path = directory / "manifest.json"
                if path.resolve() != path.absolute() or path.stat().st_size > 1_000_000:
                    continue
                record = json.loads(path.read_text(encoding="utf-8"))
                if (
                    record.get("managed_by") == MARKER
                    and record.get("key") == directory.name
                    and record.get("status") in {"completed", "failed"}
                ):
                    completed.append((record.get("created_at", 0), directory))
            except (ValueError, OSError):
                continue
        completed.sort(key=lambda item: item[0], reverse=True)
        for _, directory in completed[max(0, self.settings.max_artifact_runs - 1) :]:
            # English: Only our own completed directories under the checked artifact root.
            # 中文：只清理已校验产物根目录下由本应用创建的已完成目录。
            checked = self.directory(directory.name)
            if checked.is_dir() and checked.resolve().parent == self.root.resolve():
                shutil.rmtree(checked)

    def lookup(self, artifact_id: str, user: str, thread: str) -> tuple[Artifact, Path]:
        """
        Find an artifact produced for one conversation. / 查找某个会话产出的产物。

        Args:
            artifact_id: Artifact identifier from a recorded result. /
            已记录结果中的产物标识。
            user: Trusted user that must own the journal. / 必须拥有该日志的可信用户。
            thread: Conversation that must own the journal. / 必须拥有该日志的会话。

        Returns:
            Tuple of artifact metadata and its validated path. /
            产物元数据与其校验后路径的元组。

        Raises:
            LookupError: If no matching artifact exists in this conversation. /
            此会话没有匹配产物时抛出。
        """
        if self.root.is_dir() and KEY.fullmatch(artifact_id):
            for directory in self.root.iterdir():
                if not KEY.fullmatch(directory.name):
                    continue
                try:
                    path = self.directory(directory.name) / "manifest.json"
                    if path.resolve() != path.absolute() or path.stat().st_size > 1_000_000:
                        continue
                    record = json.loads(path.read_text(encoding="utf-8"))
                    if (
                        record.get("managed_by") != MARKER
                        or record.get("user_id") != user
                        or record.get("thread_id") != thread
                        or record.get("status") != "completed"
                    ):
                        continue
                    for artifact in ToolResult.model_validate(record["result"]).artifacts:
                        if artifact.id == artifact_id:
                            return artifact, self.validate_artifact(artifact)
                except (ValueError, KeyError, OSError):
                    continue
        raise LookupError("Artifact is unavailable in this conversation.")
