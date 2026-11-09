"""Rules-only decision fallback, equivalent to the pre-decision behavior.
 / 仅规则的回退决策，行为与引入决策层之前等价。"""

from app.agent.decision.contracts import (
    ContextPlan,
    RecoveryPlan,
    StepPlan,
    ToolPolicy,
)
from app.agent.tools.registry import ToolRegistry
from app.config import AgentSettings, DecisionSettings
from app.memory.semantics import identity_intent


def rules_plan(
    state: dict,
    registry: ToolRegistry,
    settings: DecisionSettings,
    agent_settings: AgentSettings | None = None,
    mode: str = "chat",
) -> StepPlan:
    """Build the deterministic fallback plan. / 构建确定性回退计划。

    Args:
        state: Current graph state. / 当前图状态。
        registry: Tool registry. / 工具注册表。
        settings: Decision limits. / 决策限制。
        agent_settings: Runtime context defaults. / 运行时上下文默认值。
        mode: ``chat`` or ``work``. / 聊天或工作模式。

    Returns:
        A rules plan that preserves current behavior. / 保持现状行为的规则计划。
    """
    runtime = agent_settings or AgentSettings()
    names = registry.names(mode)
    intent = identity_intent(state.get("user_input", ""))
    expected = any(
        getattr(descriptor, "requires_approval", False) for descriptor in registry.descriptors(mode)
    )
    return StepPlan(
        route="identity" if intent else "model",
        identity_intent=intent,
        tool_policy=ToolPolicy(
            allowed=names, preferred=[], max_calls=runtime.max_steps, expected_review=expected
        ),
        context_plan=ContextPlan(
            profile_mode="full",
            memory_top_k=runtime.memory_top_k,
            reference_budget=settings.max_reference_budget,
            compaction="none",
        ),
        recovery=RecoveryPlan(stop_condition="answer", retry_budget=1),
        confidence=1.0,
        reasons=["IDENTITY_INTENT"] if intent else ["MODEL_DEFAULT", "FALLBACK_RULES"],
        rationale="deterministic rules fallback",
        source="rules",
    )
