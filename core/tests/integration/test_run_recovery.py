"""Recovery from a failed model run that leaves a pending checkpoint.
/ 失败模型运行后的会话恢复测试。"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app import api
from app.config import CheckpointSettings
from app.infrastructure.llm.catalog import ModelItem
from tests.support.legacy_app import create_app


class FailFirstRequestModel(FakeMessagesListChatModel):
    """A fake model that fails only for the first user text. / 仅首个用户文本失败的假模型。"""

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        if any(getattr(message, "content", "") == "first" for message in messages):
            raise RuntimeError("temporary provider failure")
        return await super()._agenerate(messages, *args, **kwargs)


class RecordingModel(FakeMessagesListChatModel):
    calls: int = 0

    def bind_tools(self, _tools):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        self.calls += 1
        return await super()._agenerate(messages, *args, **kwargs)


def test_pending_checkpoint_survives_new_input_and_restart_then_streams_original_run(
    tmp_path, monkeypatch
):
    """The native checkpoint, not an archive reconstruction, owns recovery.
    / 原生检查点拥有恢复状态，新消息和应用重启都不会覆盖原请求。"""
    selected = []
    models = {
        "chosen": FailFirstRequestModel(responses=[AIMessage(content="unused")]),
        "other": RecordingModel(responses=[AIMessage(content="wrong model")]),
    }
    catalog = {
        name: (
            ModelItem(id=name, provider="test", reasoning_levels=("none",), default_effort="none"),
            name,
        )
        for name in models
    }
    monkeypatch.setattr(api, "configured_models", lambda: catalog)

    def provider(name, *, effort):
        selected.append(name)
        return SimpleNamespace(get_chat_model=lambda: models[name])

    monkeypatch.setattr(api, "OpenAICompatibleProvider", provider)
    settings = CheckpointSettings(sqlite_path=tmp_path / "recovery.sqlite", _env_file=None)
    app = create_app(checkpoint_settings=settings, tool_factory=lambda _: [])
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"mode": "chat"}).json()["thread_id"]
        path = f"/api/v1/threads/{thread}"
        attachment = client.post(
            path + "/attachments?filename=note.txt", content=b"ORIGINAL_ATTACHMENT"
        ).json()
        first = client.post(
            "/api/v1/chat",
            json={
                "thread_id": thread,
                "message": "first",
                "stream": True,
                "model": "chosen",
                "reasoning_effort": "none",
                "attachment_ids": [attachment["id"]],
                "personalization_enabled": False,
            },
        )
        assert first.status_code == 200
        assert "event: error" in first.text
        pending = client.get(path + "/messages").json()["pending_run"]
        assert pending["message"] == "first" and pending["status"] == "failed"
        assert pending["model_selection"]["id"] == "chosen"
        assert pending["personalization_enabled"] is False
        assert pending["attachments"] == [attachment]
        assert "ORIGINAL_ATTACHMENT" not in json.dumps(pending["attachments"])
        second = client.post(
            "/api/v1/chat",
            json={"thread_id": thread, "message": "second"},
        )
        assert second.status_code == 409 and second.json()["code"] == "RUN_PENDING"
        assert client.get(path + "/messages").json()["pending_run"] == pending

    # Recompile against the same SQLite saver, with another model now the default.
    # 使用同一 SQLite 重新编译，此时默认模型已经变为另一个模型。
    models["chosen"] = RecordingModel(responses=[AIMessage(content="Recovered")])
    catalog = {"other": catalog["other"], "chosen": catalog["chosen"]}
    with TestClient(create_app(checkpoint_settings=settings, tool_factory=lambda _: [])) as client:
        restored = client.get(path + "/messages").json()["pending_run"]
        assert restored == pending
        stale = client.post(path + "/resume?checkpoint_id=stale")
        assert stale.status_code == 409 and stale.json()["code"] == "CHECKPOINT_STALE"
        second = client.post(
            path + "/resume?stream=true&checkpoint_id=" + restored["checkpoint_id"]
        )
        assert second.status_code == 200 and second.headers["content-type"].startswith(
            "text/event-stream"
        )
        done = [
            json.loads(frame.removeprefix("event: done\ndata: "))
            for frame in second.text.split("\n\n")
            if frame.startswith("event: done\ndata: ")
        ]
        assert done and done[0]["response"] == "Recovered" and done[0]["message"] == "first"
        assert done[0]["run_id"] == pending["run_id"]
        assert models["chosen"].calls == 1 and models["other"].calls == 0
        page = client.get(path + "/messages").json()
        assert page["pending_run"] is None and len(page["items"]) == 1
        assert page["items"][0]["attachments"] == [attachment]
        # A lost completion acknowledgement is idempotent, never a new model call.
        count = len(selected)
        again = client.post(path + "/resume").json()
        assert again["run_id"] == pending["run_id"] and again["response"] == "Recovered"
        assert len(selected) == count
        assert models["chosen"].calls == 1
        assert len(client.get(path + "/messages").json()["items"]) == 1
        assert client.delete(path + "/messages").status_code == 204
        assert client.get(path + "/messages").json()["pending_run"] is None
        assert client.post(path + "/resume").json()["code"] == "NO_PENDING_RUN"
