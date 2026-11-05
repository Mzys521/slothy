"""会话记录契约。 / Conversation history contracts."""

from dataclasses import dataclass
from typing import Protocol

from app.agent.activity import RunActivity
from app.agent.metrics import RunMetrics


@dataclass(frozen=True)
class ConversationTurn:
    """已完成的一轮对话。 / A completed conversation turn.

    Args:
        user_id: 可信用户标识。 / Trusted user identity.
        thread_id: 用户下的会话标识。 / Conversation identity within the user.
        run_id: 会话内唯一执行标识。 / Run identity unique within the conversation.
        user_input: 原始用户文本。 / Original user text.
        response: 最终助手文本。 / Final assistant text.
    """

    user_id: str
    thread_id: str
    run_id: str
    user_input: str
    response: str
    learn_memory: bool = False
    project_id: str | None = None
    attachment_ids: tuple[str, ...] = ()
    activity: tuple[RunActivity, ...] = ()
    metrics: RunMetrics | None = None
    used_memory_ids: tuple[str, ...] = ()
    profile_version: int | None = None


class ConversationWriter(Protocol):
    """Runner 所需的归档接口。 / Archive operations required by the runner."""

    async def save_turn(self, turn: ConversationTurn) -> None:
        """turn：已完成轮次，重复写入须幂等。 / Completed turn, saved idempotently."""
        ...

    async def has_turn(self, *, user_id: str, thread_id: str, run_id: str) -> bool:
        """user_id/thread_id/run_id：用户、会话和执行标识。 / User, conversation and run IDs."""
        ...
