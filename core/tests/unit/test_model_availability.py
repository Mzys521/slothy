"""Model availability test endpoint behavior. / 模型可用性测试行为。"""

import pytest
from langchain_core.messages import AIMessage

from app.providers import ProviderConnection, ProviderInput, ProviderStore


class FakeModel:
    def __init__(self, model_id: str):
        self.model_id = model_id

    async def ainvoke(self, messages):
        if self.model_id == "broken-model":
            raise RuntimeError("model not found")
        return AIMessage(content="OK")


class FakeProvider:
    def __init__(self, model_id: str):
        self.model = FakeModel(model_id)

    def get_chat_model(self):
        return self.model


@pytest.mark.asyncio
async def test_model_tests_return_partial_success(tmp_path, monkeypatch):
    """One failing model does not abort others; results stay per model.
    / 单个失败不影响其他模型。"""
    store = ProviderStore(tmp_path / "providers.json")
    monkeypatch.setattr(
        ProviderConnection,
        "build_provider",
        lambda self, effort, max_tokens=None: FakeProvider(self.model),
    )
    body = ProviderInput(
        id="deepseek",
        name="DeepSeek",
        template="deepseek",
        base_url="https://api.deepseek.com",
        api_key="test-key",
        models=[{"id": "good-model"}, {"id": "broken-model"}],
    )
    results = await store.test_models(body, ["good-model", "broken-model"])
    assert [item["ok"] for item in results] == [True, False]
    assert "not found" in results[1]["message"]
    assert all("test-key" not in item["message"] for item in results)
