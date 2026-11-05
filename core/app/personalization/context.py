"""上下文构建结果，只依赖 Pydantic。 / Context result independent of LangGraph and providers."""

from pydantic import BaseModel, ConfigDict, Field


class ContextBuildResult(BaseModel):
    """构建结果，不是完整用户画像。 / Built reference context, not the full user profile."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # 中文：预算内的参考文本，不是系统指令。
    # English: Budgeted reference text, never system instructions.
    text: str = ""
    # 中文：实际使用的候选记忆 ID，不得添加未召回的 ID。
    # English: Actually used candidate IDs, without inventing unreturned IDs.
    memory_ids: tuple[str, ...] = ()
    # 中文：构建器使用一致估算方法计算的 token 数，Graph 将检查预算。
    # English: Tokens measured by the builder's consistent estimator and checked against the budget.
    token_estimate: int = Field(default=0, ge=0)
