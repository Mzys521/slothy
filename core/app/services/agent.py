"""Durable agent requests, streaming and recovery. / 持久化 Agent 请求与恢复。"""

import asyncio
from collections.abc import AsyncIterator
from typing import Any, Literal

from anyio import CancelScope
from langgraph.graph.state import CompiledStateGraph
from langgraph.types import Command, StateSnapshot

from app.agent.activity import run_activity
from app.agent.graph import AgentState, build_graph_config, create_initial_state
from app.agent.graph.review import ApprovalPolicy, pending_review
from app.agent.metrics import run_metrics
from app.agent.reasoning import reasoning_text
from app.concurrency import SessionLocks
from app.config import AgentSettings
from app.conversations import ConversationTurn, ConversationWriter
from app.services.recovery import checkpoint_state


class AgentRunner:
    """Create one runner per application process. / 每个应用进程创建一个 Runner。

    Serialize each user/thread within this runner; external code owns SQLite resources. /
    同一 Runner 内按用户/线程串行；SQLite 资源由外部管理。

    Args:
        graph: Online graph with an injected checkpointer. / 已注入 Checkpointer 的图。
        settings: Invocation defaults, independent of storage. / 调用默认值。
        conversation_writer: Optional conversation archive. / 可选归档。
    """

    def __init__(
        self,
        graph: CompiledStateGraph,
        settings: AgentSettings | None = None,
        *,
        conversation_writer: ConversationWriter | None = None,
    ) -> None:
        """Bind one graph, its settings and an optional archive. / 绑定图与可选归档。

        Args:
            graph: Compiled graph with a checkpointer. / 已编译的图。
            settings: Invocation defaults; None loads them from env. / 调用默认值。
            conversation_writer: Optional archive for completed turns. / 可选归档。

        Raises:
            ValueError: If the graph has no checkpointer. / 缺 Checkpointer 时抛出。
        """
        if graph.checkpointer is None:
            raise ValueError("AgentRunner requires a graph with a checkpointer")
        self.graph = graph
        self.settings = settings if settings is not None else AgentSettings()
        self.conversation_writer = conversation_writer
        self._locks = SessionLocks()

    async def archive_completed(self, snapshot: StateSnapshot) -> None:
        """Archive a completed turn from saved state. / 归档已完成的轮次。

        Args:
            snapshot: Saved checkpoint snapshot. / 已保存的快照。
        """
        if self.conversation_writer is None or snapshot.next or not snapshot.values.get("response"):
            return
        values = snapshot.values
        await self.conversation_writer.save_turn(
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

    async def ainvoke(
        self,
        *,
        user_id: str,
        thread_id: str,
        message: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        personalization_enabled: bool | None = None,
    ) -> AgentState:
        """Consume state streaming and return the final state. / 消费状态流并返回最终状态。

        Args:
            user_id: Authenticated user ID. / 用户 ID。
            thread_id: Business conversation ID. / 会话 ID。
            message: New request; None resumes a pending run. / 新请求，None 表示恢复。
            run_id: New execution ID; omitted when resuming. / 新执行 ID。
            project_id: Project scope for a new request. / 项目作用域。
            personalization_enabled: Per-request personalization gate. / 个性化开关。

        Returns:
            Final graph state containing the response. / 含回复的最终状态。

        Raises:
            RuntimeError: If the run produced no response. / 无回复时抛出。
        """
        result = None
        async for event in self.astream(
            user_id=user_id,
            thread_id=thread_id,
            message=message,
            run_id=run_id,
            project_id=project_id,
            personalization_enabled=personalization_enabled,
            stream_mode="values",
        ):
            result = event
        if result is None or not result.get("response"):
            raise RuntimeError("Agent execution has not produced a completed response")
        return result

    async def astream(
        self,
        *,
        user_id: str,
        thread_id: str,
        message: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
        personalization_enabled: bool | None = None,
        approval_policy: ApprovalPolicy | None = None,
        attachment_ids: list[str] | None = None,
        references: list[dict] | None = None,
        model_selection: dict | None = None,
        decision: tuple[str, bool] | None = None,
        stream_mode: Literal["values", "updates", "messages", "custom"]
        | list[Literal["values", "updates", "messages", "custom"]] = "values",
    ) -> AsyncIterator[Any]:
        """Stream a new or resumed run with synchronous durability. / 同步落盘地流式执行。

        Args:
            user_id: Authenticated user ID. / 用户 ID。
            thread_id: Business conversation ID. / 会话 ID。
            message: New request; None resumes saved input. / 新请求，None 恢复。
            run_id: Fresh execution ID; absent from retained history. / 新执行 ID。
            project_id: New project, or stored project on resume. / 项目作用域。
            personalization_enabled: New-request gate; omit on resume. / 个性化开关。
            approval_policy: Tool review policy for new tool calls. / 审核策略。
            attachment_ids: Uploaded file IDs attached to the turn. / 附件 ID。
            model_selection: Chosen model, effort and context window. / 模型选择。
            decision: Pending approval ID and approved flag on resume. / 审批决定。
            stream_mode: LangGraph stream modes to yield. / 流模式。

        Returns:
            Async iterator of LangGraph stream events. / 流事件迭代器。

        Raises:
            ValueError: If resume overrides or identity mismatch occur. / 恢复参数冲突时抛出。
            RuntimeError: If a new request arrives during a pending run. / 有未完成运行时抛出。
        """
        config = build_graph_config({"user_id": user_id, "thread_id": thread_id}, self.settings)
        user_id, thread_id = user_id.strip(), thread_id.strip()
        if isinstance(run_id, str):
            run_id = run_id.strip()
        key = config["configurable"]["thread_id"]
        lock = self._locks.for_session(key)
        async with lock:
            snapshot, saved = await checkpoint_state(self.graph, config)
            if saved and (saved.get("user_id") != user_id or saved.get("thread_id") != thread_id):
                raise ValueError("Checkpoint identity does not match this request")
            # 中文：先补写上一轮，避免快照完成后因退出或写入失败留下归档缺口。
            # English: Repair a missing archive entry before a newer run advances the checkpoint.
            await self.archive_completed(snapshot)
            if message is None:
                if (
                    run_id is not None
                    or project_id is not None
                    or personalization_enabled is not None
                    or approval_policy is not None
                    or attachment_ids is not None
                    or references is not None
                    or model_selection is not None
                ):
                    raise ValueError(
                        "Resume preserves saved inputs; do not supply request overrides"
                    )
                if not saved or (not snapshot.next and not saved.get("response")):
                    raise ValueError("No unfinished run exists for this user/thread")
                config = build_graph_config(saved, self.settings)
                review = pending_review(snapshot)
                if review is None and decision is None and not snapshot.interrupts:
                    state = None
                elif (
                    review is not None
                    and decision is not None
                    and decision[0] == review["approval_id"]
                ):
                    state = Command(resume={decision[0]: decision[1]})
                else:
                    raise ValueError("A matching explicit decision is required to resume review")
            else:
                if decision is not None:
                    raise ValueError("A review decision cannot accompany a new message")
                if snapshot.next:
                    raise RuntimeError(
                        "An unfinished run exists; resume before sending a new request"
                    )
                if run_id is not None and (
                    snapshot.values.get("run_id") == run_id
                    or any(
                        message.id == f"{run_id}:user"
                        for message in snapshot.values.get("messages", [])
                    )
                    or (
                        self.conversation_writer is not None
                        and await self.conversation_writer.has_turn(
                            user_id=user_id, thread_id=thread_id, run_id=run_id
                        )
                    )
                ):
                    raise ValueError("This run_id is already completed; use a new run_id")
                state = create_initial_state(
                    user_id=user_id,
                    thread_id=thread_id,
                    user_input=message,
                    run_id=run_id,
                    project_id=project_id,
                    personalization_enabled=personalization_enabled,
                    attachment_ids=attachment_ids,
                    references=references,
                    settings=self.settings,
                )
                state["approval_policy"] = approval_policy or "ask"
                state["model_selection"] = model_selection or {}
                config = build_graph_config(state, self.settings)
            # 中文：等当前步快照落盘后再前进，避免响应完成后快照仍留在异步写队列中。
            # English: Commit each step before advancing rather than leave writes queued at return.
            events = self.graph.astream(state, config, stream_mode=stream_mode, durability="sync")
            try:
                while True:
                    # ASGI cancellation is level-triggered. Let the graph unwind under one
                    # cancellation, rather than repeatedly cancelling its cleanup tasks.
                    # ASGI 会重复投递取消；图执行只接收一次取消，等待它收回模型/工具任务。
                    pending = asyncio.create_task(anext(events))
                    try:
                        event = await asyncio.shield(pending)
                    except StopAsyncIteration:
                        break
                    except asyncio.CancelledError:
                        pending.cancel()
                        with CancelScope(shield=True):
                            await asyncio.gather(pending, return_exceptions=True)
                        raise
                    yield event
            finally:
                # Also close the graph when a consumer stops between two events.
                # 消费者在两个事件之间停止时，也必须显式关闭图迭代器。
                with CancelScope(shield=True):
                    await events.aclose()
            if self.conversation_writer is not None:
                await self.archive_completed(await self.graph.aget_state(config))
