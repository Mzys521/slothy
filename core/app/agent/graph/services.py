"""Read-only integration ports, without memory evolution. / 在线只读集成接口，不包含记忆写入。"""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from app.agent.providers import ModelProvider
from app.personalization.context import ContextBuildResult

# Domain services return JSON snapshots and need not import graph or state types. /
# 中文：领域服务输出可序列化快照，领域实现无需导入 Graph 或 State。
Snapshot = dict[str, JsonValue]


class ProfileReader(Protocol):
    """Read an existing profile without generating one. / 读取已有画像，不生成画像。"""

    async def get_profile(self, user_id: str) -> Snapshot | None:
        """Return the user's current profile snapshot, if any. / 返回用户当前画像快照（若有）。

        Args:
            user_id: Trusted user identifier. / 可信用户标识。

        Returns:
            English: The current snapshot, or ``None`` when no profile exists.
            / 中文：当前快照；没有画像时返回 ``None``。
        """
        ...


class MemoryReader(Protocol):
    """Retrieve existing memories without extracting or updating.
    检索已有记忆，不提取、不更新。
    """

    async def retrieve(
        self, *, user_id: str, query: str, thread_id: str, project_id: str | None, limit: int
    ) -> Sequence[Snapshot]:
        """Return filtered and ranked memory candidates. / 返回已过滤、排序的候选记忆。

        Args:
            user_id: User isolation constraint. / 用户隔离条件。
            query: Original current request. / 本轮原始请求。
            thread_id: Allowed thread scope. / 允许读取的线程作用域。
            project_id: Current project scope, or ``None``. / 当前项目作用域，或 ``None``。
            limit: Maximum result count. / 返回条数上限。

        Returns:
            English: Candidates carrying ownership, status, scope and ``scope_ref``; the
            service owns recall, expiry and ranking. / 中文：包含所有权、状态、作用域与
            ``scope_ref`` 的候选；召回、过期与排序由服务负责。
        """
        ...


class ContextBuilder(Protocol):
    """Convert relevant snapshots into bounded context. / 将相关快照压缩为有界上下文。"""

    async def build(
        self,
        *,
        user_input: str,
        user_profile: Snapshot | None,
        relevant_memories: Sequence[Snapshot],
        project_id: str | None,
        token_budget: int,
    ) -> ContextBuildResult:
        """Select, deduplicate and bound injected reference data. / 选择、去重并限制注入的参考数据。

        Args:
            user_input: Current task defining relevance. / 本轮任务，决定相关性。
            user_profile: Current profile, or ``None``. / 当前画像，或 ``None``。
            relevant_memories: Isolated, bounded candidates. / 已隔离且有数量上限的候选。
            project_id: Current project, or ``None``. / 当前项目，或 ``None``。
            token_budget: Combined profile and memory token budget. / 画像与记忆共享的 token 预算。

        Returns:
            English: Bounded context text with the memory IDs actually used and a token estimate.
            / 中文：有界上下文文本，以及实际使用的记忆 ID 与 token 估算。
        """
        ...


class AttachmentReader(Protocol):
    """Read attachment metadata and text scoped to one user and thread.
    读取限定在用户与线程内的附件内容。
    """

    async def read_attachments(self, user: str, thread: str, ids: list[str]) -> list[dict]:
        """Read attachments scoped to one user and thread. / 读取限定在用户与线程内的附件。

        Args:
            user: Trusted user identifier. / 中文：可信用户标识。
            thread: Thread that owns the attachments. / 中文：附件所属线程。
            ids: Attachment IDs to read. / 中文：待读取的附件 ID 列表。

        Returns:
            English: Attachment metadata and text for the requested IDs.
            中文：所请求附件的元数据与文本。
        """
        ...


class ProfileSnapshotReader(Protocol):
    """Persist or refresh the frozen profile reference snapshot.
    持久化或刷新冻结的画像参考快照。
    """

    async def read(self, user: str, thread: str, current: Snapshot | None) -> dict:
        """Read or refresh the frozen profile snapshot for this turn.
        读取或刷新本轮的冻结画像快照。

        Args:
            user: Trusted user identifier. / 中文：可信用户标识。
            thread: Thread that scopes the snapshot. / 中文：限定快照的线程。
            current: Currently loaded profile, or ``None``. / 中文：当前已加载的画像，或 ``None``。

        Returns:
            English: The frozen ``profile`` snapshot with its ``version`` and ``revision``.
            / 中文：冻结的 ``profile`` 快照，以及其 ``version`` 与 ``revision``。
        """
        ...


@dataclass(frozen=True)
class HistorySummaryResult:
    """Summarizer output with optional usage metadata. / 历史摘要结果，可携带用量元数据。

    Attributes:
        text: English: Summary text. / 中文：摘要文本。
        usage: English: Provider usage metadata, or ``None``. / 中文：模型用量元数据，或 ``None``。
    """

    text: str
    usage: dict | None = None


@dataclass(frozen=True)
class AgentServices:
    """Startup dependencies kept outside graph state. / 启动时注入、不放进 State 的依赖。

    Args:
        model_provider: Required reusable model entry for the whole graph.
            中文：必填的模型入口，整张图复用。
        profile_reader: Optional existing-profile reader. / 可选画像读取器，缺省表示暂无画像。
        memory_reader: Optional existing-memory retriever. / 可选记忆读取器，缺省返回空候选。
        context_builder: Optional personalization context builder.
            中文：可选个性化构建器，缺省不注入历史数据。
        attachment_reader: Optional attachment reader scoped by user/thread.
            中文：可选附件读取器，按用户/线程隔离。
        personalization_gate: Optional live check that personalization may still be used.
            / 中文：可选实时开关，判断本轮是否仍可使用个性化数据。
        profile_snapshot_reader: Optional layered-prompt snapshot reader.
            中文：可选分层提示词快照读取器。
        history_summarizer: Optional model-backed history summarizer.
            中文：可选基于模型的历史摘要器。
        prompt_observer: Optional callback receiving prompt plan metadata.
            中文：可选提示词计划元数据回调。
        skill_reader: Optional provider of enabled skill descriptors. / 可选已启用技能描述提供方。
        tool_enabled: Optional per-run tool availability check. / 可选单次运行的工具可用性检查。
    """

    model_provider: ModelProvider
    profile_reader: ProfileReader | None = None
    memory_reader: MemoryReader | None = None
    context_builder: ContextBuilder | None = None
    attachment_reader: AttachmentReader | None = None
    personalization_gate: Callable[[str], Awaitable[bool]] | None = None
    profile_snapshot_reader: ProfileSnapshotReader | None = None
    history_summarizer: (
        Callable[[list, dict | None], Awaitable[str | HistorySummaryResult]] | None
    ) = None
    prompt_observer: Callable | None = None
    skill_reader: Callable[[], list[dict]] | None = None
    tool_enabled: Callable[[str], bool] | None = None
    reference_reader: Callable | None = None
    identity_reader: Callable | None = None
    reference_metadata_reader: Callable | None = None
    memory_status_reader: Callable | None = None
    runtime_journal: object | None = None
