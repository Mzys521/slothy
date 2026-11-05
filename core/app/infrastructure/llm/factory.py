"""应用启动时创建模型服务。 / Model-provider creation at application startup."""

from app.agent.providers import ModelProvider
from app.config import LLMSettings
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider


def create_model_provider(settings: LLMSettings | None = None) -> ModelProvider:
    """创建模型 Provider。 / Build a model provider.

    Args:
        settings: 已校验配置；None 时读取环境。 / Validated settings; None loads the environment.

    中文：在启动时调用一次后注入服务或 Graph；可显式传入不同配置创建多个模型。
    English: Create at startup and inject into services/graphs; explicit settings allow many models.
    """
    return OpenAICompatibleProvider(settings if settings is not None else LLMSettings())
