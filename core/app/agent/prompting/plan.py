"""Stable wire-independent prompt layers and optional provider-specific breakpoints."""

import hashlib
import json
from copy import deepcopy
from dataclasses import dataclass

from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage, ToolMessage

from app.config import PromptSettings


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def reference(name: str, value: dict | list) -> HumanMessage:
    payload = {"reference_type": name, **value} if isinstance(value, dict) else value
    return HumanMessage(name=name, content=canonical(payload))


def stable_message(message: AnyMessage) -> dict:
    # Only fields sent to the provider; checkpoint IDs and usage are not prompt text.
    result = {"role": message.type, "content": message.content}
    if message.name:
        result["name"] = message.name
    if isinstance(message, AIMessage):
        result["tool_calls"] = message.tool_calls
        result["additional_kwargs"] = message.additional_kwargs
    if isinstance(message, ToolMessage):
        result["tool_call_id"] = message.tool_call_id
    return result


@dataclass
class PromptPlan:
    messages: list[AnyMessage]
    l1_end: int
    l2_end: int
    metadata: dict

    def for_provider(self, protocol: str, breakpoints: str) -> list[AnyMessage]:
        messages = deepcopy(self.messages)
        if protocol != "anthropic" or breakpoints == "off":
            return messages
        # Mark actual provider content blocks. Never mark thinking/signature blocks.
        for boundary in sorted({self.l1_end, self.l2_end}):
            for index in range(boundary, -1, -1):
                message = messages[index]
                if isinstance(message.content, str):
                    if not message.content:
                        continue
                    message.content = [
                        {
                            "type": "text",
                            "text": message.content,
                            "cache_control": {"type": "ephemeral"},
                        }
                    ]
                    break
                blocks = message.content
                eligible = [
                    block
                    for block in blocks
                    if isinstance(block, dict)
                    and block.get("type")
                    in {"text", "tool_use", "tool_result", "image", "document"}
                ]
                if eligible:
                    eligible[-1]["cache_control"] = {"type": "ephemeral"}
                    break
        return messages


def compose(
    *,
    system: str,
    settings: PromptSettings,
    messages: list[AnyMessage],
    run_id: str,
    references: list[AnyMessage],
    snapshot: dict | None = None,
    summary: dict | None = None,
    toolset_hash: str = "",
) -> PromptPlan:
    start = next(
        (
            index
            for index, message in enumerate(messages)
            if isinstance(message, HumanMessage) and message.id == f"{run_id}:user"
        ),
        None,
    )
    if start is None:
        raise ValueError("Current user message is missing from the conversation")
    l1: list[AnyMessage] = [SystemMessage(content=system + "\nprompt_version=" + settings.version)]
    if snapshot:
        l1.append(reference("profile_snapshot", snapshot))
    history = list(messages[:start])
    if summary:
        history.insert(0, reference("conversation_summary", summary))
    current = messages[start:]
    if settings.layout == "legacy" or not settings.layered_l3:
        ordered = [*l1, *references, *history, *current]
        l2_end = len(l1) - 1
    else:
        ordered = [*l1, *history, *references, *current]
        l2_end = len(l1) + len(history) - 1
    stable_l1 = [stable_message(item) for item in l1]
    stable_l2 = [stable_message(item) for item in history]
    stable_l3 = [stable_message(item) for item in [*references, *current]]
    l1_hash = digest({"messages": stable_l1, "tools": toolset_hash})
    metadata = {
        "prompt_version": settings.version,
        "l1_hash": l1_hash,
        "l2_hash": digest(stable_l2),
        "l3_hash": digest(stable_l3),
        "l1_bytes": len(canonical(stable_l1).encode()),
        "l2_bytes": len(canonical(stable_l2).encode()),
        "toolset_hash": toolset_hash,
        "l2_turns": sum(isinstance(item, HumanMessage) for item in messages[:start]),
        "l3_bytes": len(canonical(stable_l3).encode()),
        "history_segment_version": (summary or {}).get("version", 0),
        "profile_snapshot_version": (snapshot or {}).get("version"),
        "profile_snapshot_revision": (snapshot or {}).get("revision", 0),
    }
    metadata["prompt_plan_id"] = digest(metadata)[:24]
    return PromptPlan(ordered, len(l1) - 1, l2_end, metadata)
