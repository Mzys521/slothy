"""在线 Graph 的单次运行状态。 / Per-run state for the online graph.

中文：State 只传递当前请求需要的数据，不存放服务实例、密钥、完整历史或后台记忆任务。
English: State carries only current-run data, never services, secrets, full history, or memory jobs.
"""

from typing import Annotated, Required
from uuid import uuid4

from langchain_core.messages import AnyMessage, HumanMessage
from langgraph.graph.message import add_messages
from langgraph.managed import RemainingSteps
from pydantic import JsonValue
from typing_extensions import TypedDict

from app.config import AgentSettings


class AgentState(TypedDict, total=False):
    """节点间共享的数据契约。 / Shared node-to-node data contract.

    中文：Required 标记启动必填项，其他字段逐节点补充；默认值由工厂函数提供。
    English: Required keys are run inputs; nodes fill optional keys, with factory-provided defaults.
    """

    # 中文：可信调用方解析的用户 ID，用于画像与记忆隔离；不是客户端授权凭据。
    # English: User ID resolved by a trusted caller for data isolation, never client authorization.
    user_id: Required[str]
    # 中文：当前对话线程 ID，关联消息及可选 Checkpointer；不等同于用户 ID。
    # English: Conversation ID for messages and optional checkpoints, distinct from the user ID.
    thread_id: Required[str]
    # 中文：本次执行的业务 ID，用于日志、消息幂等与后续任务追踪。
    # English: Application run ID for tracing, message idempotency, and subsequent job correlation.
    run_id: Required[str]
    # 中文：本轮原始请求，检索时使用它，避免把工具输出误当成用户问题。
    # English: Original current request for retrieval, instead of a later tool or assistant message.
    user_input: Required[str]
    # 中文：当前执行需要的消息；按 ID 合并，新增则追加，同 ID 则替换，不自动载入历史。
    # English: Working messages append or replace by ID; history is not loaded automatically.
    messages: Required[Annotated[list[AnyMessage], add_messages]]
    # 中文：当前项目 ID；None 表示未指定项目，项目记忆不得因此变成全局记忆。
    # English: Project ID; None means no project, never access to all project memories.
    project_id: str | None
    # 中文：本轮有效个性化开关，由全局开关和请求偏好共同决定。
    # English: Effective run gate combining global policy and the request's preference.
    personalization_enabled: bool
    # 中文：画像服务返回的当前快照；无画像或关闭个性化时为 None，不包含全部历史版本。
    # English: Current profile snapshot, or None when absent/disabled; excludes historical versions.
    user_profile: dict[str, JsonValue] | None
    # 中文：本轮已读取的画像版本，供回答和审计记录使用；None 表示没有使用画像。
    # English: Loaded profile version for responses and audit; None when no profile is used.
    profile_version: int | None
    # 中文：经用户、active 状态和作用域过滤后的有限候选；不是完整记忆库。
    # English: Bounded candidates filtered by user, active status, and scope, never the whole store.
    relevant_memories: list[dict[str, JsonValue]]
    # 中文：预算内的画像与记忆参考文本；默认空串，不能覆盖系统规则或本轮明确要求。
    # English: Budgeted reference text, empty by default; never overrides current instructions.
    personalization_context: str
    # 中文：实际进入个性化上下文的记忆 ID，不应直接等同于全部检索候选 ID。
    # English: IDs actually included in context, not necessarily every retrieved candidate's ID.
    used_memory_ids: list[str]
    # 中文：本轮个性化上下文的 token 估算值，0 表示尚未构建或没有注入。
    # English: Estimated personalization-context tokens; zero before construction or when empty.
    context_token_estimate: int
    # 中文：最终回答文本；初始化为空，只有成功生成并整理后才能作为成功结果返回。
    # English: Final answer text, initially empty; only a generated and finalized answer is success.
    response: str
    # 中文：终止本轮的错误摘要；可恢复的记忆读取失败应记录 warnings，而非伪造回答。
    # English: Fatal run error summary; recoverable memory-read failures belong in warnings.
    error: str | None
    # 中文：非致命降级信息，顺序节点写入旧项加新项的完整列表，新运行可用 [] 清空。
    # English: Nonfatal notes; nodes write old plus new notes; [] resets them for a new run.
    warnings: list[str]
    # 中文：LangGraph 动态提供的剩余 super-step 数，不手动初始化、不写入业务数据库。
    # English: Remaining super-steps managed by LangGraph, never initialized or persisted manually.
    remaining_steps: RemainingSteps
    approval_policy: str
    approved_calls: list[dict]
    attachment_ids: list[str]
    model_selection: dict[str, JsonValue]
    model_stats: list[dict[str, JsonValue]]
    # Persistent across new invocations; the factory must not reset these fields.
    history_summary: dict[str, JsonValue]
    history_compression_pending: bool
    profile_snapshot: dict[str, JsonValue] | None


def create_initial_state(
    *,
    user_id: str,
    thread_id: str,
    user_input: str,
    settings: AgentSettings | None = None,
    run_id: str | None = None,
    project_id: str | None = None,
    personalization_enabled: bool | None = None,
    attachment_ids: list[str] | None = None,
) -> AgentState:
    """为一次新执行创建独立默认值。 / Create independent defaults for a new invocation.

    Args:
        user_id: 已验证身份的用户 ID。 / User ID from validated identity.
        thread_id: 已由应用服务创建或校验的线程 ID。 / Application-created/validated thread ID.
        user_input: 本轮原始请求，保留原始格式。 / Current request with its original formatting.
        settings: 部署默认配置，省略时读取环境。 / Deployment defaults, loaded from env if omitted.
        run_id: 可复用的业务执行 ID，省略时生成 UUID。 / Reusable run ID, otherwise a new UUID.
        project_id: 当前项目 ID，None 表示未指定。 / Current project ID, or None if unspecified.
        personalization_enabled: 本轮开关，None 沿用全局设置。 / Request gate; None inherits.

    中文：本函数不查询画像、不检索记忆、不保存线程，也不会初始化 managed remaining_steps。
    English: No profile/memory queries, thread persistence, or managed-step initialization.
    """
    resolved_settings = settings if settings is not None else AgentSettings()
    for name, value in {
        "user_id": user_id,
        "thread_id": thread_id,
        "user_input": user_input,
        **({"run_id": run_id} if run_id is not None else {}),
        **({"project_id": project_id} if project_id is not None else {}),
    }.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{name} must be a non-empty string")
    resolved_user_id = user_id.strip()
    resolved_thread_id = thread_id.strip()
    resolved_run_id = str(uuid4()) if run_id is None else run_id.strip()
    resolved_project_id = None if project_id is None else project_id.strip()

    return AgentState(
        user_id=resolved_user_id,
        thread_id=resolved_thread_id,
        run_id=resolved_run_id,
        user_input=user_input,
        messages=[HumanMessage(content=user_input, id=f"{resolved_run_id}:user")],
        project_id=resolved_project_id,
        personalization_enabled=(
            resolved_settings.personalization_enabled and personalization_enabled is not False
        ),
        user_profile=None,
        profile_version=None,
        relevant_memories=[],
        personalization_context="",
        used_memory_ids=[],
        context_token_estimate=0,
        response="",
        error=None,
        warnings=[],
        approved_calls=[],
        attachment_ids=list(attachment_ids or []),
        model_selection={},
        model_stats=[],
    )
