"""Deterministic guards applied to decision-model output.
 / 对决策模型输出施加确定性护栏。"""

from app.agent.decision.contracts import DecisionOutput, ReasonCode, StepPlan
from app.agent.tools.registry import ToolRegistry
from app.config import AgentSettings, DecisionSettings
from app.memory.semantics import identity_intent


class DecisionGuards:
    """Validate and clamp model proposals against catalog, tools and budgets.
    / 依据目录、工具集合与预算校验并收敛模型提议。"""

    def __init__(
        self,
        registry: ToolRegistry,
        settings: DecisionSettings,
        agent_settings: AgentSettings | None = None,
    ):
        """Bind the authoritative registry and limits. / 绑定权威注册表与限制。

        Args:
            registry: Tool metadata/selection registry. / 工具元数据与选择注册表。
            settings: Decision-layer limits. / 决策层限制。
            agent_settings: Runtime context limits. / 运行时上下文限制。
        """
        self.registry = registry
        self.settings = settings
        self.agent_settings = agent_settings or AgentSettings()

    def apply(self, raw: DecisionOutput, state: dict, mode: str = "chat") -> StepPlan:
        """Clamp one proposal and record changed reasons. / 收敛一次提议并记录原因。

        Args:
            raw: Decision-model output. / 决策模型输出。
            state: Current graph state (user input). / 当前图状态。
            mode: ``chat`` or ``work`` from the graph binding. / 图绑定的聊天或工作模式。

        Returns:
            A safe, executable plan. / 安全可执行的计划。
        """
        pool = self.registry.names(mode)
        reasons: list[ReasonCode] = list(raw.reasons)
        guarded = False

        route = raw.route
        intent = identity_intent(state.get("user_input", ""))
        if route == "identity" and intent is None:
            route = "model"
            guarded = True
        allowed = [name for name in raw.tool_policy.allowed if name in pool]
        if raw.tool_policy.allowed and len(allowed) != len(raw.tool_policy.allowed):
            guarded = True
        if route == "tool" and not allowed:
            route = "model"
            guarded = True
        if not raw.tool_policy.allowed:
            allowed = [] if route != "tool" else pool
        expected = raw.tool_policy.expected_review or any(
            getattr(descriptor, "requires_approval", False)
            for descriptor in self.registry.descriptors(mode)
            if descriptor.name in allowed
        )
        memory_top_k = min(
            max(0, raw.context_plan.memory_top_k), self.agent_settings.memory_top_k
        )
        reference_budget = min(
            max(0, raw.context_plan.reference_budget), self.settings.max_reference_budget
        )
        if (
            memory_top_k != raw.context_plan.memory_top_k
            or reference_budget != raw.context_plan.reference_budget
        ):
            guarded = True
        if guarded and "GUARDED" not in reasons:
            reasons.append("GUARDED")
        if route == "tool" and expected and "TOOL_REVIEW_REQUIRED" not in reasons:
            reasons.append("TOOL_REVIEW_REQUIRED")
        return StepPlan(
            route=route,
            identity_intent=intent if route == "identity" else None,
            tool_policy=raw.tool_policy.model_copy(
                update={
                    "allowed": allowed,
                    "preferred": [name for name in raw.tool_policy.preferred if name in allowed],
                    "expected_review": expected,
                }
            ),
            context_plan=raw.context_plan.model_copy(
                update={"memory_top_k": memory_top_k, "reference_budget": reference_budget}
            ),
            recovery=raw.recovery,
            confidence=raw.confidence,
            reasons=reasons,
            rationale=raw.rationale,
            source="model",
        )
