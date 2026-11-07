"""OpenAI-compatible Chat Completions transport for DeepSeek and MiMo.
/ DeepSeek 与 MiMo 的 OpenAI 兼容 Chat Completions 传输层。"""

from copy import deepcopy

import httpx
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_openai import ChatOpenAI

from app.config import LLMSettings


def _normalize_usage(data: dict) -> dict:
    """Normalize vendor cache-usage fields on a raw streaming chunk.

    Args:
        data: Raw response or chunk mapping from the service.
            / 来自服务的原始响应或分块映射。

    Returns:
        The mapping with prompt-cache hits exposed as cached prompt tokens.
        / 将 prompt 缓存命中暴露为缓存 prompt token 的映射。
    """
    usage = data.get("usage") or {}
    cache = usage.get("prompt_cache_hit_tokens")
    if cache is None:
        return data
    details = {**(usage.get("prompt_tokens_details") or {}), "cached_tokens": cache}
    return {**data, "usage": {**usage, "prompt_tokens_details": details}}


class _VendorChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that bridges vendor thinking and reasoning fields.
    / 桥接厂商思考与推理字段的 ChatOpenAI 子类。"""

    vendor: str
    effort: str | None = None

    def bind_tools(self, tools, **kwargs):
        """Bind tools, disabling DeepSeek thinking when no effort is set.

        Args:
            tools: Tools to expose to the model. / 暴露给模型的工具。
            **kwargs: Additional binding options forwarded to LangChain.
                / 转发给 LangChain 的其他绑定选项。

        Returns:
            The tool-bound runnable from the parent class.
            / 父类返回的已绑定工具的可运行对象。
        """
        if self.vendor == "deepseek" and self.effort is None:
            kwargs["extra_body"] = {
                **(self.extra_body or {}),
                **(kwargs.get("extra_body") or {}),
                "thinking": {"type": "disabled"},
            }
        return super().bind_tools(tools, **kwargs)

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """Build the wire payload, echoing prior reasoning for tool turns.

        Args:
            input_: LangChain language-model input being converted.
                / 正在转换的 LangChain 语言模型输入。
            stop: Optional stop sequences. / 可选的停止序列。
            **kwargs: Additional payload options forwarded to LangChain.
                / 转发给 LangChain 的其他载荷选项。

        Returns:
            The chat-completions request payload. / chat completions 请求载荷。
        """
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
        """Build a chat result and keep vendor cache and reasoning data.

        Args:
            response: Raw service response (mapping or SDK model).
                / 原始服务响应（映射或 SDK 模型）。
            generation_info: Optional generation metadata from the parent call.
                / 父类调用传入的可选生成元数据。

        Returns:
            The parsed chat result with cache-read usage and reasoning metadata.
            / 带有缓存读取用量与推理元数据的解析结果。
        """
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
        """Convert one streamed chunk, keeping usage and reasoning deltas.

        Args:
            chunk: Raw streaming chunk mapping. / 原始流式分块映射。
            default_chunk_class: Fallback chunk class from LangChain. / LangChain 的默认分块类。
            base_generation_info: Generation info accumulated so far. / 已累积的生成信息。

        Returns:
            The converted generation chunk, or None when no chunk is produced.
            / 转换后的生成分块；未产生分块时为 None。
        """
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
    """Reuse one ChatOpenAI instance and its connection pools.
    / 复用一个 ChatOpenAI 实例及其连接池。

    Inject HTTP clients for transport configuration or offline tests; the caller owns them.
    / 可注入 HTTP 客户端用于传输配置或离线测试，其生命周期由调用方负责。
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
        """Initialize the adapter and construct the cached chat model.

        Args:
            settings: Validated configuration for the selected service.
                / 所选服务的已校验配置。
            effort: Optional reasoning effort; enables vendor thinking controls.
                / 可选推理档位；启用厂商思考控制。
            http_client: Optional synchronous HTTP client, owned by the caller.
                / 可选同步 HTTP 客户端，由调用方持有。
            http_async_client: Optional asynchronous HTTP client, owned by the caller.
                / 可选异步 HTTP 客户端，由调用方持有。

        Notes:
            DeepSeek keeps the max_tokens field name by merging it after LangChain's
            conversion; the Responses API is never selected.
            / DeepSeek 在 LangChain 转换之后再合并，以保留 max_tokens 字段名；不会切换到
            Responses API。
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
            # English: Merge after LangChain's conversion to preserve DeepSeek's max_tokens field.
            # 中文：通过 SDK 的后合并扩展保留 DeepSeek 的 max_tokens 参数名。
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
            # English: Keep Chat Completions instead of automatically switching to Responses.
            # 中文：固定聊天协议，避免自动切换到 Responses API。
            use_responses_api=False,
            stream_usage=settings.stream_usage,
            http_client=http_client,
            http_async_client=http_async_client,
            **vendor_options,
        )

    def get_chat_model(self) -> BaseChatModel:
        """Return the existing model instance.

        Returns:
            The same chat model for sync, async and streaming calls.
            / 供同步、异步与流式调用复用的同一个聊天模型。
        """
        return self._chat_model
