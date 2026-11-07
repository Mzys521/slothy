"""Per-run state for the online graph. / 在线 Graph 的单次运行状态。

State carries only current-run data, never services, secrets, full history or memory jobs. /
中文：State 只传递当前请求需要的数据，不存放服务实例、密钥、完整历史或后台记忆任务。
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
    """Shared node-to-node data contract. / 节点间共享的数据契约。

    Required keys are run inputs; nodes fill optional keys, with factory-provided defaults. /
    中文：Required 标记启动必填项，其他字段逐节点补充；默认值由工厂函数提供。
    """

    # User ID resolved by a trusted caller for data isolation, never client authorization. /
    # 中文：可信调用方解析的用户 ID，用于画像与记忆隔离；不是客户端授权凭据。
    user_id: Required[str]
    # Conversation ID for messages and optional checkpoints, distinct from the user ID. /
    # 中文：当前对话线程 ID，关联消息及可选 Checkpointer；不等同于用户 ID。
    thread_id: Required[str]
    # Application run ID for tracing, message idempotency and later job correlation. /
    # 中文：本次执行的业务 ID，用于日志、消息幂等与后续任务追踪。
    run_id: Required[str]
    # Original current request for retrieval, instead of a later tool or assistant message. /
    # 中文：本轮原始请求，检索时使用它，避免把工具输出误当成用户问题。
    user_input: Required[str]
    # Working messages append or replace by ID; history is not loaded automatically. /
    # 中文：当前执行需要的消息；按 ID 合并，新增则追加，同 ID 则替换，不自动载入历史。
    messages: Required[Annotated[list[AnyMessage], add_messages]]
    # Project ID; ``None`` means no project, never access to all project memories. /
    # 中文：当前项目 ID；None 表示未指定项目，项目记忆不得因此变成全局记忆。
    project_id: str | None
    # Effective run gate combining global policy and the request preference. /
    # 中文：本轮有效个性化开关，由全局开关和请求偏好共同决定。
    personalization_enabled: bool
    # Current profile snapshot, or None when absent/disabled; excludes historical versions. /
    # 中文：画像服务返回的当前快照；无画像或关闭个性化时为 None，不包含全部历史版本。
    user_profile: dict[str, JsonValue] | None
    # Loaded profile version for responses and audit; None when no profile is used. /
    # 中文：本轮已读取的画像版本，供回答和审计记录使用；None 表示没有使用画像。
    profile_version: int | None
    # Bounded candidates filtered by user, active status and scope, never the whole store. /
    # 中文：经用户、active 状态和作用域过滤后的有限候选；不是完整记忆库。
    relevant_memories: list[dict[str, JsonValue]]
    # Budgeted reference text, empty by default; never overrides current instructions. /
    # 中文：预算内的画像与记忆参考文本；默认空串，不能覆盖系统规则或本轮明确要求。
    personalization_context: str
    # IDs actually included in context, not necessarily every retrieved candidate's ID. /
    # 中文：实际进入个性化上下文的记忆 ID，不应直接等同于全部检索候选 ID。
    used_memory_ids: list[str]
    memory_trace: dict
    response_source: str
    # Estimated personalization-context tokens; zero before construction or when empty. /
    # 中文：本轮个性化上下文的 token 估算值，0 表示尚未构建或没有注入。
    context_token_estimate: int
    # Final answer text, initially empty; only a generated and finalized answer is success. /
    # 中文：最终回答文本；初始化为空，只有成功生成并整理后才能作为成功结果返回。
    response: str
    # Fatal run error summary; recoverable memory-read failures belong in warnings. /
    # 中文：终止本轮的错误摘要；可恢复的记忆读取失败应记录 warnings，而非伪造回答。
    error: str | None
    # Nonfatal notes; nodes write old plus new notes; [] resets them for a new run. /
    # 中文：非致命降级信息，顺序节点写入旧项加新项的完整列表，新运行可用 [] 清空。
    warnings: list[str]
    # Remaining super-steps managed by LangGraph, never initialized or persisted manually. /
    # 中文：LangGraph 动态提供的剩余 super-step 数，不手动初始化、不写入业务数据库。
    remaining_steps: RemainingSteps
    # Per-request tool approval policy: ``"ask"`` or ``"every"``; preserved on resume. /
    # 中文：单次请求的工具审批策略：``"ask"`` 或 ``"every"``；恢复执行时沿用。
    approval_policy: str
    # Tool calls of the current batch that the user already approved. /
    # 中文：当前批次中已经获得用户批准的工具调用。
    approved_calls: list[dict]
    # Uploaded attachment IDs referenced by this turn. / 中文：本轮引用的已上传附件 ID。
    attachment_ids: list[str]
    # Model identity, effort and context window selected for this run. /
    # 中文：本轮选择的模型标识、推理强度与上下文窗口。
    model_selection: dict[str, JsonValue]
    # Per-model-call usage rows used to derive run metrics.
    # 中文：逐次模型调用的用量记录，用于汇总指标。
    model_stats: list[dict[str, JsonValue]]
    # Persistent across new invocations; the factory must not reset these fields. /
    # 中文：跨新一轮调用持久保留；工厂函数不得重置这些字段。
    history_summary: dict[str, JsonValue]
    history_compression_pending: bool
    profile_snapshot: dict[str, JsonValue] | None
    tool_contract_version: int
    tool_review_started_at: float
    tool_approval_wait: dict[str, int]
    tool_risks: dict[str, str]
    references: list[dict]
    resolved_references: list[dict]
    used_reference_ids: list[str]


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
    references: list[dict] | None = None,
) -> AgentState:
    """Create independent defaults for one new invocation. / 为一次新执行创建独立默认值。

    This function performs no profile or memory query, no thread persistence, and never
    initializes the managed ``remaining_steps``. / 中文：本函数不查询画像、不检索记忆、
    不保存线程，也不会初始化 managed ``remaining_steps``。

    Args:
        user_id: User ID from validated identity. / 已验证身份的用户 ID。
        thread_id: Application-created or validated thread ID. / 已由应用创建或校验的线程 ID。
        user_input: Current request, with its original formatting. / 本轮原始请求，保留原始格式。
        settings: Deployment defaults, loaded from environment when omitted.
            中文：部署默认配置，省略时读取环境。
        run_id: Reusable business run ID, otherwise a new UUID.
            中文：可复用的业务执行 ID，省略时生成 UUID。
        project_id: Current project ID, or ``None`` when unspecified.
            中文：当前项目 ID，None 表示未指定。
        personalization_enabled: Request gate; ``None`` inherits the global setting.
            中文：本轮开关，None 沿用全局设置。
        attachment_ids: Uploaded attachment IDs referenced by this turn. / 本轮引用的已上传附件 ID。

    Returns:
        English: A fresh state containing only this turn's user message and reset fields.
        / 中文：仅包含本轮用户消息与重置字段的全新状态。

    Raises:
        ValueError: If a required identifier is blank, not a string, or not a non-empty string.
            / 中文：必填标识为空、不是字符串或不是非空字符串时抛出。
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
        memory_trace={},
        response_source="model",
        context_token_estimate=0,
        response="",
        error=None,
        warnings=[],
        approved_calls=[],
        attachment_ids=list(attachment_ids or []),
        references=list(references or []),
        resolved_references=[],
        used_reference_ids=[],
        model_selection={},
        model_stats=[],
        tool_contract_version=2,
        tool_approval_wait={},
    )
