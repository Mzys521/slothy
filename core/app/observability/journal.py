"""Local JSONL runtime journal for model-call observability. / 本地模型调用 JSONL 运行记录。"""

from __future__ import annotations

import asyncio
import json
import platform
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.agent.prompting.plan import estimate_tokens, tool_schema_tokens

SCHEMA_VERSION = 1
_SECRET_KEYS = {
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "password",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "id_token",
}
# Structured error fields are whitelisted; free-form text could copy prompts or credentials.
# 错误只保留结构化白名单字段；自由文本可能复制提示词或凭据。
_STATUS_FIELDS = ("status_code", "code", "error_code", "status")


def _is_secret_key(key: str) -> bool:
    """Only exact credential-shaped keys are redacted, not token counters. / 仅脱敏凭据字段。"""
    normalized = key.casefold().replace("-", "_")
    return normalized in _SECRET_KEYS or normalized.endswith("_api_key")


def _redact(value: Any) -> Any:
    """Recursively redact credential-shaped keys. / 递归脱敏凭据字段。"""
    if isinstance(value, dict):
        return {
            str(key): "[redacted]" if _is_secret_key(str(key)) else _redact(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def error_fields(error: BaseException) -> dict:
    """Keep only structured, bounded error fields. / 仅保留结构化且有界的错误字段。

    Exception messages and tracebacks can contain request text or credentials, so they are
    never persisted; the exception type and provider status stay diagnosable. / 异常消息与调用栈
    可能包含请求正文或凭据，因此不入库；异常类型与服务状态码仍可用于诊断。

    Args:
        error: Provider or tool exception. / 提供商或工具异常。

    Returns:
        Mapping with the exception type and any structured status code. / 含类型与状态码的映射。
    """
    fields: dict[str, str] = {"type": type(error).__name__}
    response = getattr(error, "response", None)
    candidates = {
        "status_code": getattr(response, "status_code", None)
        or getattr(error, "status_code", None),
        "code": getattr(error, "code", None),
        "error_code": getattr(error, "error_code", None),
        "status": getattr(error, "status", None),
    }
    for name in _STATUS_FIELDS:
        value = candidates.get(name)
        if isinstance(value, (int, str)) and not isinstance(value, bool):
            fields[name] = str(value)[:120]
    return fields


@dataclass
class JournalHealth:
    """Independent health counters for diagnostic persistence. / 诊断写入的独立健康计数。

    Attributes:
        enqueued: Records accepted for writing. / 已入队记录数。
        written: Records persisted. / 已写盘记录数。
        dropped: Records dropped because the queue was full or the session was cleared. /
            因队列满或会话已清理而丢弃的记录数。
        failed: Write attempts that raised. / 写入失败的次数。
    """

    enqueued: int = 0
    written: int = 0
    dropped: int = 0
    failed: int = 0

    @property
    def degraded(self) -> bool:
        """Whether diagnostics are not keeping up with the run. / 诊断是否已落后于运行。"""
        return self.dropped > 0 or self.failed > 0

    def as_dict(self) -> dict:
        """Return a JSON-safe health projection. / 返回可序列化的健康状态。"""
        return {
            "enqueued": self.enqueued,
            "written": self.written,
            "dropped": self.dropped,
            "failed": self.failed,
            "degraded": self.degraded,
        }


@dataclass
class _Entry:
    """One queued record with the session generation that produced it. / 待写记录及其会话世代。"""

    session_id: str
    generation: int
    record: dict = field(default_factory=dict)


class RuntimeJournal:
    """Append one JSON object per model call under logs/sessions/<session>/runtime.jsonl.

    Diagnostics must never decide whether a model answer succeeds: records are built and
    queued synchronously, a background task writes them, the queue is bounded and every
    failure is counted instead of raised. Files rotate by size and reads keep only the most
    recent records.
    / 诊断结果绝不能决定模型回答是否成功：记录同步构造并入队，后台任务负责写盘，队列有界，
    所有失败只计数不抛出。文件按大小轮转，读取只保留最近的记录。
    """

    def __init__(
        self,
        root: Path,
        *,
        application_version: str = "development",
        queue_size: int = 512,
        rotate_bytes: int = 4 * 1024 * 1024,
        rotate_keep: int = 2,
        max_read_bytes: int = 8 * 1024 * 1024,
    ):
        """Initialize the journal root and its bounded write queue. / 初始化日志根目录与有界写队列。

        Args:
            root: Log root directory. / 日志根目录。
            application_version: Version recorded in each event. / 每条事件记录的应用版本。
            queue_size: Maximum queued records before dropping. / 丢弃前的最多排队记录数。
            rotate_bytes: Session file size that triggers rotation. / 触发轮转的会话文件大小。
            rotate_keep: Rotated files kept per session. / 每个会话保留的轮转文件数。
            max_read_bytes: Maximum bytes read when tailing one file. / 读取单个文件尾部的上限。
        """
        self.root = Path(root)
        self.application_version = application_version
        self.queue_size = queue_size
        self.rotate_bytes = rotate_bytes
        self.rotate_keep = rotate_keep
        self.max_read_bytes = max_read_bytes
        self.health = JournalHealth()
        self._lock = asyncio.Lock()
        self._queue: asyncio.Queue[_Entry] | None = None
        self._task: asyncio.Task | None = None
        self._generations: dict[str, int] = {}
        self._accepted = 0

    def session_path(self, session_id: str) -> Path:
        """Return the session JSONL path, rejecting unsafe IDs. / 返回会话 JSONL 路径。"""
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_") or "unknown"
        return self.root / "sessions" / safe / "runtime.jsonl"

    async def start(self) -> RuntimeJournal:
        """Start the background writer for this process. / 启动本进程的后台写入任务。

        Returns:
            This journal, for chaining. / 自身，便于串联调用。
        """
        self._ensure_started()
        return self

    async def stop(self) -> None:
        """Stop the writer after a bounded drain; pending records are counted as dropped. /
        有界排空后停止写入器；未写入的记录计入丢弃。"""
        task, queue = self._task, self._queue
        self._task = None
        if task is None:
            return
        if queue is not None:
            try:
                async with asyncio.timeout(2):
                    await queue.join()
            except (TimeoutError, asyncio.CancelledError):
                pass
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    async def flush(self, timeout: float = 2.0) -> None:
        """Wait for queued records to be written, bounded by a timeout.
        / 等待队列写入，超时即有界返回。

        Args:
            timeout: Maximum wait in seconds. / 最大等待秒数。
        """
        queue = self._queue
        if queue is None or queue.empty():
            return
        try:
            async with asyncio.timeout(timeout):
                await queue.join()
        except (TimeoutError, asyncio.CancelledError):
            pass

    def _ensure_started(self) -> bool:
        """Start the writer task if a loop is running. / 若事件循环可用则启动写入任务。

        Returns:
            True when a writer task is running. / 写入任务可用时为 True。
        """
        if self._task is not None and not self._task.done():
            return True
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            return False
        self._queue = self._queue or asyncio.Queue(maxsize=self.queue_size)
        self._task = loop.create_task(self._drain(), name="runtime-journal")
        return True

    async def _drain(self) -> None:
        """Persist queued records until cancelled, counting every failure. /
        持续写入排队记录；所有失败仅计数。"""
        queue = self._queue
        if queue is None:
            return
        while True:
            entry = await queue.get()
            try:
                if self._generations.get(entry.session_id, 0) != entry.generation:
                    self.health.dropped += 1
                    continue
                await asyncio.to_thread(
                    self._append, self.session_path(entry.session_id), entry.record
                )
                self.health.written += 1
            except Exception:
                self.health.failed += 1
            finally:
                queue.task_done()

    def try_enqueue_model_call(
        self,
        *,
        session_id: str,
        run_id: str,
        step_id: str,
        model_call_id: str,
        provider: str,
        model: str,
        parameters: dict,
        context: dict,
        tools: dict,
        usage: dict | None,
        duration_ms: int,
        status: str,
        environment: dict | None = None,
        error: BaseException | None = None,
        response_status: str | None = None,
        compaction: dict | None = None,
    ) -> bool:
        """Queue one model-call event without blocking or raising.
        / 不阻塞、不抛错地排队一条调用事件。

        Args:
            session_id: Session that owns the call. / 调用所属会话。
            run_id: Run identifier. / 运行标识。
            step_id: Step identifier. / 步骤标识。
            model_call_id: Unique call identifier. / 调用标识。
            provider: Provider name. / 提供商名称。
            model: Model identifier. / 模型标识。
            parameters: Sampling parameters. / 采样参数。
            context: Estimated context categories. / 估算的上下文分类。
            tools: Tool exposure summary. / 工具暴露摘要。
            usage: Provider token usage, if any. / 提供商 token 用量。
            duration_ms: Call duration in milliseconds. / 毫秒耗时。
            status: success or failed. / success 或 failed。
            environment: Extra environment fields. / 附加环境字段。
            error: Original exception on failure. / 失败时的原始异常。
            response_status: Provider finish reason. / 提供商结束原因。
            compaction: History-compaction summary. / 历史压缩摘要。

        Returns:
            True when the record was queued. / 已入队时为 True。
        """
        try:
            if not self._ensure_started():
                self.health.dropped += 1
                return False
            session_id = str(session_id)
            record = self._record(
                session_id=session_id,
                run_id=run_id,
                step_id=step_id,
                model_call_id=model_call_id,
                provider=provider,
                model=model,
                parameters=parameters,
                context=context,
                tools=tools,
                usage=usage,
                duration_ms=duration_ms,
                status=status,
                environment=environment,
                error=error,
                response_status=response_status,
                compaction=compaction,
            )
            entry = _Entry(session_id, self._generations.get(session_id, 0), record)
            queue = self._queue
            if queue is None:
                self.health.dropped += 1
                return False
            queue.put_nowait(entry)
            self._accepted += 1
            self.health.enqueued = self._accepted
            return True
        except asyncio.QueueFull:
            self.health.dropped += 1
            return False
        except Exception:
            self.health.failed += 1
            return False

    def _record(self, *, session_id: str, **fields) -> dict:
        """Build one redacted, bounded record. / 构造一条已脱敏且有界的记录。

        Args:
            session_id: Session that owns the call. / 调用所属会话。
            **fields: Event fields described by ``try_enqueue_model_call``. / 事件字段。

        Returns:
            A JSON-ready record. / 可序列化的记录。
        """
        usage = fields.get("usage") or {}
        input_tokens = usage.get("input_tokens")
        cache_read = (usage.get("input_token_details") or {}).get("cache_read")
        record = {
            "event_type": "model.call",
            "session_id": session_id,
            "run_id": fields["run_id"],
            "step_id": fields["step_id"],
            "model_call_id": fields["model_call_id"],
            "provider": fields["provider"],
            "model": fields["model"],
            "environment": {
                "platform": platform.system().lower(),
                "application_version": self.application_version,
                **(fields.get("environment") or {}),
            },
            "parameters": fields["parameters"],
            "context": fields["context"],
            "tools": fields["tools"],
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cache_hit_tokens": cache_read,
                "reasoning_tokens": (usage.get("output_token_details") or {}).get("reasoning"),
                "source": "provider" if input_tokens is not None else "unknown",
            },
            "cache_hit_ratio": (
                cache_read / input_tokens if cache_read is not None and input_tokens else None
            ),
            "duration_ms": fields["duration_ms"],
            "status": fields["status"],
            "response_status": fields.get("response_status"),
            "compaction": fields.get("compaction") or {},
        }
        error = fields.get("error")
        if error is not None:
            record["error"] = error_fields(error)
        payload = _redact(record)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        return payload

    async def write(self, session_id: str, record: dict) -> None:
        """Persist one redacted record directly, for tests and maintenance tools. /
        直接写入一条脱敏记录，供测试与维护工具使用。

        Args:
            session_id: Session that owns the record. / 记录所属会话。
            record: JSON-serializable record. / 可序列化的记录。
        """
        payload = _redact(record)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        async with self._lock:
            await asyncio.to_thread(self._append, self.session_path(session_id), payload)

    async def delete_session(self, session_id: str) -> bool:
        """Drop queued records and remove one session's journal. / 丢弃待写记录并删除会话日志。

        The generation bump makes records already queued for that session obsolete, so a
        cleanup never races with an in-flight model call. / 世代递增使该会话已排队的记录失效，
        清理不会与在途模型调用竞态。

        Args:
            session_id: Session whose journal must be removed. / 需要删除日志的会话。

        Returns:
            True when a log directory or queued record was discarded.
            / 丢弃了日志目录或排队记录时为 True。
        """
        self._generations[session_id] = self._generations.get(session_id, 0) + 1
        directory = self.session_path(session_id).parent
        existed = directory.exists()
        try:
            if directory.exists():
                await asyncio.to_thread(shutil.rmtree, directory, True)
        except OSError:
            self.health.failed += 1
            return existed
        return existed

    def _append(self, path: Path, record: dict) -> None:
        """Append one complete line, rotating the file first when it is large.
        / 追加一行完整记录；文件过大时先轮转。

        Args:
            path: Session JSONL path. / 会话 JSONL 路径。
            record: JSON-ready record. / 可序列化记录。

        Raises:
            OSError: When the line cannot be written. / 无法写入时抛出。
        """
        path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(record, ensure_ascii=False, default=str) + "\n"
        try:
            if path.exists() and path.stat().st_size + len(line.encode()) > self.rotate_bytes:
                self._rotate(path)
        except OSError:
            # A failed rotation must not lose the record itself. / 轮转失败不得丢失记录本身。
            pass
        with path.open("a", encoding="utf-8") as stream:
            stream.write(line)

    def _rotate(self, path: Path) -> None:
        """Shift rotated session files and drop the oldest. / 依次滚动会话文件并丢弃最旧的。

        Args:
            path: Live session JSONL path. / 当前会话 JSONL 路径。
        """
        for index in range(self.rotate_keep, 0, -1):
            source = path if index == 1 else path.with_name(f"{path.name}.{index - 1}")
            target = path.with_name(f"{path.name}.{index}")
            if source.exists():
                Path(target).unlink(missing_ok=True)
                source.rename(target)

    async def read(self, session_id: str, *, limit: int = 500) -> list[dict]:
        """Read the most recent valid records, ignoring corrupt lines. / 读取最近的合法记录。

        Args:
            session_id: Session to read. / 待读取会话。
            limit: Maximum records returned. / 返回记录上限。

        Returns:
            The newest records in file order. / 按文件顺序的最新记录。
        """
        if limit < 1:
            return []
        return await asyncio.to_thread(self._read_sync, self.session_path(session_id), limit)

    def _read_sync(self, path: Path, limit: int) -> list[dict]:
        """Tail the session file plus its newest rotation in a worker thread. /
        在工作线程中读取会话文件及其最新轮转文件尾部。

        Args:
            path: Session JSONL path. / 会话 JSONL 路径。
            limit: Maximum records returned. / 返回记录上限。

        Returns:
            The newest valid records in file order. / 按文件顺序的最新合法记录。
        """
        records: deque[dict] = deque(maxlen=limit)
        for candidate in (path.with_name(f"{path.name}.1"), path):
            if not candidate.exists():
                continue
            self._tail_records(candidate, records, limit)
        return list(records)

    def _tail_records(self, path: Path, records: deque, limit: int) -> None:
        """Fill a bounded deque from the tail of one JSONL file. / 从一个文件尾部填充有界队列。

        Args:
            path: JSONL file to read. / 待读取文件。
            records: Bounded destination deque. / 有界目标队列。
            limit: Records still wanted. / 期望记录数。
        """
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size <= 0:
            return
        block = 64 * 1024
        position, read_bytes, blocks = size, 0, []
        with path.open("rb") as stream:
            while position > 0 and read_bytes < self.max_read_bytes:
                length = min(block, position)
                position -= length
                stream.seek(position)
                chunk = stream.read(length)
                blocks.append(chunk)
                read_bytes += length
                if b"".join(reversed(blocks)).count(b"\n") > limit + 1:
                    break
        text = b"".join(reversed(blocks)).decode("utf-8", "replace")
        lines = text.split("\n")
        if position > 0:
            # The first line is partial when the tail started mid-line.
            # 从行中截断时首行不完整。
            lines = lines[1:]
        for line in lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except (ValueError, TypeError):
                continue
            if isinstance(item, dict):
                records.append(item)


def context_breakdown(
    system_prompt: str, messages: list, personalization: str, tool_names: list[str]
) -> dict:
    """Split context into mutually exclusive estimated categories. / 将上下文拆成互斥的估算分类。

    Categories: system, runtime, history, user, memory, tool_schema. The current user
    message is excluded from history and included only in ``user``.
    / 分类为 system/runtime/history/user/memory/tool_schema；当前用户消息只计入 user。
    """
    from langchain_core.messages import HumanMessage, SystemMessage

    reference_names = {
        "runtime_context", "explicit_reference", "skill_reference", "attachment_reference",
        "personalization_reference", "profile_snapshot", "conversation_summary",
    }
    system = 0
    history = user = runtime = memory = 0
    last_user_index = max(
        (i for i, item in enumerate(messages)
         if isinstance(item, HumanMessage) and item.name not in reference_names), default=-1
    )
    for index, item in enumerate(messages):
        text = (
            item.content
            if isinstance(item.content, str)
            else json.dumps(item.content, default=str)
        )
        tokens = estimate_tokens(text)
        if isinstance(item, SystemMessage):
            system += tokens
        elif isinstance(item, HumanMessage) and index == last_user_index:
            user += tokens
        elif item.name in {"personalization_reference", "profile_snapshot"}:
            memory += tokens
        elif item.name in {"runtime_context", "explicit_reference", "skill_reference",
                           "attachment_reference"}:
            runtime += tokens
        else:
            history += tokens
    if not any(isinstance(item, SystemMessage) for item in messages):
        system = estimate_tokens(system_prompt)
    tool_schema = tool_schema_tokens(tool_names)
    categories = {
        "system": system,
        "runtime": runtime,
        "history": history,
        "user": user,
        "memory": memory,
        "tool_schema": tool_schema,
    }
    categories["other"] = 0
    categories["total"] = sum(categories.values())
    return {**categories, "estimated": True}
