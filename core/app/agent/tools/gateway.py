"""Policy, budgets, recovery, result contracts and observation for every tool call. /
每次工具调用的策略、预算、恢复、结果契约与可观测性。"""

import asyncio
from collections.abc import Callable
from time import perf_counter
from urllib.parse import quote

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from pydantic import BaseModel

from app.agent.tools.descriptor import ToolMode
from app.agent.tools.registry import ToolRegistry
from app.agent.tools.results import error_result, normalize_result
from app.agent.tools.telemetry import current_tool_messages, tool_metrics
from app.config import ToolSettings
from app.tools.artifacts import ArtifactStore, invocation_key
from app.tools.context import ToolExecutionContext, execution_context
from app.tools.contracts import ToolMetric, ToolResult, failure


async def observe_tool(request, execute):
    """
    Stream tool progress and mark approved review calls. / 流式输出工具进度并标记已审批调用。

    Args:
        request: LangGraph tool call request carrying state and call data. /
            携带状态与调用数据的 LangGraph 工具请求。
        execute: Callable that performs the wrapped tool call. /
        执行被包装工具调用的可调用对象。

    Returns:
        The resulting ToolMessage, annotated when the call was approved. /
        工具结果消息，已审批调用会附加标记。
    """
    write = get_stream_writer()
    write(("tool", request.tool_call["id"], None))
    result = await execute(request)
    if isinstance(result, ToolMessage):
        if request.tool_call in request.state.get("approved_calls", []):
            result = result.model_copy(
                update={"additional_kwargs": {**result.additional_kwargs, "review_approved": True}}
            )
        write(("tool", request.tool_call["id"], result))
    return result


class ToolGateway:
    """Enforce availability, budgets, approval and result contracts for tool calls. /
    为工具调用执行可用性、预算、审批与结果契约校验。"""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        tool_enabled: Callable[[str], bool] | None = None,
        mode: ToolMode | None = None,
    ) -> None:
        """
        Configure the gateway for one compiled graph. / 为一张编译后的图配置网关。

        Args:
            registry: Tool catalog supplying descriptors and settings. /
            提供描述符与配置的工具目录。
            tool_enabled: Optional per-tool enablement check for connectors. /
            可选的连接器逐工具启用检查。
            mode: Chat or work mode used for availability checks. / 用于可用性检查的 chat 或
            work 模式。
        """
        self.registry = registry
        self.tool_enabled = tool_enabled
        self.mode = mode
        self.settings = registry.settings or ToolSettings(_env_file=None)
        self.artifacts = ArtifactStore(registry.settings) if registry.settings else None

    def preflight(self, call: dict, state: dict) -> ToolResult | None:
        """
        Reject unknown, unavailable or disabled tools before execution. /
        执行前拒绝未知、不可用或已禁用工具。

        Args:
            call: Model tool call with name and args. / 含名称与参数的模型工具调用。
            state: Current graph state carrying the contract version. /
            含工具契约版本的当前图状态。

        Returns:
            Failure envelope when rejected, otherwise None. / 拒绝时返回失败信封，否则返回
            None。
        """
        name = call["name"]
        reason = self.registry.unavailable(
            name,
            mode=self.mode,
            legacy=state.get("tool_contract_version", 1) < 2,
            arguments=call.get("args", {}),
        )
        if reason:
            return failure(reason, "Tool is unknown or unavailable in this mode.")
        if self.tool_enabled and not self.tool_enabled(name):
            return failure("CONNECTOR_DISABLED", "This tool has been disabled in Connectors.")
        try:
            tool = self.registry.tool(name)
            schema = tool.tool_call_schema if tool else None
            if isinstance(schema, type) and issubclass(schema, BaseModel):
                schema.model_validate(call.get("args", {}))
            elif isinstance(schema, dict):
                from jsonschema import Draft202012Validator

                Draft202012Validator(schema).validate(call.get("args", {}))
        except Exception as error:
            return error_result(error)
        return None

    def budget_error(self, call: dict, state: dict) -> ToolResult | None:
        """
        Check per-run counts, output, duration and failure streaks. /
        检查单次运行的调用数、输出、时长与连续失败。

        Args:
            call: Model tool call being checked. / 正在检查的模型工具调用。
            state: Current graph state with prior tool messages. /
            含先前工具消息的当前图状态。

        Returns:
            Failure envelope when a budget is exhausted, otherwise None. /
            预算耗尽时返回失败信封，否则返回 None。
        """
        metrics = tool_metrics(state)
        if (
            sum(item.calls for item in metrics) >= self.settings.max_calls_per_run
            or sum(item.output_chars for item in metrics) >= self.settings.max_total_output_chars
            or sum(item.duration_ms for item in metrics)
            >= self.settings.max_total_duration_seconds * 1000
        ):
            return failure("BUDGET_EXCEEDED", "This run's tool budget is exhausted.")
        streak = 0
        for message in reversed(current_tool_messages(state)):
            if message.name != call["name"]:
                continue
            if message.status != "error" or message.additional_kwargs.get("review_rejected"):
                break
            streak += 1
        if streak >= self.settings.failure_streak_limit:
            return failure(
                "BUDGET_EXCEEDED",
                "This tool reached the failure limit for this run.",
                hint="Use a different approach or start a new request.",
            )
        return None

    async def wrap_tool_call(self, request, execute):
        """
        Validate, time, journal and normalize one tool call. /
        校验、计时、记录并规范化一次工具调用。

        Args:
            request: LangGraph tool call request with state and call data. /
                含状态与调用数据的 LangGraph 工具请求。
            execute: Callable that performs the underlying tool work. /
            执行底层工具工作的可调用对象。

        Returns:
            Normalized ToolMessage with metric, risk and approval metadata. /
            含指标、风险与审批元数据的规范化 ToolMessage。
        """
        call, state = request.tool_call, request.state
        descriptor = self.registry.descriptor(call["name"])
        writer = get_stream_writer()
        writer(("tool", call["id"], None))
        started = perf_counter()
        result = self.preflight(call, state) or self.budget_error(call, state)
        if result is not None and result.error and result.error.code == "INVALID_ARGS":
            result = self.budget_error(call, state) or result
        reused, journal_ms = False, 0
        context = ToolExecutionContext(
            invocation_key(state, call),
            state.get("user_id", ""),
            state.get("thread_id", ""),
            state.get("run_id", ""),
        )
        token = execution_context.set(context)
        try:
            if result is None:
                # English: Validate model-visible arguments before creating a side-effect journal.
                # 中文：创建副作用日志前先校验模型可见参数。
                schema = request.tool.tool_call_schema if request.tool else None
                if isinstance(schema, type) and issubclass(schema, BaseModel):
                    schema.model_validate(call.get("args", {}))
                elif isinstance(schema, dict):
                    from jsonschema import Draft202012Validator

                    Draft202012Validator(schema).validate(call.get("args", {}))

                spent = sum(item.duration_ms for item in tool_metrics(state)) / 1000
                remaining = max(0.001, self.settings.max_total_duration_seconds - spent)
                timeout = min(descriptor.timeout_seconds or remaining, remaining)

                async def invoke():
                    """
                    Run the tool under its timeout and normalize the result. /
                    在超时限制下运行工具并规范化结果。

                    Returns:
                        Normalized ToolResult, including converted failures. /
                        规范化 ToolResult，包括转换后的失败。
                    """
                    try:
                        async with asyncio.timeout(timeout):
                            message = await execute(request)
                        normalized = normalize_result(
                            message, descriptor, self.registry.settings, context.key
                        )
                        for artifact in normalized.artifacts:
                            artifact.url = (
                                f"/api/v1/threads/{quote(context.thread_id, safe='')}/artifacts/"
                                + artifact.id
                            )
                        return normalized
                    except TimeoutError:
                        return failure(
                            "BUDGET_EXCEEDED" if timeout == remaining else "TIMEOUT",
                            "Tool execution timed out.",
                            retryable=True,
                        )
                    except Exception as error:
                        return error_result(error)

                if (
                    descriptor.journaled
                    and self.artifacts
                    and descriptor.risk_for(call.get("args", {})) != "read"
                ):
                    result, reused, journal_ms = await self.artifacts.execute(
                        context, descriptor.name, invoke, version=descriptor.version
                    )
                else:
                    result = await invoke()
        except Exception as error:
            result = error_result(error)
        finally:
            execution_context.reset(token)
        if result.error and result.error.code == "RECOVERY_REQUIRED" and self.artifacts:
            result.data = {
                "invocation_id": context.key,
                "manifest": (self.artifacts.root / context.key / "manifest.json")
                .relative_to(self.artifacts.workspace)
                .as_posix(),
            }
        cap = descriptor.max_output_chars if descriptor else self.settings.max_output_chars
        text = result.wire()
        if cap and len(text) > cap:
            result = failure("OUTPUT_TOO_LARGE", "Tool output exceeds its character limit.")
            text = result.wire()
        previous_chars = sum(item.output_chars for item in tool_metrics(state))
        if result.ok and previous_chars + len(text) > self.settings.max_total_output_chars:
            result = failure("BUDGET_EXCEEDED", "This run's output budget is exhausted.")
            text = result.wire()
        elapsed = journal_ms if reused else round((perf_counter() - started) * 1000)
        metric = ToolMetric(
            name=call["name"],
            calls=1,
            ok=int(result.ok),
            errors=int(not result.ok),
            approval_wait_ms=state.get("tool_approval_wait", {}).get(call["id"], 0),
            duration_ms=elapsed,
            output_bytes=len(text.encode()),
            output_chars=len(text),
            artifacts=len(result.artifacts),
            timed_out=int(bool(result.error and result.error.code == "TIMEOUT")),
            reused=int(reused),
        )
        approved = call in state.get("approved_calls", [])
        message = ToolMessage(
            content=text,
            tool_call_id=call["id"],
            name=call["name"],
            status="success" if result.ok else "error",
            additional_kwargs={
                "tool_metric": metric.model_dump(),
                "risk": str(descriptor.risk_for(call.get("args", {}))) if descriptor else "execute",
                **({"review_approved": True} if approved else {}),
            },
        )
        writer(("tool", call["id"], message))
        return message

    def execute_batch(self, node):
        """
        Build a node executor that runs tool calls one at a time. /
        构建逐个执行工具调用的节点执行器。

        Args:
            node: Tool node invoked once per individual call. /
            每个单独调用执行一次的工具节点。

        Returns:
            Coroutine function suitable as a LangGraph tool node. / 适合作为 LangGraph
            工具节点的协程函数。
        """

        async def execute(state: dict, config: RunnableConfig):
            """
            Execute each tool call in order and merge result messages. /
            按顺序执行每个工具调用并合并结果消息。

            Args:
                state: Graph state ending with the pending model tool calls. /
                以待处理模型工具调用结尾的图状态。
                config: Runnable config forwarded to the tool node. / 转发给工具节点的
                Runnable 配置。

            Returns:
                Update with tool messages and cleared approval bookkeeping. /
                含工具消息并清空审批记录的更新。
            """
            original = state["messages"][-1]
            messages, working = [], dict(state)
            for call in original.tool_calls:
                pending = original.model_copy(update={"tool_calls": [call]})
                call_state = {**working, "messages": [*working["messages"][:-1], pending]}
                result = await node.ainvoke(call_state, config)
                messages.extend(result["messages"])
                working = {**state, "messages": [*state["messages"][:-1], *messages, original]}
            return {"messages": messages, "approved_calls": [], "tool_approval_wait": {}}

        return execute
