"""Policy, budgets, recovery, result contracts and observation for every tool call. /
每次工具调用的策略、预算、恢复、结果契约与可观测性。"""

import asyncio
from collections.abc import Awaitable, Callable
from time import perf_counter
from urllib.parse import quote

from langchain_core.messages import ToolMessage
from langchain_core.runnables import RunnableConfig
from langgraph.config import get_stream_writer
from langgraph.func import task
from pydantic import BaseModel

from app.agent.decision.contracts import DECISION_POLICY_VERSION
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
        artifact_indexer: Callable[..., Awaitable[None]] | None = None,
        artifact_retainer: Callable[[str], Awaitable[set[str]]] | None = None,
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
            artifact_indexer: Optional registration of generated artifacts. /
            可选的生成产物登记回调。
            artifact_retainer: Optional provider of manifest keys retention must keep. /
            可选的保留清单键提供方。
        """
        self.registry = registry
        self.tool_enabled = tool_enabled
        self.mode = mode
        self.artifact_indexer = artifact_indexer
        self.artifact_retainer = artifact_retainer
        self.settings = registry.settings or ToolSettings(_env_file=None)
        self.artifacts = ArtifactStore(registry.settings) if registry.settings else None

    def decision_policy(self, state: dict) -> dict | None:
        """Return the enforceable decision plan for this checkpoint, or ``None``.
        / 返回该检查点可执行的决策计划；不适用时为 ``None``。

        Only enforced plans written with the current policy version constrain tools. Plans
        from shadow runs and older checkpoints stay advisory. / 仅当前策略版本、且已强制执行的
        计划约束工具；影子运行与旧检查点的计划仍只是建议。

        Args:
            state: Current graph state. / 当前图状态。

        Returns:
            The stored plan mapping when it may constrain execution. / 可约束执行的已存计划。
        """
        if state.get("decision_policy_version", 1) < DECISION_POLICY_VERSION:
            return None
        if not state.get("decision_enforced"):
            return None
        plan = state.get("decision")
        return plan if isinstance(plan, dict) else None

    def decision_error(self, call: dict, state: dict) -> ToolResult | None:
        """Reject a call that violates the enforced tool subset or call quota.
        / 拒绝违反已执行工具子集或调用额度的调用。

        The check only narrows execution: the live registry, connector state, approval flow
        and result pairing are handled by the surrounding preflight checks. / 该检查只收窄
        执行范围；实时注册表、连接器状态、审批流程与结果配对仍由外层预检负责。

        Args:
            call: Model tool call with name and args. / 含名称与参数的模型工具调用。
            state: Current graph state with the persisted plan and tool metrics. /
                含已存计划与工具指标的当前图状态。

        Returns:
            Failure envelope when the plan rejects the call, otherwise ``None``. /
            计划拒绝该调用时返回失败信封，否则返回 None。
        """
        plan = self.decision_policy(state)
        if plan is None:
            return None
        policy = plan.get("tool_policy") or {}
        allowed = policy.get("allowed")
        if allowed is not None:
            names = allowed if isinstance(allowed, list) else []
            if call.get("name") not in names:
                return failure(
                    "TOOL_NOT_ALLOWED",
                    "The decision plan does not allow this tool in this run.",
                )
        limit = policy.get("max_calls")
        if isinstance(limit, int) and not isinstance(limit, bool):
            used = sum(item.calls for item in tool_metrics(state))
            if used >= max(0, limit):
                return failure(
                    "BUDGET_EXCEEDED",
                    "This run's decision tool quota is exhausted.",
                )
        return None

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
        planned = self.decision_error(call, state)
        if planned is not None:
            return planned
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
        planned = self.decision_error(call, state)
        if planned is not None:
            return planned
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
                        context,
                        descriptor.name,
                        invoke,
                        version=descriptor.version,
                        retain=await self._retained_manifests(context.user_id),
                    )
                    await self._index_artifacts(context, result)
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

    async def _retained_manifests(self, user: str) -> set[str] | None:
        """Return manifests retention must keep, or None when unknown. /
        返回必须保留的清单标识；无法得知时为 None。

        Args:
            user: Trusted user identifier. / 可信用户标识。

        Returns:
            Referenced manifest keys, or None when the provider is unavailable. /
            被引用的清单标识；提供方不可用时为 None。
        """
        if self.artifact_retainer is None:
            return None
        try:
            return await self.artifact_retainer(user)
        except Exception:
            return None

    async def _index_artifacts(self, context: ToolExecutionContext, result: ToolResult) -> None:
        """Register generated artifacts by their producing session. / 按生成会话登记生成的产物。

        Registration is best effort: a failed index is rebuilt from the completed manifest on
        the next read, and it must never fail an otherwise committed tool call. / 登记是尽力
        而为：失败可由下一次读取从已完成清单补建，绝不让已提交的工具调用失败。

        Args:
            context: Trusted execution identity for this invocation. / 本次调用的可信执行身份。
            result: Normalized tool result carrying artifacts. / 含产物的规范化工具结果。
        """
        if self.artifact_indexer is None or self.artifacts is None or not result.artifacts:
            return
        for artifact in result.artifacts:
            try:
                await self.artifact_indexer(
                    context.user_id,
                    context.thread_id,
                    artifact.id,
                    owner_thread=context.thread_id,
                    workspace_root=str(self.artifacts.workspace),
                    manifest_key=context.key,
                )
            except Exception:
                continue

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

        @task(name="execute_tool_call")
        async def execute_call(call_state: dict):
            # Inherit the task's config, not the parent node's task identity.
            # 使用子任务配置，不能将父节点的任务身份覆盖进来。
            result = await node.ainvoke(call_state)
            return result["messages"]

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
                completed = await execute_call(call_state)
                messages.extend(completed)
                # Cached task results must also appear in resumed live activity.
                # 检查点复用的任务结果也需要重新投影到恢复后的活动轨迹。
                for message in completed:
                    get_stream_writer()(("tool", call["id"], message))
                working = {**state, "messages": [*state["messages"][:-1], *messages, original]}
            return {"messages": messages, "approved_calls": [], "tool_approval_wait": {}}

        return execute
