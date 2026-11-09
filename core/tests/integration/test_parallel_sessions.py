"""P0 regression: exact session isolation and cancellation. / P0 会话隔离与取消回归。"""

import asyncio
import gc
import weakref
from types import SimpleNamespace

import httpx
import pytest
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.concurrency import SessionLocks
from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


def test_session_locks_are_exact_and_idle_entries_do_not_leak():
    pool = SessionLocks()
    first = pool.for_session("first")
    assert pool.for_session("first") is first
    assert pool.for_session("second") is not first
    observed = weakref.ref(first)
    del first
    gc.collect()
    assert observed() is None


async def test_same_session_still_serializes_waiters():
    pool = SessionLocks()
    first = pool.for_session("same")
    await first.acquire()
    waiting = asyncio.create_task(pool.for_session("same").acquire())
    await asyncio.sleep(0)
    assert not waiting.done()
    first.release()
    assert await asyncio.wait_for(waiting, 1)
    pool.for_session("same").release()


@pytest.mark.parametrize("mode", ["chat", "work"])
async def test_colliding_http_sessions_run_together_and_cancelling_one_keeps_other(tmp_path, mode):
    started = {name: asyncio.Event() for name in ("A", "B")}
    release = {name: asyncio.Event() for name in ("A", "B")}
    cancelled = {name: asyncio.Event() for name in ("A", "B")}

    async def reply(messages):
        name = next(m.content for m in reversed(messages) if m.type == "human" and not m.name)
        started[name].set()
        try:
            await release[name].wait()
        except asyncio.CancelledError:
            cancelled[name].set()
            raise
        return AIMessage(content=name + "-complete")

    class GatedModel(RunnableLambda):
        def bind_tools(self, _tools):
            return self

    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "parallel.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: GatedModel(reply)),
    )
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client,
    ):
        buckets = {}
        for _ in range(65):
            response = await client.post("/api/v1/threads", json={"mode": mode})
            thread = response.json()["thread_id"]
            bucket = hash(thread) % 64
            if bucket in buckets:
                first, second = buckets[bucket], thread
                break
            buckets[bucket] = thread
        else:
            pytest.fail("Expected two real IDs colliding under the legacy 64-lock scheme")

        def send(thread, name):
            return asyncio.create_task(
                client.post(
                    "/api/v1/chat",
                    json={
                        "thread_id": thread,
                        "message": name,
                        "stream": True,
                        "personalization_enabled": False,
                    },
                )
            )

        a = send(first, "A")
        b = None
        try:
            await asyncio.wait_for(started["A"].wait(), 3)
            b = send(second, "B")
            await asyncio.wait_for(started["B"].wait(), 3)
            a.cancel()
            with pytest.raises(asyncio.CancelledError):
                await a
            await asyncio.wait_for(cancelled["A"].wait(), 3)
            assert not cancelled["B"].is_set() and not b.done()
            release["B"].set()
            result = await asyncio.wait_for(b, 5)
            assert result.status_code == 200 and "B-complete" in result.text
            assert "A-complete" not in result.text
            history = (await client.get(f"/api/v1/threads/{second}/messages")).json()["items"]
            assert len(history) == 1 and history[0]["response"] == "B-complete"
        finally:
            release["A"].set()
            release["B"].set()
            for task in (a, b):
                if task is not None and not task.done():
                    task.cancel()
            await asyncio.gather(*(t for t in (a, b) if t is not None), return_exceptions=True)
