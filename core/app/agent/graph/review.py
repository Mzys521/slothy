"""Review model-proposed tool batches before invoking their executors.
在执行工具前审查模型提议的工具批次。

Gates are policy-driven: tool descriptors decide which calls pause for a user decision, and
the pending batch lives only in the graph checkpoint. / 中文：审批门由策略驱动：工具描述元数据
决定哪些调用暂停等待用户决定，待审批次只保存在图检查点中。
"""

from time import time
from typing import Literal

from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.types import Command, StateSnapshot, interrupt

from app.agent.graph.state import AgentState
from app.agent.tools.registry import ToolRegistry
from app.tools.contracts import ToolMetric, failure

ApprovalPolicy = Literal["ask", "every"]
READ_ONLY_COMMANDS = {"pwd", "ls", "cat", "rg", "echo"}
SAFE_TOOLS = {"calculate", "calculate_financial", "list_uploaded_files", "read_uploaded_file"}


def batch(state: AgentState) -> list[dict]:
    """Return the pending tool calls of the last assistant message.
    返回最后一条助手消息的待处理工具调用。

    Args:
        state: State whose last message must be an ``AIMessage`` with tool calls.
            中文：最后一条消息必须是带工具调用的 ``AIMessage`` 的状态。

    Returns:
        English: The ``tool_calls`` list of that message. / 中文：该消息的 ``tool_calls`` 列表。

    Raises:
        ValueError: If the last message is not an ``AIMessage`` or has no tool calls.
            中文：最后一条消息不是 ``AIMessage`` 或没有工具调用时抛出。
    """
    message = state["messages"][-1]
    if not isinstance(message, AIMessage) or not message.tool_calls:
        raise ValueError("Expected pending tool calls")
    return message.tool_calls


def needs_review(state: AgentState, calls: list[dict], registry: ToolRegistry) -> bool:
    """Decide whether an available call must pause for review. / 判断可用调用是否需要暂停审批。

    Unavailable calls are skipped because the gateway reports them as errors; the ``"every"``
    policy pauses every batch, and otherwise each descriptor decides by risk. / 中文：不可用调用
    会被跳过并由网关返回错误；``"every"`` 策略暂停所有批次，其余情况由各工具描述按风险决定。

    Args:
        state: State carrying ``approval_policy`` and the contract version.
            中文：携带审批策略与契约版本的状态。
        calls: Tool calls proposed by the model. / 模型提议的工具调用。
        registry: Tool metadata registry. / 工具元数据注册表。

    Returns:
        English: ``True`` when at least one available call requires approval.
        / 中文：至少一个可用调用需要审批时返回 ``True``。
    """
    for call in calls:
        descriptor = registry.descriptor(call["name"])
        if registry.unavailable(
            call["name"],
            legacy=state.get("tool_contract_version", 1) < 2,
            arguments=call.get("args", {}),
        ):
            continue
        if state.get("approval_policy") == "every" or descriptor.approval_for(call.get("args", {})):
            return True
    return False


def pending_review(snapshot: StateSnapshot) -> dict | None:
    """Extract the persisted tool-review interrupt, if one is pending.
    提取待处理的工具审批中断（若有）。

    Args:
        snapshot: Current checkpoint snapshot of the thread. / 线程的当前检查点快照。

    Returns:
        English: A dict with ``approval_id``, ``kind``, ``thread_id``, ``run_id``, ``calls`` and
        ``used_memory_ids``, otherwise ``None``. / 中文：包含审批 ID 与调用信息的字典；没有待审
        时返回 ``None``。
    """
    for item in snapshot.interrupts:
        if isinstance(item.value, dict) and item.value.get("kind") == "tool_review":
            return {
                "approval_id": item.id,
                **item.value,
                "used_memory_ids": snapshot.values.get("used_memory_ids", []),
            }
    return None


def review_tools(state: AgentState, registry: ToolRegistry) -> Command[Literal["tools", "agent"]]:
    """Pause the graph for approval, then continue to tools or back to the model.
    暂停图等待审批，随后进入工具或返回模型。

    The registry is bound with ``partial`` at graph build time, so it never enters state. /
    中文：注册表在构图时通过 ``partial`` 绑定，不进入状态。

    Args:
        state: State carrying the pending batch and thread/run identifiers.
            中文：携带待审批次与线程/运行标识的状态。
        registry: Tool metadata registry used to describe each call.
            中文：用于描述每个调用的工具元数据注册表。

    Returns:
        English: Command resuming to ``"tools"`` when approved, or to ``"agent"`` with one
        rejection ``ToolMessage`` per call. / 中文：批准时跳转 ``"tools"``；拒绝时为每个调用写入
        一条拒绝 ``ToolMessage`` 并跳回 ``"agent"``。
    """
    calls = batch(state)
    approved = interrupt(
        {
            "kind": "tool_review",
            "thread_id": state["thread_id"],
            "run_id": state["run_id"],
            "calls": [review_call(registry, call) for call in calls],
        }
    )
    waiting = max(0, round((time() - state.get("tool_review_started_at", time())) * 1000))
    waits = {call["id"]: waiting for call in calls}
    if approved is True:
        return Command(goto="tools", update={"approved_calls": calls, "tool_approval_wait": waits})
    denied = failure("DENIED", "The user declined this tool batch; it was not executed.").wire()
    rejected = [
        ToolMessage(
            content=denied,
            name=call["name"],
            tool_call_id=call["id"],
            status="error",
            additional_kwargs={
                "review_rejected": True,
                "risk": str(registry.descriptor(call["name"]).risk_for(call.get("args", {})))
                if registry.descriptor(call["name"])
                else "execute",
                "tool_metric": ToolMetric(
                    name=call["name"],
                    calls=1,
                    rejected=1,
                    approval_wait_ms=waiting,
                    output_bytes=len(denied.encode()),
                    output_chars=len(denied),
                ).model_dump(),
            },
        )
        for call in calls
    ]
    return Command(goto="agent", update={"messages": rejected, "approved_calls": []})


def review_call(registry: ToolRegistry, call: dict) -> dict:
    """Build one public review entry with name, arguments, risk and a human hint.
    构造单条公开审批条目。

    Args:
        registry: Tool metadata registry. / 工具元数据注册表。
        call: One model tool call with ``name`` and ``args``. / 包含名称与参数的单条模型工具调用。

    Returns:
        English: Public call description rendered in the approval payload.
        中文：用于审批载荷的公开调用描述。
    """
    descriptor = registry.descriptor(call["name"])
    arguments = call.get("args", {})
    hint = "This call cannot execute in the current configuration."
    if descriptor:
        risk = descriptor.risk_for(arguments)
        hint = {
            "read": "Read-only or pure computation.",
            "write": "Writes files in the workspace.",
            "execute": "Executes code with local process policy checks.",
            "network": "Contacts an external service.",
        }[risk]
        if path := arguments.get("path") or arguments.get("filename"):
            hint += f" Target: {str(path)[:500]}."
        if isinstance(arguments.get("content"), str):
            hint += f" UTF-8 bytes: {len(arguments['content'].encode())}."
        if arguments.get("overwrite"):
            hint += " Existing content may be replaced."
    return {
        "name": call["name"],
        "arguments": arguments,
        "risk": str(descriptor.risk_for(arguments)) if descriptor else "execute",
        "review_hint": hint,
    }


def guarded_tools(node, registry: ToolRegistry, gateway):
    """Wrap a tool node so unsafe batches are reviewed before execution.
    包装工具节点，使不安全批次先经审批。

    Args:
        node: Bound tool executor, normally ``gateway.execute_batch(ToolNode)``.
            中文：已绑定的工具执行器，通常是 ``gateway.execute_batch(ToolNode)``。
        registry: Tool metadata registry. / 工具元数据注册表。
        gateway: Execution gateway used for preflight checks. / 用于预检的执行网关。

    Returns:
        English: An async graph node that routes to ``"review_tools"`` or executes and routes
        back to ``"agent"``. / 中文：异步图节点，路由到 ``"review_tools"``，或执行后
        返回 ``"agent"``。
    """

    async def execute(
        state: AgentState, config: RunnableConfig
    ) -> Command[Literal["agent", "review_tools"]]:
        """Execute the current batch, or request review first. / 执行当前批次，或先请求审批。

        Args:
            state: State carrying the pending tool calls and approval records.
                中文：携带待处理工具调用与批准记录的状态。
            config: LangGraph runnable config forwarded to the tool node.
                中文：透传给工具节点的运行配置。

        Returns:
            English: Command routing to ``"review_tools"`` or ``"agent"``. / 中文：路由到
            ``"review_tools"`` 或 ``"agent"`` 的 Command。
        """
        calls = batch(state)
        available = [call for call in calls if gateway.preflight(call, state) is None]
        if needs_review(state, available, registry) and calls != state.get("approved_calls"):
            return Command(goto="review_tools", update={"tool_review_started_at": time()})
        result = await node(state, config)
        return Command(goto="agent", update={**result, "approved_calls": []})

    return execute
