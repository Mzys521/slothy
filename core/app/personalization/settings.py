"""Public configuration and runtime capabilities of the memory learning engine.
/ 记忆学习引擎的公开配置与运行时能力。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryConfiguration(BaseModel):
    """User-tunable learning policy shared by the reviewer and planner.
    / 供审查界面与规划器共用的可调学习策略。"""

    model_config = ConfigDict(extra="forbid", frozen=True, str_strip_whitespace=True)

    mode: Literal["off", "shadow", "auto", "review"] = "auto"
    model: str | None = Field(default=None, max_length=256)
    batch_size: int = Field(default=8, strict=True, ge=1, le=64)
    idle_seconds: int = Field(default=60, strict=True, ge=1, le=86400)
    consolidate_every: int = Field(default=20, strict=True, ge=1, le=1000)
    forget_interval_hours: int = Field(default=6, strict=True, ge=1, le=720)
    active_limit: int = Field(default=500, strict=True, ge=1, le=100000)
    auto_confirm_score: float = Field(default=0.8, ge=0, le=1, allow_inf_nan=False)
    observed_min_threads: int = Field(default=2, strict=True, ge=1, le=100)
    episodic_ttl_days: int = Field(default=30, strict=True, ge=1, le=3650)
    fact_ttl_days: int = Field(default=180, strict=True, ge=1, le=3650)
    retention_days: int = Field(default=90, strict=True, ge=1, le=3650)
    daily_token_budget: int = Field(default=50000, strict=True, ge=0, le=10000000)
    strength_c: float = Field(default=4.0, gt=0, le=1000000, allow_inf_nan=False)

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        """Treat a blank model override as unset.

        Args:
            value: Configured model id or None. / 配置的模型 ID 或 None。

        Returns:
            None for blank values, otherwise the stripped model id.
            / 空值返回 None，否则返回去除首尾空白的模型 ID。
        """
        return value or None


class MemoryRuntime(BaseModel):
    """Effective engine state reported to the UI.
    / 上报给界面的实际引擎状态。"""

    engine: Literal["rule_review", "llm_learning"] = "rule_review"
    planner_available: bool = False
    personalization_enabled: bool
    model_available: bool = False
    effective_model: str | None = None
    mode: Literal["off", "shadow", "auto", "review"] = "auto"
    applied_fields: list[str] = Field(default_factory=list)
    reported_tokens: int = 0
    estimated_tokens: int = 0
    budgeted_tokens: int = 0


class MemoryConfigurationPublic(BaseModel):
    """Configuration revision with defaults and runtime state.
    / 含默认值与运行时状态的配置修订。"""

    revision: int
    configuration: MemoryConfiguration
    defaults: MemoryConfiguration
    runtime: MemoryRuntime


class MemoryConfigurationInput(BaseModel):
    """Optimistic-concurrency payload for configuration updates.
    / 配置更新的乐观并发载荷。"""

    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(strict=True, ge=0, lt=2**63 - 1)
    configuration: MemoryConfiguration
