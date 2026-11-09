"""Persisted model-call JSONL and work-details endpoint tests. / JSONL 与工作详情接口测试。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


class PlainModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_work_details_persist_context_usage_and_redaction(tmp_path):
    """A completed call yields one JSONL record with usage and categories.
    / 完成调用生成 JSONL。"""
    model = PlainModel(
        responses=[
            AIMessage(
                content="Hello",
                usage_metadata={
                    "input_tokens": 120,
                    "output_tokens": 8,
                    "total_tokens": 128,
                    "input_token_details": {"cache_read": 64},
                },
            )
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "work.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"mode": "chat"}).json()["thread_id"]
        reply = client.post(
            "/api/v1/chat",
            json={"thread_id": thread, "message": "hi", "stream": False},
        )
        assert reply.status_code == 200, reply.text
        details = client.get(f"/api/v1/threads/{thread}/work-details").json()["items"]
        assert len(details) == 1
        event = details[0]
        assert event["event_type"] == "model.call"
        assert event["run_id"] == reply.json()["run_id"]
        assert event["context"]["estimated"] is True
        assert event["context"]["total"] > 0
        assert event["usage"]["input_tokens"] == 120
        assert event["usage"]["source"] == "provider"
        assert event["usage"]["cache_hit_tokens"] == 64
        assert event["cache_hit_ratio"] == 64 / 120
        assert "authorization" not in str(event).casefold()
