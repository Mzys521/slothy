"""Read native checkpoint state and archive completed runs without any model.

Reading a thread's saved state must not depend on a configured model or an execution graph:
the projection graph carries the same state definition and node topology, so LangGraph can
recompute pending tasks and interrupts from the stored checkpoints without running a node.
/ 读取会话已保存状态时不得依赖已配置的模型或执行图：只读投影图携带相同的状态定义与节点拓扑，
LangGraph 因此能仅凭已存检查点重算待执行任务与中断，而不会执行任何节点。
"""

from dataclasses import dataclass
from typing import Literal

from langchain_core.runnables import RunnableConfig
from langgraph.graph import START
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import StateSnapshot

from app.agent.activity import run_activity
from app.agent.graph.review import pending_review
from app.agent.graph.runtime import build_graph_config
from app.agent.metrics import run_metrics
from app.agent.reasoning import reasoning_text
from app.config import AgentSettings
from app.conversations import ConversationTurn, ConversationWriter

RunState = Literal["not_started", "pending", "awaiting_approval", "completed", "failed"]


@dataclass(frozen=True)
class RunStatus:
    """Read-only status of one logical run. / 一次逻辑运行的只读状态。

    Attributes:
        status: Lifecycle stage visible to the caller. / 调用方可见的生命周期阶段。
        run_id: Run identifier when the checkpoint knows one. / 检查点已知的运行标识。
        checkpoint_id: Latest checkpoint identifier when one exists. / 最新检查点标识。
        message: Original user input of that run. / 该运行的原始用户输入。
        archived: Whether the completed turn is already persisted. / 已完成轮次是否已归档。
    """

    status: RunState
    run_id: str | None = None
    checkpoint_id: str | None = None
    message: str = ""
    archived: bool = False


async def checkpoint_state(
    graph: CompiledStateGraph, config: RunnableConfig
) -> tuple[StateSnapshot, dict]:
    """Return the latest snapshot and its saved request without starting execution.
    / 返回最新快照与保存的原始请求，不启动执行或重建输入。"""
    snapshot = await graph.aget_state(config)
    values = snapshot.values
    if START in snapshot.next:
        saved = await graph.checkpointer.aget_tuple(snapshot.config)
        seed = saved.checkpoint["channel_values"].get(START) if saved else None
        if isinstance(seed, dict):
            values = seed
    return snapshot, values


class CheckpointReader:
    """Project one thread's saved state without a model or tool binding. /
    不绑定模型或工具即可投影会话的已保存状态。

    Args:
        graph: Projection graph sharing the execution topology. / 与执行图相同拓扑的投影图。
        settings: Defaults used to derive the checkpoint key. / 派生检查点键的默认配置。
    """

    def __init__(
        self, graph: CompiledStateGraph, settings: AgentSettings | None = None
    ) -> None:
        """Bind the projection graph and settings. / 绑定投影图与配置。

        Args:
            graph: Compiled projection graph with the shared checkpointer. / 共享检查点的投影图。
            settings: Deployment defaults; environment values when omitted. / 缺省时读取环境的配置。
        """
        self.graph = graph
        self.settings = settings if settings is not None else AgentSettings()

    def config(self, user_id: str, thread_id: str) -> RunnableConfig:
        """Build the checkpoint key for one session. / 为会话构建检查点键。

        Args:
            user_id: Trusted user identifier. / 可信用户标识。
            thread_id: Conversation identifier. / 会话标识。

        Returns:
            The runnable config addressing that thread. / 指向该会话的运行配置。
        """
        return build_graph_config(
            {"user_id": user_id, "thread_id": thread_id}, self.settings
        )

    async def get_thread_state(self, user_id: str, thread_id: str) -> tuple[StateSnapshot, dict]:
        """Return the latest native snapshot and its saved request. / 返回最新原生快照与保存的请求。

        Args:
            user_id: Trusted user identifier. / 可信用户标识。
            thread_id: Conversation identifier. / 会话标识。

        Returns:
            The snapshot plus its effective values, including an unexecuted START seed. /
            快照及其有效值，包含尚未执行的 START 种子。
        """
        return await checkpoint_state(self.graph, self.config(user_id, thread_id))

    async def get_approval(self, user_id: str, thread_id: str) -> dict | None:
        """Return the pending tool review of a session, if any. / 返回会话待处理的工具审批。

        Args:
            user_id: Trusted user identifier. / 可信用户标识。
            thread_id: Conversation identifier. / 会话标识。

        Returns:
            The pending review payload, or None. / 待审批载荷；没有时返回 None。
        """
        snapshot, _ = await self.get_thread_state(user_id, thread_id)
        return pending_review(snapshot)


class ConversationArchiver:
    """Archive completed checkpoint snapshots through the conversation writer. /
    通过会话写入器归档已完成的检查点快照。

    Args:
        writer: Optional idempotent conversation archive. / 可选的幂等会话归档。
    """

    def __init__(self, writer: ConversationWriter | None) -> None:
        """Bind the archive writer. / 绑定归档写入器。

        Args:
            writer: Archive owning idempotent turn writes. / 负责幂等轮次写入的归档。
        """
        self.writer = writer

    async def archive_completed(self, snapshot: StateSnapshot) -> bool:
        """Persist one completed turn idempotently. / 幂等持久化一轮已完成的对话。

        Args:
            snapshot: Saved checkpoint snapshot. / 已保存的检查点快照。

        Returns:
            True when a completed turn was archived. / 已归档完成轮次时为 True。
        """
        if self.writer is None or snapshot.next or not snapshot.values.get("response"):
            return False
        values = snapshot.values
        await self.writer.save_turn(
            ConversationTurn(
                values["user_id"],
                values["thread_id"],
                values["run_id"],
                values["user_input"],
                values["response"],
                reasoning=reasoning_text(values.get("messages", [])),
                learn_memory=values.get("personalization_enabled", False)
                and values.get("response_source") != "identity_fact",
                project_id=values.get("project_id"),
                attachment_ids=tuple(values.get("attachment_ids", ())),
                activity=tuple(run_activity(values)),
                metrics=run_metrics(values),
                used_memory_ids=tuple(values.get("used_memory_ids", ())),
                profile_version=values.get("profile_version"),
                references=tuple(values.get("resolved_references", ())),
                memory_trace=values.get("memory_trace", {}),
            )
        )
        return True

    async def has_archived(self, user_id: str, thread_id: str, run_id: str) -> bool:
        """Report whether a completed turn is already stored. / 报告已完成轮次是否已存储。

        Args:
            user_id: Trusted user identifier. / 可信用户标识。
            thread_id: Conversation identifier. / 会话标识。
            run_id: Run identifier. / 运行标识。

        Returns:
            True when the archive already holds that run. / 归档中已有该运行时为 True。
        """
        if self.writer is None:
            return False
        return await self.writer.has_turn(
            user_id=user_id, thread_id=thread_id, run_id=run_id
        )


def run_status(snapshot: StateSnapshot, values: dict, *, archived: bool) -> RunStatus:
    """Project one checkpoint into a read-only run status. / 把检查点投影为只读运行状态。

    Args:
        snapshot: Latest native snapshot of the thread. / 会话的最新原生快照。
        values: Effective values, including an unexecuted START seed. / 有效值，含未执行种子。
        archived: Whether the completed turn is persisted. / 完成轮次是否已持久化。

    Returns:
        Status distinguishing not started, pending, approval, completed and failed runs. /
        区分尚未创建、待继续、待审批、已完成与失败的运行状态。
    """
    run_id = values.get("run_id")
    checkpoint_id = (snapshot.config or {}).get("configurable", {}).get("checkpoint_id")
    message = values.get("user_input")
    if not run_id:
        return RunStatus("not_started", checkpoint_id=checkpoint_id, archived=archived)
    if not snapshot.next:
        return RunStatus(
            "completed" if values.get("response") else "not_started",
            run_id,
            checkpoint_id,
            message or "",
            archived,
        )
    if pending_review(snapshot) is not None:
        status: RunState = "awaiting_approval"
    elif any(task.error for task in snapshot.tasks):
        status = "failed"
    else:
        status = "pending"
    return RunStatus(status, run_id, checkpoint_id, message or "", archived)
