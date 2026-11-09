"""Local JSONL runtime journal for model-call observability. / 本地模型调用 JSONL 运行记录。"""

from __future__ import annotations

import asyncio
import json
import platform
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

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


def _is_secret_key(key: str) -> bool:
    """Only exact credential-shaped keys are redacted, not token counters. / 仅脱敏凭据字段。"""
    normalized = key.casefold().replace("-", "_")
    return normalized in _SECRET_KEYS or normalized.endswith("_api_key")


def estimate_tokens(text: str | None) -> int:
    """Estimate tokens with a stable local heuristic. / 使用稳定的本地启发式估算 Token。"""
    return max(1, len(text or "") // 4)


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


class RuntimeJournal:
    """Append one JSON object per model call under logs/sessions/<session>/runtime.jsonl.

    Writes are serialized with an async lock and pushed off the event loop with
    ``asyncio.to_thread``; a single malformed line never invalidates other lines.
    / 每次模型调用追加一行 JSON；异步锁串行化并使用线程写入，单行损坏不影响其他记录。
    """

    def __init__(self, root: Path, *, application_version: str = "0.1.4.1"):
        """Initialize the journal root. / 初始化日志根目录。"""
        self.root = Path(root)
        self.application_version = application_version
        self._lock = asyncio.Lock()

    def session_path(self, session_id: str) -> Path:
        """Return the session JSONL path, rejecting unsafe IDs. / 返回会话 JSONL 路径。"""
        safe = "".join(ch for ch in session_id if ch.isalnum() or ch in "-_") or "unknown"
        return self.root / "sessions" / safe / "runtime.jsonl"

    def _append(self, path: Path, record: dict) -> None:
        """Append one complete line with UTF-8 encoding. / 追加一行完整 UTF-8 记录。"""
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")

    async def write(self, session_id: str, record: dict) -> None:
        """Persist one redacted record without blocking the graph. / 非阻塞写入脱敏记录。"""
        payload = _redact(record)
        payload.setdefault("schema_version", SCHEMA_VERSION)
        payload.setdefault("timestamp", datetime.now(UTC).isoformat())
        async with self._lock:
            await asyncio.to_thread(self._append, self.session_path(session_id), payload)

    async def model_call(
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
    ) -> None:
        """Record one prepared/dispatched/completed model call. / 记录一次模型调用。"""
        usage = usage or {}
        input_tokens = usage.get("input_tokens")
        cache_read = (usage.get("input_token_details") or {}).get("cache_read")
        record = {
            "event_type": "model.call",
            "session_id": session_id,
            "run_id": run_id,
            "step_id": step_id,
            "model_call_id": model_call_id,
            "provider": provider,
            "model": model,
            "environment": {
                "platform": platform.system().lower(),
                "application_version": self.application_version,
                **(environment or {}),
            },
            "parameters": parameters,
            "context": context,
            "tools": tools,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": usage.get("output_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "cache_hit_tokens": cache_read,
                "reasoning_tokens": (usage.get("output_token_details") or {}).get("reasoning"),
                "source": "provider" if input_tokens is not None else "unknown",
            },
            "cache_hit_ratio": (
                cache_read / input_tokens
                if cache_read is not None and input_tokens
                else None
            ),
            "duration_ms": duration_ms,
            "status": status,
            "response_status": response_status,
            "compaction": compaction or {},
        }
        if error is not None:
            record["error"] = {
                "type": type(error).__name__,
                "message": str(error)[:1000],
                "traceback": "".join(traceback.format_exception(error))[-6000:],
            }
        await self.write(session_id, record)

    async def read(self, session_id: str, *, limit: int = 500) -> list[dict]:
        """Read valid JSONL records, ignoring corrupt lines. / 读取有效记录并忽略损坏行。"""
        path = self.session_path(session_id)
        if not path.exists():
            return []
        records: list[dict] = []
        await asyncio.to_thread(self._read_sync, path, records)
        return records[-limit:]

    @staticmethod
    def _read_sync(path: Path, records: list[dict]) -> None:
        """Read lines synchronously in a worker thread. / 在工作线程同步读取。"""
        with path.open("r", encoding="utf-8") as stream:
            for line in stream:
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
    tool_schema = sum(estimate_tokens(name) for name in tool_names) * 8
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
