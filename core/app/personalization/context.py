"""Context build result that depends only on Pydantic.
/ 仅依赖 Pydantic 的上下文构建结果。"""

from pydantic import BaseModel, ConfigDict, Field


class ContextBuildResult(BaseModel):
    """Built reference context, not the full user profile.
    / 构建出的参考上下文，不是完整用户画像。"""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # English: Budgeted reference text, never system instructions.
    # 中文：预算内的参考文本，不是系统指令。
    text: str = ""
    # English: Actually used candidate IDs, without inventing unreturned IDs.
    # 中文：实际使用的候选记忆 ID，不得添加未召回的 ID。
    memory_ids: tuple[str, ...] = ()
    # English: Tokens measured by the builder's consistent estimator, checked against the budget.
    # 中文：构建器使用一致估算方法计算的 token 数，Graph 将检查预算。
    token_estimate: int = Field(default=0, ge=0)
