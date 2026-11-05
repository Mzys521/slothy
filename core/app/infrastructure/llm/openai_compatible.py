"""DeepSeek/MiMo 聊天协议适配。 / OpenAI-compatible Chat Completions transport."""

from copy import deepcopy

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from app.config import LLMSettings


def _normalize_usage(data: dict) -> dict:
    usage = data.get("usage") or {}
    cache = usage.get("prompt_cache_hit_tokens")
    if cache is None:
        return data
    details = {**(usage.get("prompt_tokens_details") or {}), "cached_tokens": cache}
    return {**data, "usage": {**usage, "prompt_tokens_details": details}}


class _VendorChatOpenAI(ChatOpenAI):
    vendor: str
    effort: str | None = None

    def bind_tools(self, tools, **kwargs):
        if self.vendor == "deepseek" and self.effort is None:
            kwargs["extra_body"] = {
                **(self.extra_body or {}),
                **(kwargs.get("extra_body") or {}),
                "thinking": {"type": "disabled"},
            }
        return super().bind_tools(tools, **kwargs)

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        thinking = (payload.get("extra_body") or {}).get("thinking")
        if not payload.get("tools") or thinking == {"type": "disabled"}:
            return payload
        history = self._convert_input(input_).to_messages()
        for message, wire in zip(history, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                wire["reasoning_content"] = message.additional_kwargs.get(
                    f"{self.vendor}_reasoning_content", ""
                )
        return payload

    def _create_chat_result(self, response, generation_info=None):
        result = super()._create_chat_result(response, generation_info)
        raw = response if isinstance(response, dict) else response.model_dump(warnings=False)
        cache = (raw.get("usage") or {}).get("prompt_cache_hit_tokens")
        for choice, generation in zip(raw["choices"], result.generations, strict=True):
            message = generation.message
            if isinstance(message, AIMessage) and cache is not None and message.usage_metadata:
                message.usage_metadata.setdefault("input_token_details", {})["cache_read"] = cache
            reasoning = choice["message"].get("reasoning_content")
            if isinstance(reasoning, str):
                message.additional_kwargs[f"{self.vendor}_reasoning_content"] = reasoning
        return result

    def _convert_chunk_to_generation_chunk(self, chunk, default_chunk_class, base_generation_info):
        result = super()._convert_chunk_to_generation_chunk(
            _normalize_usage(chunk), default_chunk_class, base_generation_info,
        )
        if result and isinstance(result.message, AIMessageChunk):
            choices = chunk.get("choices") or []
            delta = (choices[0].get("delta") or {}) if choices else {}
            reasoning = delta.get("reasoning_content")
            if isinstance(reasoning, str):
                result.message.additional_kwargs[f"{self.vendor}_reasoning_content"] = reasoning
        return result


class OpenAICompatibleProvider:
    """复用一个模型及其连接池。 / Reuse one ChatOpenAI instance and its connection pools.

    中文：可注入 HTTP 客户端用于传输配置或离线测试，注入方负责其生命周期。
    English: Inject HTTP clients for transport configuration/tests; callers own supplied clients.
    """

    prompt_cache_protocol = "openai"

    def __init__(
        self,
        settings: LLMSettings,
        *,
        effort: str | None = None,
        http_client: httpx.Client | None = None,
        http_async_client: httpx.AsyncClient | None = None,
    ) -> None:
        """初始化适配器。 / Initialize the adapter.

        Args:
            settings: 所选服务的已校验配置。 / Validated configuration for the selected service.
            http_client: 可选同步 HTTP 客户端。 / Optional synchronous HTTP client.
            http_async_client: 可选异步 HTTP 客户端。 / Optional asynchronous HTTP client.
        """
        extra_body = deepcopy(settings.extra_body)
        vendor_options = {}
        if settings.provider in ("deepseek", "mimo"):
            vendor_options = {"vendor": settings.provider, "effort": effort}
            if effort is not None:
                extra_body["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
                if settings.provider == "deepseek":
                    extra_body["reasoning_effort"] = effort
        max_completion_tokens = settings.max_tokens
        if settings.provider == "deepseek" and settings.max_tokens is not None:
            # 中文：通过 SDK 的后合并扩展保留 DeepSeek 的 max_tokens 参数名。
            # English: Merge after LangChain's conversion to preserve DeepSeek's max_tokens field.
            extra_body["max_tokens"] = settings.max_tokens
            max_completion_tokens = None

        model_class = _VendorChatOpenAI if vendor_options else ChatOpenAI
        self._chat_model = model_class(
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.resolved_base_url,
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
            temperature=settings.temperature,
            max_completion_tokens=max_completion_tokens,
            extra_body=extra_body or None,
            # 中文：固定聊天协议，避免自动切换到 Responses API。
            # English: Keep Chat Completions instead of automatically switching to Responses.
            use_responses_api=False,
            stream_usage=settings.stream_usage,
            http_client=http_client,
            http_async_client=http_async_client,
            **vendor_options,
        )

    def get_chat_model(self) -> BaseChatModel:
        """返回已创建的模型实例。 / Return the existing model instance."""
        return self._chat_model
