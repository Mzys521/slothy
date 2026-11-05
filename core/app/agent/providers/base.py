"""应用依赖此接口，隔离具体服务 SDK。 / Application contract independent of vendor SDKs."""

from typing import Protocol

from langchain_core.language_models import BaseChatModel


class ModelProvider(Protocol):
    """提供可注入、可复用的聊天模型。 / Supply a reusable injectable LangChain chat model."""

    def get_chat_model(self) -> BaseChatModel:
        """返回同一实例供同步、异步及流式调用。 / Reuse one instance for sync/async/stream calls."""
        ...
