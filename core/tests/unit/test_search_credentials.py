"""Search credential precedence and storage tests. / 搜索凭据优先级与存储测试。"""

from app.providers import ProviderInput, ProviderStore


def test_search_key_prefers_independent_then_deepseek_model_key(tmp_path):
    """Independent Search key wins; DeepSeek model key is the fallback.
    / 独立密钥优先，模型密钥回退。"""
    store = ProviderStore(tmp_path / "providers.json")
    model = ProviderInput(
        id="deepseek",
        name="DeepSeek",
        template="deepseek",
        base_url="https://api.deepseek.com",
        api_key="model-key",
        models=[],
    )
    store.save(model)
    assert store.search_source() == "model"
    assert store.search_key() == "model-key"

    store.save_search_key("search-key")
    assert store.search_source() == "independent"
    assert store.search_key() == "search-key"
    assert all(item.id != "deepseek-search" for item in store.list())

    store.clear_search_key()
    assert store.search_key() == "model-key"
