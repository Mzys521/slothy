"""Structured contracts for the model-driven decision layer.
 / 决策模型驱动的决策层结构化契约。"""

from typing import Literal

from pydantic import BaseModel, Field

Route = Literal["identity", "model", "tool"]
ProfileMode = Literal["full", "compact", "off"]
Compaction = Literal["none", "batch", "aggressive"]
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
]


class ToolPolicy(BaseModel):
    """Tools the decision model proposes for this run.
    / 决策模型为本轮建议的工具集合。"""

    model_config = {"extra": "ignore"}

    allowed: list[str] = Field(default_factory=list)
    preferred: list[str] = Field(default_factory=list)
    max_calls: int = Field(default=8, ge=0, le=64)
    expected_review: bool = False


class ContextPlan(BaseModel):
    """Context strategy for this run. / 本轮上下文策略。"""

    model_config = {"extra": "ignore"}

    profile_mode: ProfileMode = "full"
    memory_top_k: int = Field(default=8, ge=0, le=32)
    reference_budget: int = Field(default=12000, ge=0, le=100000)
    compaction: Compaction = "none"


class RecoveryPlan(BaseModel):
    """Stop/retry expectations proposed by the decision model.
    / 决策模型建议的停止/重试策略。"""

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
    """Guarded, executable plan persisted in graph state. / 经护栏校验、写入图状态的可执行计划。"""

    source: Literal["model", "rules", "fallback"] = "rules"
    enforced: bool = False
