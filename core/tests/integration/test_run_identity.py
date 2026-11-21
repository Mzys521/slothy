"""Client request identity, model-independent reads and resume reconciliation (F02, F03).

These tests pin two audit findings: a failed new message must never resume the previous
completed run, and checkpoint reads must keep working after every provider is removed.
/ 这些用例固定两项审计结论：失败的新消息绝不能恢复上一条已完成运行；移除全部服务商后检查点
读取仍须可用。
"""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class ToolModel(FakeMessagesListChatModel):
    """Fake chat model that ignores tool binding. / 忽略工具绑定的假聊天模型。"""

    def bind_tools(self, tools, **kwargs):
        return self


class CountingModel(FakeMessagesListChatModel):
    """Fake chat model counting real provider calls. / 统计真实调用的假聊天模型。"""

    calls: int = 0

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        self.calls += 1
        return await super()._agenerate(messages, *args, **kwargs)


def request_mkdir(path):
    """Fake assistant message proposing a reviewed workspace write. / 提议需审批写入的假消息。"""
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "workspace_write", "args": {"action": "mkdir", "path": path}, "id": path}
        ],
    )


def test_pending_run_and_approval_survive_provider_removal(tmp_path):
    tool_settings = ToolSettings(_env_file=None, workspace_path=tmp_path / "artifacts")
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite", _env_file=None)
    model = ToolModel(responses=[request_mkdir("first")])
    app = create_app(
        checkpoint_settings=checkpoint,
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(tool_settings),
    )
    with TestClient(app) as client:
        review = client.post(
            BASE + "/chat", json={"mode": "work", "message": "Make a folder"}
        ).json()
        assert review["kind"] == "tool_review"
        thread = BASE + "/threads/" + review["thread_id"]
        assert client.get(thread + "/approval").json()["approval_id"] == review["approval_id"]

    # No provider and no tools: reads must still project the pending run and its approval.
    # 无服务商、无工具时，读取仍须投影待处理运行及其审批。
    without_model = create_app(
        checkpoint_settings=checkpoint,
        model_provider=None,
        tool_factory=lambda _settings: [],
    )
    with TestClient(without_model) as client:
        assert client.get(BASE + "/runtime").json()["ready"] is False
        pending = client.get(thread + "/messages").json()["pending_run"]
        assert pending is not None
        assert pending["run_id"] == review["run_id"]
        assert pending["status"] == "awaiting_approval"
        assert client.get(thread + "/approval").json()["approval_id"] == review["approval_id"]
        workspace = client.post(BASE + "/workspaces", json={"name": "Moved"}).json()["id"]
        moved = client.patch(thread, json={"workspace_id": workspace})
        assert moved.status_code == 409 and moved.json()["code"] == "RUN_PENDING"
        # Continuing really needs the saved model, so it reports that instead of hiding the run.
        # 真正继续需要原模型，因此明确报告模型不可用，而不是隐藏该运行。
        assert client.post(thread + "/resume", json={}).status_code == 503


def test_failed_new_message_never_replays_the_previous_run(tmp_path):
    model = CountingModel(responses=[AIMessage(content="A done")])
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "identity.sqlite", _env_file=None)
    app = create_app(
        checkpoint_settings=checkpoint,
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=lambda _settings: [],
    )
    with TestClient(app) as client:
        first = client.post(
            BASE + "/chat",
            json={"message": "A", "client_request_id": "req-a"},
        )
        assert first.status_code == 200, first.text
        completed = first.json()
        thread = BASE + "/threads/" + completed["thread_id"]
        # The new request fails before any checkpoint exists (reference validation).
        # 新请求在建立任何检查点前失败（引用校验）。
        failed = client.post(
            BASE + "/chat",
            json={
                "thread_id": completed["thread_id"],
                "message": "B",
                "client_request_id": "req-b",
                "references": [{"kind": "session", "id": "missing-session"}],
            },
        )
        assert failed.status_code == 422 and failed.json()["code"] == "REFERENCE_INVALID"
        status = client.get(thread + "/run-status", params={"client_request_id": "req-b"}).json()
        assert status["status"] == "not_started" and status["run_id"] is None
        # A retry of B must not resurrect A: unnamed resume is rejected and A stays intact.
        # 重试 B 不得复活 A：无限定恢复被拒绝，A 的内容与状态保持不变。
        unnamed = client.post(thread + "/resume")
        assert unnamed.status_code == 409 and unnamed.json()["code"] == "NO_PENDING_RUN"
        resumed_a = client.post(thread + "/resume", params={"run_id": completed["run_id"]})
        assert resumed_a.status_code == 200
        assert resumed_a.json()["run_id"] == completed["run_id"]
        assert resumed_a.json()["response"] == "A done"
        page = client.get(thread + "/messages").json()
        assert [item["run_id"] for item in page["items"]] == [completed["run_id"]]
        assert page["pending_run"] is None
        assert model.calls == 1
        # A failed run after its START checkpoint belongs to the new turn, never to A.
        # 已写入 START 检查点后失败的运行属于新轮次，绝不属于 A。
        model.responses = [AIMessage(content="B retried")]
        retried = client.post(
            BASE + "/chat",
            json={
                "thread_id": completed["thread_id"],
                "message": "B",
                "client_request_id": "req-b",
            },
        )
        assert retried.status_code == 200, retried.text
        assert retried.json()["run_id"] != completed["run_id"]
        status = client.get(thread + "/run-status", params={"client_request_id": "req-b"}).json()
        assert status["status"] == "completed" and status["result"]["response"] == "B retried"


def test_approval_resumed_run_settles_the_original_request_claim(tmp_path):
    class CountingToolModel(ToolModel):
        calls: int = 0

        async def _agenerate(self, messages, *args, **kwargs):
            self.calls += 1
            return await super()._agenerate(messages, *args, **kwargs)

    model = CountingToolModel(responses=[request_mkdir("approved"), AIMessage(content="Created.")])
    tool_settings = ToolSettings(_env_file=None, workspace_path=tmp_path / "artifacts")
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "claim.sqlite", _env_file=None)
    app = create_app(
        checkpoint_settings=checkpoint,
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(tool_settings),
    )
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        review = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "Make a folder",
                "client_request_id": "req-approval",
            },
        ).json()
        assert review["kind"] == "tool_review"
        done = client.post(
            BASE + f"/threads/{thread}/approval/{review['approval_id']}",
            json={"approved": True},
        )
        assert done.status_code == 200, done.text
        # The resumed approval completes the claim of the original request; repeating that
        # request returns the same run instead of starting or failing it.
        # 审批恢复完成原请求的领取记录；重复该请求返回同一运行，而不是新运行或失败。
        again = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "Make a folder",
                "client_request_id": "req-approval",
            },
        )
        assert again.status_code == 200, again.text
        assert again.json()["run_id"] == done.json()["run_id"]
        assert model.calls == 2
        status = client.get(
            BASE + f"/threads/{thread}/run-status?client_request_id=req-approval"
        ).json()
        assert status["status"] == "completed" and status["run_id"] == done.json()["run_id"]


def test_duplicate_request_identity_returns_the_same_run(tmp_path):
    model = CountingModel(responses=[AIMessage(content="only once")])
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "duplicate.sqlite", _env_file=None)
    app = create_app(
        checkpoint_settings=checkpoint,
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=lambda _settings: [],
    )
    with TestClient(app) as client:
        body = {"message": "one request", "client_request_id": "req-1"}
        first = client.post(BASE + "/chat", json=body)
        assert first.status_code == 200, first.text
        thread_id = first.json()["thread_id"]
        again = client.post(BASE + "/chat", json={**body, "thread_id": thread_id})
        assert again.status_code == 200
        assert again.json()["run_id"] == first.json()["run_id"]
        assert again.json()["response"] == "only once"
        conflict = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread_id,
                "message": "different input",
                "client_request_id": "req-1",
            },
        )
        assert conflict.status_code == 409 and conflict.json()["code"] == "REQUEST_CONFLICT"
        thread = BASE + "/threads/" + thread_id
        assert len(client.get(thread + "/messages").json()["items"]) == 1
        assert model.calls == 1
        status = client.get(thread + "/run-status", params={"client_request_id": "req-1"}).json()
        assert status["status"] == "completed" and status["archived"] is True
