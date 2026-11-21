"""Compile only the online agent graph. / 只编译在线 Agent 图。"""

from collections.abc import Callable, Mapping, Sequence
from functools import partial
from typing import Literal

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.agent.decision.contracts import DECISION_POLICY_VERSION
from app.agent.decision.engine import DecisionEngine
from app.agent.decision.model import DecisionModelClient
from app.agent.graph.edges import route_after_agent
from app.agent.graph.nodes import AgentNodes
from app.agent.graph.review import guarded_tools, review_tools
from app.agent.graph.services import AgentServices
from app.agent.graph.state import AgentState
from app.agent.prompting.plan import digest
from app.agent.prompts.system import BASE_SYSTEM_PROMPT
from app.agent.providers import ModelProvider
from app.agent.tools.gateway import ToolGateway
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.results import tool_error_content
from app.config import AgentSettings, DecisionSettings, PromptSettings

ToolRouting = Literal["none", "direct", "review"]
# Node names shared by the execution graph and the read-only checkpoint projection. /
# 执行图与只读检查点投影共用的节点名称。
GRAPH_NODES = (
    "prepare_run",
    "decide",
    "load_profile",
    "retrieve_memories",
    "build_personalized_context",
    "agent",
    "finalize_response",
)


def wire_online_graph(
    builder: StateGraph,
    nodes: Mapping[str, Callable],
    *,
    tool_routing: ToolRouting,
) -> None:
    """Wire the node names and edges shared by execution and read-only projection. /
    连接执行图与只读投影共用的节点名称与边。

    Both graphs must share this topology because LangGraph recomputes pending tasks and
    interrupts for ``aget_state`` from the saved checkpoint plus the compiled triggers.
    / 两张图必须共用此拓扑：LangGraph 的 ``aget_state`` 依据已存检查点与编译好的触发通道重算
    待执行任务与中断。

    Args:
        builder: Graph builder receiving the nodes and edges. / 接收节点与边的图构建器。
        nodes: Node name to callable mapping. / 节点名称到可调用对象的映射。
        tool_routing: Tool wiring mode: none, direct execution or reviewed execution. /
            工具连接方式：无工具、直接执行或审批后执行。

    Raises:
        ValueError: When required node names are missing. / 缺少必需节点名称时抛出。
    """
    missing = [name for name in GRAPH_NODES if name not in nodes]
    if missing:
        raise ValueError(f"Missing graph nodes: {', '.join(missing)}")
    for name, node in nodes.items():
        builder.add_node(name, node)
    builder.add_edge(START, "prepare_run")
    builder.add_edge("prepare_run", "decide")
    builder.add_edge("decide", "load_profile")
    builder.add_edge("load_profile", "retrieve_memories")
    builder.add_edge("retrieve_memories", "build_personalized_context")
    builder.add_edge("build_personalized_context", "agent")
    if tool_routing == "none":
        builder.add_edge("agent", "finalize_response")
    else:
        builder.add_conditional_edges(
            "agent", route_after_agent, {"tools": "tools", "finalize_response": "finalize_response"}
        )
        if tool_routing == "direct":
            builder.add_edge("tools", "agent")
    builder.add_edge("finalize_response", END)


def build_readonly_graph(
    *, checkpointer: BaseCheckpointSaver, tool_routing: ToolRouting = "review"
) -> CompiledStateGraph:
    """Build a projection graph that reads native checkpoints without binding a model. /
    构建不绑定模型、仅读取原生检查点的投影图。

    The placeholder nodes are never executed; they exist only so LangGraph can resolve the
    stored task and interrupt topology exactly as the execution graph does. / 占位节点永不执行，
    只用于让 LangGraph 按执行图相同的拓扑解析已存任务与中断。

    Args:
        checkpointer: Caller-owned saver holding the real checkpoints. / 持有真实检查点的快照后端。
        tool_routing: Tool wiring mode matching the execution graph. / 与执行图一致的工具连接方式。

    Returns:
        A compiled graph that can only be used for state reads. / 仅用于读取状态的编译图。
    """

    async def placeholder(_state: AgentState) -> dict:
        """Never executed; preserves the saved topology for reads.
        / 永不执行，仅保留读取所需拓扑。"""
        return {}

    builder = StateGraph(AgentState)
    names = [*GRAPH_NODES]
    if tool_routing != "none":
        names.extend(["tools", "review_tools"] if tool_routing == "review" else ["tools"])
    wire_online_graph(
        builder, {name: placeholder for name in names}, tool_routing=tool_routing
    )
    return builder.compile(checkpointer=checkpointer, name="online_agent_readonly")


def build_agent_graph(
    services: AgentServices,
    *,
    settings: AgentSettings | None = None,
    tools: Sequence[BaseTool] = (),
    system_prompt: str = BASE_SYSTEM_PROMPT,
    checkpointer: BaseCheckpointSaver | None = None,
    require_tool_review: bool = False,
    prompt_settings: PromptSettings | None = None,
    tool_registry: ToolRegistry | None = None,
    mode: str | None = None,
    decision_settings: DecisionSettings | None = None,
    decision_provider: ModelProvider | None = None,
    decision_model_client: DecisionModelClient | None = None,
) -> CompiledStateGraph:
    """Build the asynchronous online agent graph. / 创建可异步调用的在线 Agent 图。

    The graph reads profile and memory context, runs the model/tool loop and finalizes one
    turn. It performs no database setup, memory extraction, profile updates or background jobs. /
    中文：图负责读取画像与记忆上下文、运行模型/工具循环并整理一轮回答；不初始化数据库、
    不做记忆提取、画像更新或后台任务。

    Args:
        services: Reusable startup dependencies. / 启动时创建并复用的依赖。
        settings: Runtime limits; environment defaults are loaded when omitted.
            中文：运行限制，省略时读取环境。
        tools: Explicitly registered tools; none by default. / 可选、显式注册的工具，默认没有。
        system_prompt: Trusted base instructions. / 可信基础提示词。
        checkpointer: Caller-owned state backend; required when tool review is enabled.
            中文：可选状态后端，调用方负责线程所有权；启用工具审批时必填。
        require_tool_review: Whether tool batches may pause for user approval.
            中文：工具批次是否需要暂停等待用户审批。
        prompt_settings: Prompt layout and caching settings; environment defaults when omitted.
            中文：提示词布局与缓存设置，省略时读取环境。
        tool_registry: Optional tool metadata; adapts ``tools`` when omitted.
            中文：可选工具元数据，省略时从 ``tools`` 适配。
        mode: Optional ``chat``/``work`` mode forwarded to the tool gateway.
            中文：可选的 chat/work 模式，传给工具网关。
        decision_settings: Decision-layer mode and limits. / 决策层模式与限制。
        decision_provider: Adapter shared with the chat runner for the decision model.
            / 中文：决策模型复用聊天运行器的适配器。
        decision_model_client: Optional injected decision client for tests.
            / 中文：测试可注入的决策客户端。

    Returns:
        English: A compiled graph named ``online_agent`` with the recursion limit preset.
        / 中文：编译后的 ``online_agent`` 图，并预设递归上限。

    Raises:
        ValueError: If ``system_prompt`` is blank, tool names are duplicated, a tool review is
        requested without a checkpointer, or ``settings`` is invalid. / 中文：系统提示词为空、
        工具名重复、需要审批但缺少检查点或配置无效时抛出。
    """
    resolved = settings if settings is not None else AgentSettings()
    if not system_prompt.strip():
        raise ValueError("system_prompt must not be blank")
    registry = tool_registry if tool_registry is not None else ToolRegistry.from_tools(tools)
    selected_tools = tuple(
        sorted(
            (tool for tool in tools if registry.unavailable(tool.name, mode=mode) is None),
            key=lambda tool: tool.name,
        )
    )
    if len({tool.name for tool in selected_tools}) != len(selected_tools):
        raise ValueError("Tool names must be unique")
    model = services.model_provider.get_chat_model()
    nodes = AgentNodes(
        services,
        resolved,
        model,
        system_prompt,
        tools_enabled=bool(selected_tools) or require_tool_review,
        tools=tuple(selected_tools),
        prompt_settings=prompt_settings,
        toolset_hash=digest([convert_to_openai_tool(tool) for tool in selected_tools]),
        cache_protocol=getattr(services.model_provider, "prompt_cache_protocol", "openai"),
    )
    builder = StateGraph(AgentState)
    decision_engine = DecisionEngine(
        decision_settings or DecisionSettings(),
        registry,
        resolved,
        model_client=decision_model_client,
        provider=decision_provider,
    )

    async def decide(state):
        """Ask the decision model for one guarded plan, or skip when disabled.
        请求决策模型生成一个受护栏约束的计划；关闭时跳过。

        Args:
            state: Working state after prepare_run. / prepare_run 之后的工作状态。

        Returns:
            Partial update with the persisted decision fields. / 含持久化决策字段的部分更新。
        """
        plan = await decision_engine.decide(state, mode or "chat")
        if plan is None:
            return {}
        return {
            "decision": plan.model_dump(),
            "decision_reasons": list(plan.reasons),
            "decision_source": plan.source,
            "decision_enforced": plan.enforced,
        }

    async def prepare_run(state):
        """Reset per-run fields and publish the tool contract snapshot.
        重置逐轮字段并发布工具契约快照。

        Args:
            state: Working state after merging the new request. / 合并新请求后的工作状态。

        Returns:
            English: Updates from ``AgentNodes.prepare_run`` plus the tool contract version,
            approval waits, per-tool risks and the decision policy version. / 中文：
            ``AgentNodes.prepare_run`` 的更新，附加工具契约版本、审批等待、各工具风险映射与
            决策策略版本。
        """
        return {
            **await nodes.prepare_run(state),
            "tool_contract_version": 2,
            "tool_approval_wait": {},
            "decision_policy_version": DECISION_POLICY_VERSION,
            "tool_risks": {
                tool.name: str(registry.descriptor(tool.name).risk)
                for tool in registry.execution_tools()
            },
        }

    graph_nodes: dict[str, Callable] = {
        "prepare_run": prepare_run,
        "decide": decide,
        "load_profile": nodes.load_profile,
        "retrieve_memories": nodes.retrieve_memories,
        "build_personalized_context": nodes.build_personalized_context,
        "agent": nodes.agent,
        "finalize_response": nodes.finalize_response,
    }
    routing: ToolRouting = "none"
    # Keep approval nodes available to reject/resume a pending call after a connector
    # is disabled. The current ToolNode returns an error for removed capabilities. /
    # 中文：即使连接器被禁用也保留审批节点，用于拒绝或恢复待处理的调用；当前工具节点会为
    # 已移除的能力返回错误。
    if selected_tools or require_tool_review:
        gateway = ToolGateway(
            registry,
            tool_enabled=services.tool_enabled,
            mode=mode,
            artifact_indexer=services.artifact_indexer,
            artifact_retainer=services.artifact_retainer,
        )
        tool_node = ToolNode(
            registry.execution_tools(),
            handle_tool_errors=tool_error_content,
            awrap_tool_call=gateway.wrap_tool_call,
        )
        if require_tool_review:
            if checkpointer is None:
                raise ValueError("Tool review requires a checkpointer")
            graph_nodes["review_tools"] = partial(review_tools, registry=registry)
            graph_nodes["tools"] = guarded_tools(
                gateway.execute_batch(tool_node), registry, gateway
            )
            routing = "review"
        else:
            graph_nodes["tools"] = gateway.execute_batch(tool_node)
            routing = "direct"
    wire_online_graph(builder, graph_nodes, tool_routing=routing)
    # The step cap is preset here; trusted callers may still override it via RunnableConfig. /
    # 中文：此处预设步数上限，调用方仍可通过标准 RunnableConfig 显式覆盖。
    return builder.compile(checkpointer=checkpointer, name="online_agent").with_config(
        recursion_limit=resolved.max_steps
    )
