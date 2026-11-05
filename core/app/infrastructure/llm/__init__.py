"""Configured LangChain model-provider implementations."""

from app.infrastructure.llm.factory import create_model_provider
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

__all__ = ["OpenAICompatibleProvider", "create_model_provider"]
