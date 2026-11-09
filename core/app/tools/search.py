"""DeepSeek Search API adapter, kept outside the Agent Runner.
/ DeepSeek 搜索适配器，独立于 Agent Runner。"""

import asyncio
from collections.abc import Callable

import httpx

from app.config import ToolSettings
from app.tools.contracts import ToolOperationError, ToolResult

DEFAULT_SEARCH_BASE_URL = "https://api.deepseek.com"


class DeepSeekSearch:
    """Call DeepSeek's raw Search API with the configured credential.
    / 使用配置凭据调用原始搜索接口。"""

    def __init__(
        self,
        key_provider: Callable[[], str | None],
        *,
        base_url: str = DEFAULT_SEARCH_BASE_URL,
        timeout_seconds: float = 20.0,
    ) -> None:
        """Initialize the search adapter.

        / 初始化搜索适配器。

        Args:
            key_provider: Resolves the independent or fallback model key at call time.
                / 调用时解析独立密钥或模型密钥的回调。
            base_url: DeepSeek API base URL. / DeepSeek API 根地址。
            timeout_seconds: Per-attempt timeout. / 单次请求超时。
        """
        self.key_provider = key_provider
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds

    @property
    def endpoint(self) -> str:
        """Build the documented Search endpoint. / 构造官方 Search 端点。"""
        base = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        return base + "/v1/tools/search"

    async def run(
        self,
        query: str,
        count: int = 5,
        ori_question: str | None = None,
        need_content: bool = True,
    ):
        """Search the web and normalize the documented response fields.

        / 搜索网页并规范化官方响应字段。

        Raises:
            ToolOperationError: For missing credentials, auth/balance/rate/timeout errors.
                / 无凭据、鉴权/余额/限流/超时等错误时抛出。
        """
        key = (self.key_provider() or "").strip()
        if not key:
            raise ToolOperationError(
                "SEARCH_NOT_CONFIGURED",
                "DeepSeek Search is not configured.",
                hint="Add a DeepSeek Search API key in Settings.",
            )
        body = {"query": query, "count": count, "need_content": need_content}
        if ori_question:
            body["ori_question"] = ori_question
        last_error = ""
        for attempt in range(2):
            try:
                async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                    response = await client.post(
                        self.endpoint,
                        json=body,
                        headers={"Authorization": f"Bearer {key}"},
                    )
            except httpx.TimeoutException as error:
                last_error = "Search request timed out."
                if attempt == 0:
                    await asyncio.sleep(0.5)
                    continue
                raise ToolOperationError("SEARCH_TIMEOUT", last_error, retryable=True) from error
            except httpx.HTTPError as error:
                raise ToolOperationError(
                    "SEARCH_FAILED",
                    "The search service is unreachable.",
                    retryable=True,
                ) from error
            if response.status_code == 200:
                try:
                    payload = response.json()
                except ValueError as error:
                    raise ToolOperationError(
                        "SEARCH_FAILED", "The search service returned invalid JSON."
                    ) from error
                results = [
                    {
                        "title": str(item.get("title") or ""),
                        "url": str(item.get("url") or ""),
                        "snippet": str(item.get("snippet") or ""),
                        "summary": str(item.get("summary") or ""),
                        "content": str(item.get("content") or ""),
                        "source": item.get("site_name"),
                        "source_icon": item.get("site_icon"),
                        "published_at": item.get("date"),
                        "authority": item.get("authority"),
                        "rerank_score": item.get("rerank_score"),
                    }
                    for item in payload.get("results", [])
                    if isinstance(item, dict) and item.get("url") and item.get("title")
                ]
                return ToolResult(
                    ok=True,
                    summary=(
                        f"Found {len(results)} web results."
                        if results
                        else "Search completed with no matching results."
                    ),
                    data={"query": query, "results": results},
                ).model_dump()
            if response.status_code in (408, 429, 500, 502, 503, 504) and attempt == 0:
                await asyncio.sleep(0.5)
                continue
            code_map = {
                400: ("SEARCH_INVALID", "The search query was rejected."),
                401: ("SEARCH_AUTH_FAILED", "DeepSeek rejected the Search API key."),
                402: ("SEARCH_BALANCE", "The DeepSeek account has insufficient balance."),
                403: ("SEARCH_FORBIDDEN", "The search request was blocked."),
                429: ("SEARCH_RATE_LIMITED", "Search rate limit exceeded; retry later."),
                504: ("SEARCH_TIMEOUT", "The search service timed out."),
            }
            code, message = code_map.get(
                response.status_code, ("SEARCH_FAILED", "The search service failed.")
            )
            raise ToolOperationError(
                code,
                message,
                retryable=response.status_code in (429, 500, 502, 503, 504),
            )
        raise ToolOperationError(
            "SEARCH_FAILED", last_error or "The search failed.", retryable=True
        )


def create_search_tool(settings: ToolSettings, search: DeepSeekSearch):
    """Create the model-facing search tool via the shared built-in factory.
    / 创建模型可见搜索工具。"""
    from app.agent.tools.builtins import builtin_tools

    return builtin_tools(settings, search=search)
