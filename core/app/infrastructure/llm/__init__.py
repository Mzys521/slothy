"""Configured LangChain model-provider implementations.
/ 已配置的 LangChain 模型 Provider 实现。"""

from app.infrastructure.llm.factory import create_model_provider
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

__all__ = ["OpenAICompatibleProvider", "create_model_provider"]
