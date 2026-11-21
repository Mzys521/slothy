"""Clear and delete are retryable business operations across every store (F06).

/ 清空与删除是跨全部存储的可重试业务操作（F06）。
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.config import CheckpointSettings
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class PlainModel(FakeMessagesListChatModel):
    """Fake chat model ignoring tool binding. / 忽略工具绑定的假聊天模型。"""

    def bind_tools(self, tools, **kwargs):
        return self


def build_app(tmp_path, model):
    """Build an offline app over one data directory. / 在同一数据目录上构建离线应用。"""
    return create_app(
        checkpoint_settings=CheckpointSettings(
            sqlite_path=tmp_path / "cleanup.sqlite", _env_file=None
        ),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
    )


def send_turn(client, thread, message="hello"):
    """Send one turn and return its run id. / 发送一轮并返回运行 ID。"""
    reply = client.post(BASE + "/chat", json={"thread_id": thread, "message": message})
    assert reply.status_code == 200, reply.text
    return reply.json()["run_id"]


def flaky(calls, original, message):
    """Wrap a cleanup step so its first call fails. / 包装清理步骤使其首次调用失败。"""

    async def step(*args):
        calls["count"] += 1
        if calls["count"] == 1:
            raise RuntimeError(message)
        return await original(*args)

    return step


def test_clear_resumes_after_a_failed_step_and_removes_every_record(tmp_path):
    model = PlainModel(responses=[AIMessage(content="bye")])
    app = build_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        run_id = send_turn(client, thread)
        assert client.get(BASE + f"/threads/{thread}/messages").json()["items"]
        assert client.get(BASE + f"/threads/{thread}/work-details").json()["items"]
        assert client.get(BASE + f"/threads/{thread}/memory-receipts?run_id={run_id}").is_success
        calls = {"count": 0}
        lifecycle = app.state.thread_lifecycle
        lifecycle.journal_deleter = flaky(calls, lifecycle.journal_deleter, "journal busy")
        failed = client.delete(BASE + f"/threads/{thread}/messages")
        assert failed.status_code == 202, failed.text
        operation = failed.json()["operation_id"]
        assert failed.json()["state"] == "failed"
        status = client.get(BASE + f"/operations/{operation}").json()
        assert status["state"] == "failed"
        assert "checkpoint:" + thread in status["steps"]
        assert "journal:" + thread not in status["steps"]
        # A session whose cleanup is unfinished accepts no new writes.
        # 清理未完成的会话不接受新的写入。
        refused = client.post(BASE + "/chat", json={"thread_id": thread, "message": "again"})
        assert refused.status_code == 409 and refused.json()["code"] == "SESSION_CLEANING"
        # Repeating the clear continues the recorded task instead of failing.
        # 重复清空会继续已记录的任务，而不是失败。
        resumed = client.delete(BASE + f"/threads/{thread}/messages")
        assert resumed.status_code == 204, resumed.text
        assert client.get(BASE + f"/operations/{operation}").json()["state"] == "done"

    # Restart resumes the durable task and finishes the journal step.
    # 重启会继续持久任务并完成日志步骤。
    with TestClient(build_app(tmp_path, model)) as client:
        status = client.get(BASE + f"/operations/{operation}").json()
        assert status["state"] == "done"
        assert status["steps"]
        page = client.get(BASE + f"/threads/{thread}/messages").json()
        assert page["items"] == [] and page["pending_run"] is None
        assert client.get(BASE + f"/threads/{thread}/work-details").json()["items"] == []
        receipts = client.get(BASE + f"/threads/{thread}/memory-receipts?run_id={run_id}").json()
        assert receipts["items"] == [] and receipts["pending"] is False
        assert (
            client.post(BASE + "/chat", json={"thread_id": thread, "message": "ok"}).status_code
            == 200
        )


def test_delete_is_retryable_and_keeps_other_sessions(tmp_path):
    model = PlainModel(responses=[AIMessage(content="ok")])
    app = build_app(tmp_path, model)
    with TestClient(app) as client:
        doomed = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        kept = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        send_turn(client, doomed, "remove me")
        send_turn(client, kept, "keep me")
        calls = {"count": 0}
        lifecycle = app.state.thread_lifecycle
        lifecycle.checkpoint_purger = flaky(
            calls, lifecycle.checkpoint_purger, "checkpoint busy"
        )
        failed = client.delete(BASE + f"/threads/{doomed}")
        assert failed.status_code == 202, failed.text
        assert client.get(BASE + f"/threads/{doomed}").status_code == 200

    with TestClient(build_app(tmp_path, model)) as client:
        operation = failed.json()["operation_id"]
        assert client.get(BASE + f"/operations/{operation}").json()["state"] == "done"
        assert client.get(BASE + f"/threads/{doomed}").status_code == 404
        assert client.get(BASE + f"/threads/{kept}").status_code == 200
        assert client.get(BASE + f"/threads/{kept}/messages").json()["items"]


def test_workspace_delete_is_resumable_and_blocks_new_sessions(tmp_path):
    model = PlainModel(responses=[AIMessage(content="ok")])
    app = build_app(tmp_path, model)
    with TestClient(app) as client:
        workspace = client.post(BASE + "/workspaces", json={"name": "Project"}).json()["id"]
        first = client.post(
            BASE + "/threads", json={"mode": "chat", "workspace_id": workspace}
        ).json()["thread_id"]
        second = client.post(
            BASE + "/threads", json={"mode": "chat", "workspace_id": workspace}
        ).json()["thread_id"]
        send_turn(client, first)
        send_turn(client, second)
        personal = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        send_turn(client, personal, "keep")
        calls = {"count": 0}
        lifecycle = app.state.thread_lifecycle
        lifecycle.journal_deleter = flaky(calls, lifecycle.journal_deleter, "journal busy")
        failed = client.delete(BASE + f"/workspaces/{workspace}")
        assert failed.status_code == 202, failed.text
        blocked = client.post(BASE + "/threads", json={"mode": "chat", "workspace_id": workspace})
        assert blocked.status_code == 409 and blocked.json()["code"] == "WORKSPACE_CLEANING"
        assert client.get(BASE + f"/workspaces/{workspace}").status_code == 200

    with TestClient(build_app(tmp_path, model)) as client:
        operation = failed.json()["operation_id"]
        assert client.get(BASE + f"/operations/{operation}").json()["state"] == "done"
        assert client.get(BASE + f"/workspaces/{workspace}").status_code == 404
        assert client.get(BASE + f"/threads/{first}").status_code == 404
        assert client.get(BASE + f"/threads/{second}").status_code == 404
        assert client.get(BASE + f"/threads/{personal}").status_code == 200
        assert client.get(BASE + f"/threads/{personal}/messages").json()["items"]


def test_successful_clear_returns_204_and_empty_details(tmp_path):
    model = PlainModel(responses=[AIMessage(content="bye")])
    app = build_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        send_turn(client, thread)
        assert client.delete(BASE + f"/threads/{thread}/messages").status_code == 204
        assert client.get(BASE + f"/threads/{thread}/messages").json()["items"] == []
        assert client.get(BASE + f"/threads/{thread}/work-details").json()["items"] == []
