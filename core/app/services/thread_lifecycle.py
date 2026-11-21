"""Retryable, resumable cleanup of threads and workspaces across every store.

A cleanup is a durable task: the archive rows, the checkpoint thread, memory evidence, the
profile snapshot and the runtime journal are advanced one recorded step at a time, so an
interrupted or failed step can be retried instead of leaving half-cleared data. /
清理是可持久化的任务：归档行、检查点线程、记忆证据、画像快照与运行日志按已记录步骤逐步推进，
中断或失败的步骤可以重试，而不是留下半完成状态。
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import uuid4

from app.infrastructure.conversations import SQLiteConversationStore

# Steps per thread; each one is idempotent and recorded before the next starts.
# 每个会话的步骤；每步幂等，写入记录后才进入下一步。
CLEANUP_STEPS = ("checkpoint", "memory", "archive", "profile", "journal")
CLEAR_KINDS = {"thread_clear"}
DELETE_KINDS = {"thread_delete", "workspace_delete"}


@dataclass(frozen=True)
class CleanupOutcome:
    """Result of advancing one cleanup task. / 推进一个清理任务的结果。

    Attributes:
        task_id: Cleanup task identifier. / 清理任务标识。
        state: pending, running, failed or done. / 任务状态。
        complete: Whether every recorded step finished. / 是否所有步骤已完成。
        error: Stable failure code when the task needs another attempt. / 需要重试时的稳定错误码。
    """

    task_id: str
    state: str
    complete: bool
    error: str | None = None


class ThreadLifecycle:
    """Advance durable thread and workspace cleanups. / 推进可持久化的会话与工作区清理。

    Args:
        conversations: Archive owning durable task rows and transactional deletes. /
            保存持久任务行并执行事务删除的归档。
        checkpoint_purger: Awaitable removing one checkpoint thread. / 删除检查点线程的可等待对象。
        memory_forgetter: Awaitable removing one thread's memory evidence. /
            删除会话记忆证据的可等待对象。
        profile_resetter: Awaitable dropping one thread's profile snapshot. /
            丢弃会话画像快照的可等待对象。
        journal_deleter: Awaitable dropping one thread's runtime journal. /
            丢弃会话运行日志的可等待对象。
    """

    def __init__(
        self,
        conversations: SQLiteConversationStore,
        *,
        checkpoint_purger: Callable[[str, str], Awaitable[None]],
        memory_forgetter: Callable[[str, str], Awaitable[None]],
        profile_resetter: Callable[[str, str], Awaitable[None]],
        journal_deleter: Callable[[str], Awaitable[object]],
    ) -> None:
        """Bind the cleanup steps. / 绑定清理步骤。

        Args:
            conversations: Archive owning cleanup state. / 保存清理状态的归档。
            checkpoint_purger: Checkpoint thread remover. / 检查点线程删除器。
            memory_forgetter: Memory evidence remover. / 记忆证据删除器。
            profile_resetter: Profile snapshot remover. / 画像快照删除器。
            journal_deleter: Runtime journal remover. / 运行日志删除器。
        """
        self.conversations = conversations
        self.checkpoint_purger = checkpoint_purger
        self.memory_forgetter = memory_forgetter
        self.profile_resetter = profile_resetter
        self.journal_deleter = journal_deleter

    async def begin_cleanup(
        self, *, user: str, kind: str, target_id: str, thread_ids: list[str]
    ) -> dict:
        """Persist a cleanup task and start accepting no more writes for its threads. /
        持久化清理任务，并让涉及的会话不再接受写入。

        Args:
            user: Owning user id. / 所属用户 ID。
            kind: thread_clear, thread_delete or workspace_delete. / 清理类型。
            target_id: Thread or workspace identifier. / 会话或工作区标识。
            thread_ids: Threads covered by this cleanup. / 本次清理覆盖的会话。

        Returns:
            The stored task. / 已存储的任务。
        """
        return await self.conversations.begin_cleanup(
            user, uuid4().hex, kind, target_id, thread_ids
        )

    async def run(self, task: dict) -> CleanupOutcome:
        """Advance every not-yet-recorded step of one task. / 推进任务中尚未记录的步骤。

        Args:
            task: Stored cleanup task. / 已存储的清理任务。

        Returns:
            The outcome of this attempt; a failure is recorded so a restart can retry it. /
            本次尝试的结果；失败会被记录，以便重启后重试。
        """
        user = task["user_id"]
        kind = task["kind"]
        done = set(task["steps"])
        try:
            for thread in task["threads"]:
                for step in CLEANUP_STEPS:
                    key = f"{step}:{thread}"
                    if key in done:
                        continue
                    await self._step(step, user, thread, kind)
                    await self.conversations.mark_cleanup_step(task["id"], key)
                    done.add(key)
            if kind == "workspace_delete":
                await self.conversations.delete_workspace(user, task["target_id"])
            else:
                await self.conversations.release_thread_cleanup(user, task["threads"])
            await self.conversations.finish_cleanup(task["id"])
            return CleanupOutcome(task["id"], "done", True)
        except Exception:
            await self.conversations.fail_cleanup(task["id"], "CLEANUP_FAILED")
            return CleanupOutcome(task["id"], "failed", False, "CLEANUP_FAILED")

    async def _step(self, step: str, user: str, thread: str, kind: str) -> None:
        """Run one idempotent cleanup step. / 执行一个幂等清理步骤。

        Args:
            step: Step name. / 步骤名称。
            user: Owning user id. / 所属用户 ID。
            thread: Thread being cleaned. / 被清理的会话。
            kind: Cleanup kind controlling how the archive rows are removed. /
                决定归档行删除方式的清理类型。
        """
        if step == "checkpoint":
            await self.checkpoint_purger(user, thread)
        elif step == "memory":
            await self.memory_forgetter(user, thread)
        elif step == "archive":
            if kind in DELETE_KINDS:
                await self.conversations.delete_thread(user, thread)
            else:
                await self.conversations.clear_thread(user, thread)
        elif step == "profile":
            await self.profile_resetter(user, thread)
        elif step == "journal":
            await self.journal_deleter(thread)
        else:
            raise ValueError(f"Unknown cleanup step: {step}")

    async def resume_unfinished(self) -> list[CleanupOutcome]:
        """Continue cleanups interrupted by a restart. / 继续被重启中断的清理任务。

        Returns:
            The outcome of every resumed task. / 每个继续任务的执行结果。
        """
        return [
            await self.run(task) for task in await self.conversations.unfinished_cleanup_tasks()
        ]
