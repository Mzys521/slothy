"""Search settings API and dynamic tool registration tests. / 搜索设置与动态工具注册测试。"""

from fastapi.testclient import TestClient

from app.config import CheckpointSettings
from app.providers import ProviderStore
from tests.support.legacy_app import create_app


def test_search_key_adds_tool_and_reports_status(tmp_path):
    """A stored Search key exposes deepseek_search and is never returned.
    / 已存密钥启用工具且不回传。"""
    database = tmp_path / "search.sqlite"
    store = ProviderStore(database.with_name("providers.json"))
    store.save_search_key("test-search-key")
    app = create_app(checkpoint_settings=CheckpointSettings(sqlite_path=database))
    with TestClient(app) as client:
        status = client.get("/api/v1/settings/search").json()
        assert status == {"configured": True, "source": "independent", "independent": True}
        assert "test-search-key" not in str(status)
        runtime = client.get("/api/v1/runtime").json()
        assert "deepseek_search" in runtime["chat_tools"]
        assert "deepseek_search" in runtime["work_tools"]

        deleted = client.delete("/api/v1/settings/search")
        assert deleted.status_code == 204
        assert client.get("/api/v1/settings/search").json()["configured"] is False
        runtime = client.get("/api/v1/runtime").json()
        assert "deepseek_search" not in runtime["work_tools"]
