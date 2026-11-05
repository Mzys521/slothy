"""只编译在线 Agent 图。 / Compile only the online agent graph."""

from collections.abc import Sequence

from langchain_core.tools import BaseTool
from langchain_core.utils.function_calling import convert_to_openai_tool
from langgraph.checkpoint.base import BaseCheckpointSaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph
from langgraph.prebuilt import ToolNode

from app.agent.activity import observe_tool
from app.agent.graph.edges import route_after_agent
from app.agent.graph.nodes import AgentNodes
from app.agent.graph.review import guarded_tools, review_tools
from app.agent.graph.services import AgentServices
from app.agent.graph.state import AgentState
from app.agent.prompting.plan import digest
from app.agent.prompts.system import BASE_SYSTEM_PROMPT
from app.config import AgentSettings, PromptSettings


def build_agent_graph(
    services: AgentServices,
    *,
    settings: AgentSettings | None = None,
    tools: Sequence[BaseTool] = (),
    system_prompt: str = BASE_SYSTEM_PROMPT,
    checkpointer: BaseCheckpointSaver | None = None,
    require_tool_review: bool = False,
    prompt_settings: PromptSettings | None = None,
) -> CompiledStateGraph:
    """创建可异步调用的在线图。 / Build an asynchronous online graph.

    Args:
        services: 启动时创建并复用的依赖。 / Reusable startup dependencies.
        settings: 运行限制，None 时读取环境。 / Runtime limits, loaded from env if omitted.
        tools: 可选、显式注册的工具，默认没有。 / Explicit optional tools; none by default.
        system_prompt: 可信基础提示词。 / Trusted base instructions.
        checkpointer: 可选状态后端，调用方负责线程所有权。 / Optional caller-owned checkpointing.

    中文：使用 ainvoke/astream；没有数据库初始化、记忆提取、画像更新或后台任务图。
    English: Use ainvoke/astream; no DB setup, memory extraction, profile updates or job graph.
    """
    resolved = settings if settings is not None else AgentSettings()
    if not system_prompt.strip():
        raise ValueError("system_prompt must not be blank")
    selected_tools = tuple(sorted(tools, key=lambda tool: tool.name))
    if len({tool.name for tool in selected_tools}) != len(selected_tools):
        raise ValueError("Tool names must be unique")
    model = services.model_provider.get_chat_model()
    runnable = model.bind_tools(selected_tools) if selected_tools else model
    nodes = AgentNodes(
        services,
        resolved,
        runnable,
        system_prompt,
        tools_enabled=bool(selected_tools),
        prompt_settings=prompt_settings,
        toolset_hash=digest([convert_to_openai_tool(tool) for tool in selected_tools]),
        cache_protocol=getattr(services.model_provider, "prompt_cache_protocol", "openai"),
    )
    builder = StateGraph(AgentState)
    builder.add_node("prepare_run", nodes.prepare_run)
    builder.add_node("load_profile", nodes.load_profile)
    builder.add_node("retrieve_memories", nodes.retrieve_memories)
    builder.add_node("build_personalized_context", nodes.build_personalized_context)
    builder.add_node("agent", nodes.agent)
    builder.add_node("finalize_response", nodes.finalize_response)
    builder.add_edge(START, "prepare_run")
    builder.add_edge("prepare_run", "load_profile")
    builder.add_edge("load_profile", "retrieve_memories")
    builder.add_edge("retrieve_memories", "build_personalized_context")
    builder.add_edge("build_personalized_context", "agent")
    if selected_tools:
        tool_node = ToolNode(
            selected_tools,
            handle_tool_errors="工具执行失败。 / Tool execution failed.",
            awrap_tool_call=observe_tool,
        )
        if require_tool_review:
            if checkpointer is None:
                raise ValueError("Tool review requires a checkpointer")
            builder.add_node("review_tools", review_tools)
            builder.add_node("tools", guarded_tools(tool_node))
        else:
            builder.add_node("tools", tool_node)
            builder.add_edge("tools", "agent")
        builder.add_conditional_edges(
            "agent", route_after_agent, {"tools": "tools", "finalize_response": "finalize_response"}
        )
    else:
        builder.add_edge("agent", "finalize_response")
    builder.add_edge("finalize_response", END)
    # 中文：预设步数上限，调用方仍可通过标准 RunnableConfig 显式覆盖。
    # English: Preset the step cap; trusted callers can explicitly override RunnableConfig.
    return builder.compile(checkpointer=checkpointer, name="online_agent").with_config(
        recursion_limit=resolved.max_steps
    )
