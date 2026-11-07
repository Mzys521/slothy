"""Model-provider creation at application startup. / 应用启动时创建模型服务。"""

from app.agent.providers import ModelProvider
from app.config import LLMSettings
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider


def create_model_provider(settings: LLMSettings | None = None) -> ModelProvider:
    """Build the reusable model provider.

    Call once at startup and inject the result into services or graphs; pass explicit
    settings to build several providers. / 在启动时调用一次并把结果注入服务或图；显式传入
    配置可创建多个 Provider。

    Args:
        settings: Validated settings; loaded from the environment when None.
            / 已校验配置；为 None 时读取环境。

    Returns:
        An OpenAI-compatible provider owning one reusable chat model.
        / 持有单个可复用聊天模型的 OpenAI 兼容 Provider。
    """
    return OpenAICompatibleProvider(settings if settings is not None else LLMSettings())
