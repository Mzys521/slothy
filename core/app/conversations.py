"""Conversation history contracts. / 会话记录契约。"""

from dataclasses import dataclass, field
from typing import Protocol

from app.agent.activity import RunActivity
from app.agent.metrics import RunMetrics


@dataclass(frozen=True)
class ConversationTurn:
    """A completed conversation turn. / 已完成的一轮对话。

    Args:
        user_id: Trusted user identity. / 可信用户标识。
        thread_id: Conversation identity within the user. / 用户下的会话标识。
        run_id: Run identity unique within the conversation. / 会话内唯一执行标识。
        user_input: Original user text. / 原始用户文本。
        response: Final assistant text. / 最终助手文本。
        reasoning: Display-only text, never prompt content.
            / 仅供展示的推理文本，不进入提示词。
        learn_memory: Whether this turn may feed memory learning. / 本轮是否参与记忆学习。
        project_id: Project scope recorded for the turn. / 本轮记录的项目作用域。
        attachment_ids: Uploaded file IDs referenced by the turn. / 本轮引用的上传文件 ID。
        activity: Public execution activity entries. / 公开的执行活动条目。
        metrics: Token and timing metrics, if recorded. / 已记录的 token 与耗时指标。
        used_memory_ids: Memory IDs injected into the prompt. / 注入提示词的记忆 ID。
        profile_version: Profile version used for the turn. / 本轮使用的画像版本。
    """

    user_id: str
    thread_id: str
    run_id: str
    user_input: str
    response: str
    reasoning: str = ""
    learn_memory: bool = False
    project_id: str | None = None
    attachment_ids: tuple[str, ...] = ()
    activity: tuple[RunActivity, ...] = ()
    metrics: RunMetrics | None = None
    used_memory_ids: tuple[str, ...] = ()
    profile_version: int | None = None
    references: tuple[dict, ...] = ()
    memory_trace: dict = field(default_factory=dict)


class ConversationWriter(Protocol):
    """Archive operations required by the runner. / Runner 所需的归档接口。"""

    async def save_turn(self, turn: ConversationTurn) -> None:
        """Persist one completed turn idempotently. / 幂等保存一轮已完成的对话。

        Args:
            turn: Completed turn; repeated writes must be idempotent. / 已完成轮次；重复写入须幂等。
        """
        ...

    async def has_turn(self, *, user_id: str, thread_id: str, run_id: str) -> bool:
        """Report whether a run was already archived. / 判断某次执行是否已归档。

        Args:
            user_id: Trusted user identity. / 可信用户标识。
            thread_id: Conversation identity within the user. / 用户下的会话标识。
            run_id: Run identity unique within the conversation. / 会话内唯一执行标识。

        Returns:
            True when the turn already exists. / 该轮次已存在时返回 True。
        """
        ...
