import zlib
from binascii import crc32
from datetime import UTC, datetime, timedelta
from struct import pack
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.agent.activity import RunActivity
from app.agent.metrics import RunMetrics
from app.api import create_app
from app.config import CheckpointSettings
from app.conversations import ConversationTurn


def png(size=2):
    def chunk(kind, data):
        body = kind + data
        return pack(">I", len(data)) + body + pack(">I", crc32(body))

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress((b"\0" + b"\xff\0\0\xff" * size) * size))
        + chunk(b"IEND", b"")
    )


def app_at(path):
    return create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=path),
        model_provider=SimpleNamespace(
            get_chat_model=lambda: FakeListChatModel(responses=["OK"])
        ),
    )


def test_account_and_avatar_persist_and_bad_uploads_preserve_current_avatar(tmp_path):
    app = app_at(tmp_path / "account.sqlite")
    with TestClient(app) as client:
        base = "/api/v1/account"
        assert client.get(base).json()["avatar_version"] is None
        assert client.get(base + "/avatar").status_code == 404
        assert client.put(base, json={"display_name": "Lin", "handle": "lin"}).status_code == 200
        saved = client.put(base + "/avatar", content=png(), headers={"Content-Type": "image/png"})
        assert saved.status_code == 200, saved.text
        version = saved.json()["avatar_version"]
        assert version and saved.json()["display_name"] == "Lin"
        assert client.get(base + "/avatar").content == png()
        assert client.put(base + "/avatar", content=png(257)).status_code == 422
        assert client.put(base + "/avatar", content=png()[:-3] + b"bad").status_code == 422
        assert client.put(base + "/avatar", content=b"x" * 524_289).status_code == 413
        assert client.get(base).json()["avatar_version"] == version
        assert client.put(base, json={"handle": "<script>"}).status_code == 422
    with TestClient(app) as client:
        assert client.get(base).json()["display_name"] == "Lin"
        assert client.get(base + "/avatar").content == png()
        assert client.delete(base + "/avatar").json()["avatar_version"] is None
        assert client.get(base + "/avatar").status_code == 404
        assert client.get(base).json()["display_name"] == "Lin"


def test_usage_aggregates_recorded_runs_without_counting_forks_twice(tmp_path):
    app = app_at(tmp_path / "usage.sqlite")
    with TestClient(app) as client:
        empty = client.get("/api/v1/account/activity").json()
        assert empty["total_tokens"] is None and empty["current_streak"] == 0

        async def seed():
            store = app.state.conversations
            today = datetime.now(UTC).date()
            for index, days_ago in enumerate((3, 1, 0), start=1):
                thread = "chat" if index != 3 else "work"
                if index in (1, 3):
                    await store.create_thread("local-user", thread, "", thread)
                await store.save_turn(ConversationTurn(
                    "local-user", thread, str(index), "Hello", "OK",
                    metrics=RunMetrics(
                        model="test-model", reasoning_effort="high" if index > 1 else "none",
                        total_tokens=index * 100, input_tokens=index * 80,
                        output_tokens=index * 20, cached_input_tokens=0,
                        cache_reported_input_tokens=0, model_seconds=index,
                        context_tokens=index * 100,
                    ),
                    activity=(RunActivity(
                        id="call", kind="tool", name="calculate", status="done",
                    ),),
                ))
                async with store._connection.execute(
                    "UPDATE conversation_turns SET recorded_at=? WHERE run_id=?",
                    ((today - timedelta(days=days_ago)).isoformat() + "T10:00:00Z", str(index)),
                ):
                    pass
        client.portal.call(seed)
        fork = client.post("/api/v1/threads/chat/fork", json={"title": "Branch"})
        assert fork.status_code == 201, fork.text
        usage = client.get("/api/v1/account/activity").json()
        assert usage["total_tokens"] == 600
        assert usage["peak_daily_tokens"] == 300
        assert usage["longest_model_seconds"] == 3
        assert usage["turns"] == 3 and usage["chat_turns"] == 2
        assert usage["longest_streak"] == usage["current_streak"] == 2
        assert usage["distinct_tools"] == 1 and usage["tool_calls"] == 3
        assert usage["reasoning_effort"] == "high" and usage["reasoning_share"] == 2 / 3
        assert len(usage["daily"]) == 3
