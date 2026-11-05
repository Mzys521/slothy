"""Public configuration contract for the proposed memory planner."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class MemoryConfiguration(BaseModel):
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

    @field_validator("model")
    @classmethod
    def normalize_model(cls, value: str | None) -> str | None:
        return value or None


class MemoryRuntime(BaseModel):
    engine: Literal["rule_review"] = "rule_review"
    planner_available: Literal[False] = False
    personalization_enabled: bool


class MemoryConfigurationPublic(BaseModel):
    revision: int
    configuration: MemoryConfiguration
    defaults: MemoryConfiguration
    runtime: MemoryRuntime


class MemoryConfigurationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expected_revision: int = Field(strict=True, ge=0, lt=2**63 - 1)
    configuration: MemoryConfiguration
