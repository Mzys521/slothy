"""OpenAI-compatible Chat Completions transport for DeepSeek and MiMo.
/ DeepSeek 与 MiMo 的 OpenAI 兼容 Chat Completions 传输层。"""

import json
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


def _native_search_kind(provider: str, model: str) -> str | None:
    """Return the vendor-native web-search mode for a configured model.

    MiMo exposes ``tools:[{"type":"web_search"}]`` on supported Chat Completions models.
    DeepSeek uses the independent Search API tool instead of a model-native search flag;
    its chat requests never receive ``web_search``.

    / 返回模型可用的原生联网搜索模式：MiMo 在受支持 Chat Completions 模型上使用
    ``tools:[{"type":"web_search"}]``；DeepSeek 使用独立 Search API 工具，聊天请求不携带
    ``web_search``。

    Args:
        provider: Selected provider name. / 所选服务名。
        model: Explicit model id. / 显式模型 ID。

    Returns:
        ``"mimo"`` or ``None`` when unsupported. / 支持时返回模式，否则 None。
    """
    lowered = model.casefold()
    if provider == "mimo" and lowered.startswith(
        ("mimo-v2.6-flash", "mimo-v2.6-pro", "mimo-v2.6-pro-ultraspeed")
    ):
        return "mimo"
    return None


def _search_disabled_error(error: Exception) -> bool:
    """Detect a vendor saying its web-search plugin is disabled for this account.

    / 判断错误是否为厂商明确表示当前账号未启用联网搜索插件。

    Args:
        error: Provider exception. / 服务商异常。

    Returns:
        True when the request should be retried without the search tool.
        / 应在去掉搜索工具后重试时返回 True。
    """
    body = getattr(error, "body", None)
    text = json.dumps(body, ensure_ascii=False) if body is not None else str(error)
    return "websearchenabled is false" in text.casefold()


class _VendorChatOpenAI(ChatOpenAI):
    """ChatOpenAI subclass that bridges vendor thinking and reasoning fields.
    / 桥接厂商思考与推理字段的 ChatOpenAI 子类。"""

    vendor: str
    effort: str | None = None
    search_kind: str | None = None

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
        if self.vendor == "deepseek" and self.effort is None and not self.use_responses_api:
            kwargs["extra_body"] = {
                **(self.extra_body or {}),
                **(kwargs.get("extra_body") or {}),
                "thinking": {"type": "disabled"},
            }
        return super().bind_tools(tools, **kwargs)

    async def _agenerate(self, *args, **kwargs):
        """Retry once without native search when the vendor plugin is disabled.
        / 插件未启用时去掉搜索重试一次。"""
        try:
            return await super()._agenerate(*args, **kwargs)
        except Exception as error:
            if not (self.search_kind and _search_disabled_error(error)):
                raise
            self.search_kind = None
            return await super()._agenerate(*args, **kwargs)

    def _generate(self, *args, **kwargs):
        """Synchronous counterpart of the native-search fallback. / 同步请求的搜索降级。"""
        try:
            return super()._generate(*args, **kwargs)
        except Exception as error:
            if not (self.search_kind and _search_disabled_error(error)):
                raise
            self.search_kind = None
            return super()._generate(*args, **kwargs)

    async def _astream(self, *args, **kwargs):
        """Stream with a one-shot fallback when native search is unavailable.
        / 流式请求的搜索降级。"""
        try:
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk
        except Exception as error:
            if not (self.search_kind and _search_disabled_error(error)):
                raise
            self.search_kind = None
            async for chunk in super()._astream(*args, **kwargs):
                yield chunk

    def _get_request_payload(self, input_, *, stop=None, **kwargs):
        """Build the wire payload, echoing prior reasoning for tool turns.

        Native web search is appended as a server-side tool for supported models only.

        Args:
            input_: LangChain language-model input being converted.
                / 正在转换的 LangChain 语言模型输入。
            stop: Optional stop sequences. / 可选的停止序列。
            **kwargs: Additional payload options forwarded to LangChain.
                / 转发给 LangChain 的其他载荷选项。

        Returns:
            The chat-completions or Responses request payload. / 聊天或 Responses 请求载荷。
        """
        payload = super()._get_request_payload(input_, stop=stop, **kwargs)
        if self.search_kind:
            tools = list(payload.get("tools") or [])
            if not any(
                isinstance(tool, dict) and tool.get("type") == "web_search" for tool in tools
            ):
                tools.append(
                    {"type": "web_search", "max_keyword": 3, "force_search": False, "limit": 1}
                    if self.search_kind == "mimo"
                    else {"type": "web_search"}
                )
            payload["tools"] = tools
            payload.setdefault("tool_choice", "auto")
        if self.use_responses_api:
            return payload
        thinking = (payload.get("extra_body") or {}).get("thinking")
        if not payload.get("tools") or thinking == {"type": "disabled"}:
            return payload
        history = self._convert_input(input_).to_messages()
        for message, wire in zip(history, payload["messages"], strict=True):
            if isinstance(message, AIMessage):
                if self.vendor == "deepseek" and not message.tool_calls:
                    # Final-answer reasoning stays display-only; tool rounds need passback.
                    # 最终回答的推理仅供展示；只有工具往返需要按协议回传。
                    wire.pop("reasoning_content", None)
                    continue
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
        if self.use_responses_api:
            return super()._create_chat_result(response, generation_info)
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
        ) if not self.use_responses_api else super()._convert_chunk_to_generation_chunk(
            chunk, default_chunk_class, base_generation_info,
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
        search_kind = (
            _native_search_kind(settings.provider, settings.model)
            if settings.web_search != "off"
            else None
        )
        responses_api = search_kind == "deepseek"
        if settings.provider in ("deepseek", "mimo"):
            vendor_options = {
                "vendor": settings.provider,
                "effort": effort,
                "search_kind": search_kind,
            }
            if effort is not None and not responses_api:
                extra_body["thinking"] = {"type": "disabled" if effort == "none" else "enabled"}
                if settings.provider == "deepseek":
                    extra_body["reasoning_effort"] = effort
        max_completion_tokens = settings.max_tokens
        if (
            settings.provider == "deepseek"
            and settings.max_tokens is not None
            and not responses_api
        ):
            # English: Merge after LangChain's conversion to preserve DeepSeek's max_tokens field.
            # 中文：通过 SDK 的后合并扩展保留 DeepSeek 的 max_tokens 参数名。
            extra_body["max_tokens"] = settings.max_tokens
            max_completion_tokens = None
        if responses_api:
            # Responses API uses its own payload/field names; chat-only extensions stay out.
            # Responses API 使用自有字段；不发送聊天专用扩展。
            extra_body = {}

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
            # English: Responses is selected only for DeepSeek native web search; otherwise
            # Chat Completions remains the stable default.
            # 中文：仅 DeepSeek 原生联网搜索使用 Responses；其余保持 Chat Completions。
            use_responses_api=responses_api,
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
