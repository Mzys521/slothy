"""在线读取接口，不包含记忆写入。 / Read-only integration ports, without memory evolution."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Protocol

from pydantic import JsonValue

from app.agent.providers import ModelProvider
from app.personalization.context import ContextBuildResult

# 中文：领域服务输出可序列化快照，领域实现无需导入 Graph 或 State。
# English: Domain services return JSON snapshots and need not import Graph or State.
Snapshot = dict[str, JsonValue]


class ProfileReader(Protocol):
    """读取已有画像，不生成画像。 / Read an existing profile without generating one."""

    async def get_profile(self, user_id: str) -> Snapshot | None:
        """user_id：可信用户标识。 / Trusted user whose current snapshot is requested."""
        ...


class MemoryReader(Protocol):
    """检索已有记忆，不提取或更新。 / Retrieve existing memories without extracting/updating."""

    async def retrieve(
        self, *, user_id: str, query: str, thread_id: str, project_id: str | None, limit: int
    ) -> Sequence[Snapshot]:
        """返回已过滤、排序的候选。 / Return filtered and ranked candidates.

        Args:
            user_id: 用户隔离条件。 / User isolation constraint.
            query: 本轮原始请求。 / Original current request.
            thread_id: 允许读取的线程作用域。 / Allowed thread scope.
            project_id: 当前项目作用域或 None。 / Current project scope, or None.
            limit: 返回条数上限。 / Maximum result count.

        中文：每项需包含 id、user_id、status、scope、scope_ref；服务负责召回、过期和排序。
        English: Items include ownership/status/scope; services own recall, expiry and ranking.
        """
        ...


class ContextBuilder(Protocol):
    """将相关快照压缩为上下文。 / Convert relevant snapshots into bounded context."""

    async def build(
        self,
        *,
        user_input: str,
        user_profile: Snapshot | None,
        relevant_memories: Sequence[Snapshot],
        project_id: str | None,
        token_budget: int,
    ) -> ContextBuildResult:
        """选择、去重并限制注入。 / Select, deduplicate, and bound injected reference data.

        Args:
            user_input: 本轮任务，决定相关性。 / Current task defining relevance.
            user_profile: 当前画像或 None。 / Current profile, or None.
            relevant_memories: 已隔离且有数量上限的候选。 / Isolated, bounded candidates.
            project_id: 当前项目或 None。 / Current project, or None.
            token_budget: 画像和记忆共享的 token 预算。 / Combined profile/memory token budget.
        """
        ...


class AttachmentReader(Protocol):
    async def read_attachments(self, user: str, thread: str, ids: list[str]) -> list[dict]: ...


class ProfileSnapshotReader(Protocol):
    async def read(self, user: str, thread: str, current: Snapshot | None) -> dict: ...


@dataclass(frozen=True)
class HistorySummaryResult:
    text: str
    usage: dict | None = None


@dataclass(frozen=True)
class AgentServices:
    """启动时注入服务，不放进 State。 / Startup dependencies kept outside State.

    Args:
        model_provider: 必填模型入口，整张图复用。 / Required reusable model provider.
        profile_reader: 可选画像读取器，缺省表示暂无画像。 / Optional existing-profile reader.
        memory_reader: 可选记忆读取器，缺省返回空候选。 / Optional existing-memory retriever.
        context_builder: 可选个性化构建器，缺省不注入历史数据。 / Optional context builder.
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
