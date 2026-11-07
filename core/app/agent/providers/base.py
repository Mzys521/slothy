"""Chat-model contract independent of vendor SDKs. / 与具体服务 SDK 解耦的聊天模型契约。"""

from typing import Protocol

from langchain_core.language_models import BaseChatModel


class ModelProvider(Protocol):
    """Supply a reusable injectable LangChain chat model. / 提供可注入、可复用的聊天模型。"""

    def get_chat_model(self) -> BaseChatModel:
        """Return one shared model instance for sync, async and streaming calls.

        返回共享模型实例，供同步、异步与流式调用复用。

        Returns:
            English: The configured chat model instance. / 中文：已配置的聊天模型实例。
        """
        ...
