"""Public execution activity derived from the current run's graph messages."""

import json
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langgraph.config import get_stream_writer
from pydantic import BaseModel


class RunActivity(BaseModel):
    id: str
    kind: Literal["prepare", "context", "model", "tool"]
    status: Literal["running", "done", "requested", "approval", "error", "rejected"]
    name: str | None = None
    arguments: str | None = None
    result: str | None = None
    memory_count: int | None = None
    approved: bool = False


def preview(value: object, limit: int) -> str:
    def redact(item):
        if isinstance(item, dict):
            return {
                key: "[redacted]" if any(
                    word in key.lower().replace("-", "_")
                    for word in (
                        "password", "token", "api_key", "apikey", "secret",
                        "credential", "authorization", "cookie",
                    )
                ) else redact(child)
                for key, child in item.items()
            }
        if isinstance(item, list):
            return [redact(child) for child in item]
        return item

    if isinstance(value, str):
        try:
            structured = json.loads(value)
        except ValueError:
            structured = None
        text = value if not isinstance(structured, (dict, list)) else json.dumps(
            redact(structured), ensure_ascii=False, indent=2
        )
    else:
        text = json.dumps(redact(value), ensure_ascii=False, indent=2)
    return text if len(text) <= limit else text[:limit] + "…"


def run_activity(
    state: dict, *, stage: str | None = None, waiting_for_approval: bool = False,
    tool_progress: dict[str, ToolMessage | None] | None = None,
) -> list[RunActivity]:
    run_id = state.get("run_id")
    messages = state.get("messages", [])
    start = next(
        (index for index in range(len(messages) - 1, -1, -1)
         if messages[index].id == f"{run_id}:user"),
        None,
    ) if run_id else None
    if start is None:
        return []
    entries = [RunActivity(id="prepare", kind="prepare", status="done")]
    calls: dict[str, RunActivity] = {}
    replies = 0
    model_finished = False
    current_messages = messages[start + 1:]
    context_ready = stage == "model" or bool(current_messages) or bool(state.get("response"))
    if context_ready or stage == "context":
        entries.append(RunActivity(
            id="context", kind="context", status="done" if context_ready else "running",
            memory_count=len(state.get("used_memory_ids", [])) if context_ready else None,
        ))
    for message in current_messages:
        if isinstance(message, AIMessage):
            replies += 1
            model_finished = not message.tool_calls
            entries.append(RunActivity(id=f"model:{replies}", kind="model", status="done"))
            for index, call in enumerate(message.tool_calls):
                entry = RunActivity(
                    id=f"tool:{replies}:{index}", kind="tool", status="requested",
                    name=call["name"], arguments=preview(call["args"], 4000),
                )
                entries.append(entry)
                if call["id"]:
                    calls[call["id"]] = entry
        elif isinstance(message, ToolMessage) and (entry := calls.get(message.tool_call_id)):
            apply_tool_result(entry, message)
    pending = [entry for entry in entries if entry.status == "requested"]
    if waiting_for_approval:
        for entry in pending:
            entry.status = "approval"
    if stage == "model" and not pending and not model_finished:
        entries.append(RunActivity(id=f"model:{replies + 1}", kind="model", status="running"))
    for call_id, result in (tool_progress or {}).items():
        entry = calls.get(call_id)
        if entry and entry.status == "requested":
            if result is None:
                entry.status = "running"
            else:
                apply_tool_result(entry, result)
    return entries


def apply_tool_result(entry: RunActivity, message: ToolMessage) -> None:
    result = message.text
    failed = message.status == "error"
    if entry.name in {"run_command", "write_document_python"}:
        try:
            payload = json.loads(result)
        except ValueError:
            payload = None
        failed = failed or (isinstance(payload, dict) and payload.get("ok") is False)
    entry.status = (
        "rejected" if message.additional_kwargs.get("review_rejected")
        else "error" if failed
        else "done"
    )
    entry.result = preview(result, 8000)
    entry.approved = message.additional_kwargs.get("review_approved") is True


async def observe_tool(request, execute):
    write = get_stream_writer()
    write(("tool", request.tool_call["id"], None))
    result = await execute(request)
    if isinstance(result, ToolMessage):
        if request.tool_call in request.state.get("approved_calls", []):
            result = result.model_copy(update={
                "additional_kwargs": {**result.additional_kwargs, "review_approved": True}
            })
        write(("tool", request.tool_call["id"], result))
    return result
