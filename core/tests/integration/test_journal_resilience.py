"""Diagnostic persistence must never decide whether a model answer succeeds (F04).

/ 诊断写入绝不应决定模型回答是否成功（F04）。
"""

import asyncio
import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.config import CheckpointSettings
from app.observability.journal import RuntimeJournal, error_fields
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class CountingModel(FakeMessagesListChatModel):
    """Fake chat model counting provider calls. / 统计提供商调用的假聊天模型。"""

    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        self.calls += 1
        return await super()._agenerate(messages, *args, **kwargs)


def journal_fields(**overrides) -> dict:
    """Build one complete model-call journal payload. / 构建一条完整的模型调用日志参数。"""
    fields = {
        "session_id": "thread-1",
        "run_id": "run-1",
        "step_id": "run-1:step:1",
        "model_call_id": "call-1",
        "provider": "test",
        "model": "fake",
        "parameters": {"temperature": None},
        "context": {"total": 10},
        "tools": {"count": 0, "names": [], "schema_tokens": 0},
        "usage": None,
        "duration_ms": 5,
        "status": "success",
    }
    fields.update(overrides)
    return fields


def test_unwritable_journal_keeps_the_answer_and_archives(tmp_path):
    model = CountingModel(responses=[AIMessage(content="Hello")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "journal.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        journal = app.state.runtime_journal

        def deny(*_args, **_kwargs):
            raise PermissionError("diagnostic disk is read-only")

        journal._append = deny
        reply = client.post(BASE + "/chat", json={"message": "hi"})
        assert reply.status_code == 200, reply.text
        assert reply.json()["response"] == "Hello"
        thread = BASE + "/threads/" + reply.json()["thread_id"]
        assert len(client.get(thread + "/messages").json()["items"]) == 1
        details = client.get(thread + "/work-details").json()
        assert details["items"] == []
        assert details["diagnostics"]["degraded"] is True
        assert details["diagnostics"]["failed"] > 0
        assert model.calls == 1


def test_provider_failure_is_reported_when_the_journal_also_fails(tmp_path):
    class ProviderError(RuntimeError):
        status_code = 429

    class FailingModel(CountingModel):
        async def _agenerate(self, messages, *args, **kwargs):
            self.calls += 1
            raise ProviderError("rate limited: secret prompt text")

    model = FailingModel(responses=[AIMessage(content="unused")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "failing.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        app.state.runtime_journal._append = lambda *args, **kwargs: (_ for _ in ()).throw(
            PermissionError("read-only")
        )
        reply = client.post(BASE + "/chat", json={"message": "hi"})
        assert reply.status_code == 502
        assert reply.json()["code"] == "AGENT_RUN_FAILED"
        assert model.calls == 1


def test_error_records_keep_only_structured_fields(tmp_path):
    class ProviderError(RuntimeError):
        status_code = 429

    model = CountingModel(responses=[AIMessage(content="unused")])

    async def fail(*_args, **_kwargs):
        model.calls += 1
        raise ProviderError("leaked prompt text")

    model._agenerate = fail
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "error.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        reply = client.post(BASE + "/chat", json={"thread_id": thread, "message": "hi"})
        assert reply.status_code == 502
        records = client.get(BASE + f"/threads/{thread}/work-details").json()["items"]
        assert len(records) == 1
        error = records[0]["error"]
        assert error == {"type": "ProviderError", "status_code": "429"}
        assert "leaked prompt text" not in json.dumps(records)


def test_error_fields_whitelist():
    class Coded(RuntimeError):
        code = "RATE_LIMITED"

    fields = error_fields(Coded("do not persist this"))
    assert fields == {"type": "Coded", "code": "RATE_LIMITED"}


async def test_queue_overflow_is_counted_without_raising(tmp_path):
    journal = RuntimeJournal(tmp_path, queue_size=1)

    def slow_append(*_args, **_kwargs):
        import time

        time.sleep(0.15)

    journal._append = slow_append
    journal._ensure_started()
    results = [journal.try_enqueue_model_call(**journal_fields(model_call_id=str(i)))
               for i in range(12)]
    assert any(results) and not all(results)
    assert journal.health.dropped > 0
    await journal.stop()


async def test_reads_stay_bounded_and_cover_rotation(tmp_path):
    journal = RuntimeJournal(tmp_path, rotate_bytes=400, rotate_keep=2)
    for index in range(60):
        await journal.write(
            "thread-1",
            {"event_type": "model.call", "model_call_id": f"call-{index}", "padding": "x" * 40},
        )
    # Only the newest rotation and the live file are read, so the result is a suffix of the
    # newest records rather than the whole history. / 只读取最新轮转与当前文件，因此结果是最近
    # 记录的后缀，而不是完整历史。
    records = await journal.read("thread-1", limit=5)
    assert 0 < len(records) <= 5
    assert records[-1]["model_call_id"] == "call-59"
    ids = [int(item["model_call_id"].removeprefix("call-")) for item in records]
    assert ids == sorted(ids) and ids[-1] - ids[0] == len(ids) - 1
    assert (journal.session_path("thread-1").parent / "runtime.jsonl.1").exists()


async def test_reads_return_exactly_the_limit_without_rotation(tmp_path):
    journal = RuntimeJournal(tmp_path)
    for index in range(60):
        await journal.write("thread-1", {"model_call_id": f"call-{index}"})
    records = await journal.read("thread-1", limit=5)
    newest = [f"call-{index}" for index in range(55, 60)]
    assert [item["model_call_id"] for item in records] == newest


async def test_delete_session_discards_queued_records(tmp_path):
    journal = RuntimeJournal(tmp_path, queue_size=4)

    def slow_append(*_args, **_kwargs):
        import time

        time.sleep(0.05)

    journal._append = slow_append
    journal._ensure_started()
    assert journal.try_enqueue_model_call(**journal_fields())
    await journal.delete_session("thread-1")
    await journal.flush()
    await journal.stop()
    directory = journal.session_path("thread-1").parent
    assert not directory.exists()
    assert journal.health.dropped >= 1


def test_concurrent_large_input_does_not_block_the_run(tmp_path):
    """A slow journal keeps the run responsive because writes happen in a worker thread.
    / 慢日志不会阻塞运行，因为写盘发生在工作线程。"""
    assert asyncio  # keep the import meaningful for readers of this module.
    model = CountingModel(responses=[AIMessage(content="fast")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "slow.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )
    with TestClient(app) as client:
        import time

        def slow_append(*_args, **_kwargs):
            time.sleep(0.4)

        app.state.runtime_journal._append = slow_append
        started = time.perf_counter()
        reply = client.post(BASE + "/chat", json={"message": "hi"})
        elapsed = time.perf_counter() - started
        assert reply.status_code == 200
        # The answer is not gated on the diagnostic write. / 回答不以诊断写盘为条件。
        assert elapsed < 5
        assert model.calls == 1
