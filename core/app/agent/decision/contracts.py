"""Structured contracts for the model-driven decision layer.
 / 决策模型驱动的决策层结构化契约。"""

from typing import Literal

from pydantic import BaseModel, Field

Route = Literal["identity", "model", "tool"]
ProfileMode = Literal["full", "compact", "off"]
Compaction = Literal["none", "batch", "aggressive"]
# Plan provenance and fallback results share one vocabulary. ``fallback`` always means the
# decision model could not produce a plan and the chat model keeps autonomy; ``rules`` is the
# deterministic pre-decision baseline used when no decision model is configured. /
# 计划来源与回退结果共用同一词表：fallback 表示决策模型失败并把自主权交回聊天模型；rules 是
# 未配置决策模型时的确定性基线。
PlanSource = Literal["model", "rules", "fallback"]
# Bumped when plan interpretation changes. Checkpoints written before this version keep the
# previous behavior (plans are recorded but cannot restrict tools or context). /
# 计划解释变化时递增。此前写入的检查点沿用旧行为（计划只记录，不限制工具或上下文）。
DECISION_POLICY_VERSION = 2
ReasonCode = Literal[
    "IDENTITY_INTENT",
    "IDENTITY_AMBIGUOUS",
    "MODEL_DEFAULT",
    "MODEL_EXPLICIT",
    "EFFORT_DEFAULTED",
    "TOOL_ALLOWED",
    "TOOL_FORBIDDEN",
    "TOOL_REVIEW_REQUIRED",
    "CONTEXT_COMPACTED",
    "BUDGET_CLAMPED",
    "GUARDED",
    "FALLBACK_RULES",
    "FALLBACK_SAFE",
    "FALLBACK_MODEL_UNAVAILABLE",
    "FALLBACK_INVALID_OUTPUT",
]


class ToolPolicy(BaseModel):
    """Tools the decision model proposes for this run. / 决策模型为本轮建议的工具集合。

    ``allowed`` is tri-state: ``None`` imposes no restriction (the chat model keeps
    autonomy), ``[]`` explicitly allows nothing (the safe default for raw model output), and
    a non-empty list allows only those names. ``max_calls`` is the per-run call quota;
    ``None`` adds no decision-layer quota and leaves only the deployment tool budget in
    force. / ``allowed`` 为三态：``None`` 表示不施加限制（聊天模型保持自主），``[]`` 表示不
    允许任何工具（模型原始输出的安全默认值），非空列表仅允许其中名称。``max_calls`` 是本轮
    调用额度；``None`` 表示不附加决策层额度，仅受部署工具预算约束。
    """

    model_config = {"extra": "ignore"}

    allowed: list[str] | None = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    max_calls: int | None = Field(default=8, ge=0, le=64)
    expected_review: bool = False


class ContextPlan(BaseModel):
    """Context strategy for this run. / 本轮上下文策略。"""

    model_config = {"extra": "ignore"}

    profile_mode: ProfileMode = "full"
    memory_top_k: int = Field(default=8, ge=0, le=32)
    reference_budget: int = Field(default=12000, ge=0, le=100000)
    compaction: Compaction = "none"


class RecoveryPlan(BaseModel):
    """Advisory stop/retry expectations proposed by the decision model.

    Recovery hints are suggestions for the chat model only. The graph keeps its own step,
    timeout and tool budgets authoritative and never changes execution because of this
    object. / 恢复提示仅为给聊天模型的建议；图的步数、超时与工具预算仍然权威，本对象不会
    改变执行。
    """

    model_config = {"extra": "ignore"}

    stop_condition: Literal["answer", "max_steps", "error"] = "answer"
    retry_budget: int = Field(default=1, ge=0, le=5)


class DecisionOutput(BaseModel):
    """Raw model output before deterministic guards. / 护栏前的模型原始输出。"""

    model_config = {"extra": "ignore"}

    route: Route = "model"
    identity_intent: str | None = None
    tool_policy: ToolPolicy = Field(default_factory=ToolPolicy)
    context_plan: ContextPlan = Field(default_factory=ContextPlan)
    recovery: RecoveryPlan = Field(default_factory=RecoveryPlan)
    confidence: float = Field(default=0.0, ge=0, le=1)
    reasons: list[ReasonCode] = Field(default_factory=list)
    rationale: str = Field(default="", max_length=200)


class StepPlan(DecisionOutput):
    """Guarded, executable plan persisted in graph state. / 经护栏校验、写入图状态的可执行计划。

    Plans are advisory metrics in ``shadow`` mode and only constrain execution when
    ``enforced`` is true on a checkpoint written with ``DECISION_POLICY_VERSION``. ``source``
    is one of ``model``, ``rules`` or ``fallback``; a ``fallback`` plan restores the chat
    model's autonomy (unrestricted tools and default context) after a decision-model failure.
    ``recovery`` is documentation for the chat model, not an execution contract; the graph's
    own step, timeout and tool budgets remain authoritative. / 计划在 shadow 模式下仅作为指标，
    只有使用 ``DECISION_POLICY_VERSION`` 写入的检查点且 ``enforced`` 为真时才约束执行；
    ``source`` 为 model/rules/fallback；决策模型失败后的 fallback 计划恢复聊天模型自主权
    （工具不受限、上下文默认值）。``recovery`` 只是给聊天模型的说明，不是执行契约，图的步数、
    超时与工具预算仍然权威。
    """

    source: PlanSource = "rules"
    enforced: bool = False
