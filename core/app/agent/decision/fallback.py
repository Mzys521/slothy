"""Rules-only decision fallback, equivalent to the pre-decision behavior.
 / 仅规则的回退决策，行为与引入决策层之前等价。"""

from app.agent.decision.contracts import (
    ContextPlan,
    ReasonCode,
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

    The baseline imposes no tool restriction (``allowed`` and ``max_calls`` are ``None``) so
    the chat model keeps the same autonomy it had before the decision layer. / 基线不施加工具
    限制（``allowed`` 与 ``max_calls`` 为 ``None``），聊天模型保持引入决策层之前的自主权。

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
    intent = identity_intent(state.get("user_input", ""))
    expected = any(
        getattr(descriptor, "requires_approval", False) for descriptor in registry.descriptors(mode)
    )
    return StepPlan(
        route="identity" if intent else "model",
        identity_intent=intent,
        tool_policy=ToolPolicy(
            allowed=None,
            preferred=[],
            max_calls=None,
            expected_review=expected,
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


def fallback_plan(
    state: dict,
    registry: ToolRegistry,
    settings: DecisionSettings,
    agent_settings: AgentSettings | None = None,
    mode: str = "chat",
    *,
    reason: ReasonCode = "FALLBACK_MODEL_UNAVAILABLE",
    rationale: str = "decision model unavailable; chat model decides",
) -> StepPlan:
    """Return autonomy to the chat model after a decision-model failure.
    / 决策模型失败后把自主权交回聊天模型。

    The resulting plan is the pre-decision rules baseline with the ``fallback`` provenance:
    unrestricted tools, default context and no added call quota. / 结果是带 ``fallback`` 来源的
    决策前规则基线：工具不受限、使用默认上下文、不附加调用额度。

    Args:
        state: Current graph state. / 当前图状态。
        registry: Tool registry. / 工具注册表。
        settings: Decision limits. / 决策限制。
        agent_settings: Runtime context defaults. / 运行时上下文默认值。
        mode: ``chat`` or ``work``. / 聊天或工作模式。
        reason: Stable reason code explaining the fallback. / 解释回退的稳定原因码。
        rationale: Short human-readable explanation. / 简短可读的说明。

    Returns:
        A guarded fallback plan that restores chat-model autonomy. / 恢复聊天模型自主权的回退计划。
    """
    plan = rules_plan(state, registry, settings, agent_settings, mode)
    reasons = [reason]
    if "FALLBACK_RULES" not in reasons:
        reasons.append("FALLBACK_RULES")
    return plan.model_copy(
        update={
            "source": "fallback",
            "reasons": reasons,
            "rationale": rationale[:200],
        }
    )
