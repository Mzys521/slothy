"""Recovery from a failed model run that leaves a pending checkpoint.
/ 失败模型运行后的会话恢复测试。"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


class FailFirstRequestModel(FakeMessagesListChatModel):
    """A fake model that fails only for the first user text. / 仅首个用户文本失败的假模型。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        if any(getattr(message, "content", "") == "first" for message in messages):
            raise RuntimeError("temporary provider failure")
        return await super()._agenerate(messages, *args, **kwargs)


def test_new_message_recovers_a_failed_pending_run(tmp_path):
    """A second message rebuilds a fresh run instead of RUN_PENDING.
    / 第二条消息自动恢复。"""
    model = FailFirstRequestModel(responses=[AIMessage(content="Recovered")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "recovery.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"mode": "chat"}).json()["thread_id"]
        first = client.post(
            "/api/v1/chat",
            json={"thread_id": thread, "message": "first", "stream": True},
        )
        assert first.status_code == 200
        assert "error" in first.text
        second = client.post(
            "/api/v1/chat",
            json={"thread_id": thread, "message": "second", "stream": True},
        )
        assert second.status_code == 200, second.text
        assert "RUN_PENDING" not in second.text
        done = [
            json.loads(frame.removeprefix("event: done\ndata: "))
            for frame in second.text.split("\n\n")
            if frame.startswith("event: done\ndata: ")
        ]
        assert done and done[0]["response"] == "Recovered"
