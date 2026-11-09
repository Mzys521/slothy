"""DeepSeek Search adapter and availability tests. / DeepSeek 搜索适配器与可用性测试。"""

import pytest

from app.agent.tools import create_builtin_tools
from app.config import ToolSettings
from app.tools.contracts import ToolOperationError
from app.tools.search import DeepSeekSearch


class FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, response: FakeResponse):
        self.response = response

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def post(self, *args, **kwargs):
        return self.response


@pytest.mark.asyncio
async def test_search_parses_documented_result_fields(monkeypatch):
    """Search normalizes title, URL, summaries, source and publication time. / 搜索规范化字段。"""
    payload = {
        "results": [
            {
                "title": "Release",
                "url": "https://example.com/release",
                "snippet": "short",
                "summary": "long",
                "content": "body",
                "site_name": "Example",
                "site_icon": "https://example.com/icon.ico",
                "date": "2026-06-01T00:00:00+00:00",
                "authority": 3,
                "rerank_score": 2.4,
            }
        ]
    }
    monkeypatch.setattr(
        "httpx.AsyncClient", lambda **kwargs: FakeClient(FakeResponse(200, payload))
    )
    result = await DeepSeekSearch(lambda: "test-key").run("DeepSeek", count=1)
    item = result["data"]["results"][0]
    assert item["title"] == "Release"
    assert item["url"] == "https://example.com/release"
    assert item["source"] == "Example"
    assert item["published_at"].startswith("2026-06-01")


@pytest.mark.asyncio
async def test_search_reports_auth_and_empty_configuration(monkeypatch):
    """Auth failure and missing credentials are explicit, never fabricated.
    / 鉴权失败与未配置明确报错。"""
    with pytest.raises(ToolOperationError) as missing:
        await DeepSeekSearch(lambda: None).run("x")
    assert missing.value.result.error.code == "SEARCH_NOT_CONFIGURED"

    monkeypatch.setattr("httpx.AsyncClient", lambda **kwargs: FakeClient(FakeResponse(401, {})))
    with pytest.raises(ToolOperationError) as unauthorized:
        await DeepSeekSearch(lambda: "bad").run("x")
    assert unauthorized.value.result.error.code == "SEARCH_AUTH_FAILED"


def test_search_tool_appears_only_when_configured(tmp_path):
    """The model-facing tool is registered only for a resolved key. / 仅配置后注册搜索工具。"""
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    without = create_builtin_tools(settings)
    with_search = create_builtin_tools(
        settings, search=DeepSeekSearch(lambda: "key")
    )
    assert "deepseek_search" not in {tool.name for tool in without}
    assert "deepseek_search" in {tool.name for tool in with_search}
