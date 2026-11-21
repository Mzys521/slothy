"""Decision-model client over the selected model's existing provider adapter.
 / 通过所选模型既有的 Provider 适配器调用决策模型。

The decision model is not a separate protocol. It reuses the same adapter the chat runner
uses (OpenAI-compatible or native Anthropic) and parses one structured plan through the
single :func:`parse_decision_output` path. / 决策模型不是独立协议：它复用聊天运行器所用的同一
适配器（OpenAI 兼容或原生 Anthropic），并通过唯一的 :func:`parse_decision_output` 解析结构化
计划。
"""

import asyncio
import json
import logging

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import ValidationError

from app.agent.decision.contracts import DecisionOutput
from app.agent.decision.prompts import FEW_SHOT, SYSTEM_PROMPT
from app.agent.providers import ModelProvider
from app.config import DecisionSettings, LLMSettings
from app.infrastructure.llm.openai_compatible import OpenAICompatibleProvider

logger = logging.getLogger(__name__)


class DecisionModelError(RuntimeError):
    """Explain why one decision request could not produce a valid plan.
    / 解释一次决策请求为何无法生成有效计划。

    ``reason`` is a stable code such as ``timeout``, ``invalid_json`` or ``empty`` that the
    engine maps to a fallback reason. / ``reason`` 是 timeout、invalid_json、empty 等稳定码，
    由引擎映射为回退原因。
    """

    def __init__(self, reason: str, message: str | None = None) -> None:
        """Record the stable reason and a short diagnostic message. / 记录稳定原因与简短诊断。

        Args:
            reason: Stable failure code. / 稳定失败原因码。
            message: Optional human-readable detail. / 可选的可读说明。
        """
        self.reason = reason
        super().__init__(message or reason)


def _parse_json(text: str) -> dict:
    """Extract one JSON object from a model reply. / 从模型回复中提取一个 JSON 对象。"""
    cleaned = text.strip()
    if cleaned.startswith("```"):
        cleaned = cleaned.removeprefix("```json").removeprefix("```").strip()
        if cleaned.endswith("```"):
            cleaned = cleaned[:-3].strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("decision reply contains no JSON object")
    value = json.loads(cleaned[start : end + 1])
    if not isinstance(value, dict):
        raise ValueError("decision reply is not an object")
    return value


def parse_decision_output(text: str) -> DecisionOutput:
    """Parse one model reply into the shared structured plan contract.
    / 把一次模型回复解析为共享的结构化计划契约。

    Args:
        text: Raw assistant text that should contain one JSON object. / 应包含一个 JSON 对象的
            原始助手文本。

    Returns:
        The validated decision output. / 已校验的决策输出。

    Raises:
        DecisionModelError: When the reply has no valid JSON or does not match the plan
            schema. / 回复没有有效 JSON 或不符合计划 schema 时抛出。
    """
    try:
        value = _parse_json(text)
    except ValueError as error:
        raise DecisionModelError("invalid_json", "decision reply contains no valid JSON") from error
    try:
        return DecisionOutput.model_validate(value)
    except ValidationError as error:
        raise DecisionModelError(
            "invalid_structure", "decision reply does not match the plan schema"
        ) from error


def environment_provider(settings: DecisionSettings) -> ModelProvider | None:
    """Build the OpenAI-compatible adapter for environment-configured decisions.
    / 为环境变量配置的决策模型构建 OpenAI 兼容适配器。

    Endpoint, model and key come from ``DECISION_*`` or ``DASHSCOPE__*``. Independent backend
    runs use this path when no model was selected in the UI. / 端点、模型与密钥来自
    ``DECISION_*`` 或 ``DASHSCOPE__*``；独立后端未在 UI 选择模型时使用此路径。

    Args:
        settings: Decision settings resolved from the environment. / 由环境解析的决策配置。

    Returns:
        A provider adapter, or ``None`` when the credentials are incomplete. /
        适配器；凭据不完整时为 ``None``。
    """
    if not (settings.model and settings.api_key and settings.base_url):
        return None
    try:
        llm = LLMSettings(
            _env_file=None,
            provider="openai_compatible",
            model=settings.model,
            api_key=settings.api_key,
            base_url=settings.base_url,
            timeout_seconds=settings.timeout_seconds,
            max_tokens=settings.max_tokens,
            temperature=0,
        )
    except (TypeError, ValueError):
        return None
    return OpenAICompatibleProvider(llm)


class DecisionModelClient:
    """Structured-output client over an injected provider adapter. / 基于注入适配器的结构化客户端。

    Pass ``provider`` to reuse the catalog model's adapter (including native Anthropic). When
    omitted, an OpenAI-compatible adapter is built from ``DECISION_*``/``DASHSCOPE__*``
    environment values so standalone backends work without the UI selection. / 传入
    ``provider`` 可复用目录模型的适配器（包括原生 Anthropic）；省略时从
    ``DECISION_*``/``DASHSCOPE__*`` 环境值构建 OpenAI 兼容适配器，使独立后端无需 UI 选择即可
    工作。
    """

    def __init__(
        self,
        settings: DecisionSettings,
        provider: ModelProvider | None = None,
    ):
        """Store validated settings and the resolved provider. / 保存已校验配置与解析后的适配器。

        Args:
            settings: Decision-model mode and limits. / 决策模型模式与限制。
            provider: Optional selected-model adapter from the catalog. / 可选目录模型适配器。
        """
        self.settings = settings
        self.provider = provider
        if self.provider is None and settings.mode != "off":
            self.provider = environment_provider(settings)

    @property
    def configured(self) -> bool:
        """Report whether a callable provider adapter exists. / 是否具备可调用的适配器。"""
        return self.provider is not None

    async def decide(self, payload: dict, *, timeout: float) -> DecisionOutput | None:
        """Ask the decision model for one structured plan. / 请求一次结构化决策计划。

        Args:
            payload: Canonical JSON facts for this run. / 本轮规范化 JSON 事实。
            timeout: Client-side timeout in seconds. / 客户端超时秒数。

        Returns:
            Parsed output, or ``None`` when no provider is configured. / 解析后的输出；未配置
            适配器时为 None。

        Raises:
            DecisionModelError: On timeout, transport failure, empty reply or invalid
                structure. / 超时、传输失败、空回复或结构非法时抛出。
        """
        provider = self.provider
        if provider is None:
            return None
        messages = [
            SystemMessage(content=SYSTEM_PROMPT + "\n" + FEW_SHOT),
            HumanMessage(content=json.dumps(payload, ensure_ascii=False, sort_keys=True)),
        ]
        try:
            async with asyncio.timeout(timeout):
                response = await provider.get_chat_model().ainvoke(messages)
        except TimeoutError:
            raise DecisionModelError("timeout", "decision model timed out") from None
        except Exception as error:
            raise DecisionModelError("call_failed", "decision model call failed") from error
        content = getattr(response, "text", "")
        if not isinstance(content, str) or not content.strip():
            raise DecisionModelError("empty", "decision model returned an empty reply")
        return parse_decision_output(content)
