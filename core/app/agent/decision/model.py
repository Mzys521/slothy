"""Decision-model client using the configured OpenAI-compatible endpoint.
 / 使用已配置的 OpenAI 兼容端点调用决策模型。"""

import json
import logging

import httpx

from app.agent.decision.contracts import DecisionOutput
from app.agent.decision.prompts import FEW_SHOT, SYSTEM_PROMPT
from app.config import DecisionSettings

logger = logging.getLogger(__name__)


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


class DecisionModelClient:
    """Thin structured-output client; returns ``None`` when unconfigured.
    / 轻量结构化输出客户端；未配置时返回 ``None``。"""

    def __init__(self, settings: DecisionSettings):
        """Store validated settings. / 保存已校验配置。

        Args:
            settings: Decision-model endpoint, key and limits. / 决策模型端点、密钥与限制。
        """
        self.settings = settings

    @property
    def configured(self) -> bool:
        """Report whether endpoint, key and model are all present. / 端点、密钥与模型是否齐全。"""
        return bool(self.settings.base_url and self.settings.api_key and self.settings.model)

    async def decide(self, payload: dict, *, timeout: float) -> DecisionOutput | None:
        """Ask the decision model for one structured plan. / 请求一次结构化决策计划。

        Args:
            payload: Canonical JSON facts for this run. / 本轮规范化 JSON 事实。
            timeout: Client-side timeout in seconds. / 客户端超时秒数。

        Returns:
            Parsed output, or ``None`` when unavailable or invalid.
            解析后的输出；不可用或非法时为 None。
        """
        if not self.configured:
            return None
        url = str(self.settings.base_url).rstrip("/") + "/chat/completions"
        body = {
            "model": self.settings.model,
            "temperature": 0,
            "max_tokens": self.settings.max_tokens,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT + "\n" + FEW_SHOT},
                {
                    "role": "user",
                    "content": json.dumps(payload, ensure_ascii=False, sort_keys=True),
                },
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=timeout) as client:
                response = await client.post(
                    url,
                    json=body,
                    headers={"Authorization": f"Bearer {self.settings.api_key.get_secret_value()}"},
                )
                response.raise_for_status()
            content = response.json()["choices"][0]["message"]["content"]
            if not isinstance(content, str) or not content.strip():
                return None
            return DecisionOutput.model_validate(_parse_json(content))
        except Exception:
            logger.warning("Decision model call failed", exc_info=False)
            return None
