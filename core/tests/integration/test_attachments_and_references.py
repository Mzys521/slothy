"""Attachment metadata reads and runtime reference revalidation (F05, F08).

/ 附件元数据读取与运行时引用重验（F05、F08）。
"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app import api
from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class RecordingModel(FakeMessagesListChatModel):
    """Fake model recording every provider input. / 记录每次提供商输入的假模型。"""

    inputs: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        self.inputs = [*self.inputs, messages]
        return await super()._agenerate(messages, *args, **kwargs)


def chat_app(tmp_path, model, **overrides):
    """Build an app with a failing-safe offline model. / 构建使用离线假模型的应用。"""
    return create_app(
        checkpoint_settings=CheckpointSettings(
            sqlite_path=tmp_path / "attachments.sqlite", _env_file=None
        ),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        **overrides,
    )


def test_upload_rejects_an_unknown_thread_before_parsing(tmp_path):
    model = RecordingModel(responses=[AIMessage(content="unused")])
    app = chat_app(tmp_path, model)
    with TestClient(app) as client:
        response = client.post(
            BASE + "/threads/missing/attachments?filename=notes.txt", content=b"hello"
        )
        assert response.status_code == 404 and response.json()["code"] == "NOT_FOUND"


def test_corrupt_pdf_uses_the_documented_422_contract(tmp_path):
    model = RecordingModel(responses=[AIMessage(content="unused")])
    app = chat_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        response = client.post(
            BASE + f"/threads/{thread}/attachments?filename=broken.pdf",
            content=b"%PDF-1.7\n1 0 obj<< /Type /Catalog >>",
        )
        assert response.status_code == 422
        assert response.json()["code"] == "ATTACHMENT_UNREADABLE"


def test_quota_is_checked_before_buffering_the_body(tmp_path, monkeypatch):
    model = RecordingModel(responses=[AIMessage(content="unused")])
    monkeypatch.setattr(api, "MAX_THREAD_UPLOAD_BYTES", 10)
    app = chat_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        path = BASE + f"/threads/{thread}/attachments?filename=notes.txt"
        assert client.post(path, content=b"x" * 20).status_code == 201
        # The stored usage already exceeds the (patched) thread quota, so the next upload is
        # rejected before its body is buffered. / 已用容量超过配额，下一次上传在缓冲正文前被拒绝。
        refused = client.post(path, content=b"y" * 20)
        assert refused.status_code == 413
        assert refused.json()["code"] == "ATTACHMENT_QUOTA"
        assert len(client.get(BASE + "/attachments").json()["items"]) == 1


def test_file_request_id_makes_upload_retries_idempotent(tmp_path):
    model = RecordingModel(responses=[AIMessage(content="unused")])
    app = chat_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        path = BASE + f"/threads/{thread}/attachments?filename=notes.txt&file_request_id=upload-1"
        first = client.post(path, content=b"first body")
        assert first.status_code == 201
        again = client.post(path, content=b"first body")
        assert again.status_code == 201
        assert again.json()["id"] == first.json()["id"]
        library = client.get(BASE + "/attachments").json()["items"]
        assert [item["id"] for item in library] == [first.json()["id"]]


def test_history_marks_a_removed_attachment_without_failing(tmp_path):
    model = RecordingModel(responses=[AIMessage(content="read it")])
    app = chat_app(tmp_path, model)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        kept = client.post(
            BASE + f"/threads/{thread}/attachments?filename=kept.txt", content=b"KEEP"
        ).json()
        removed = client.post(
            BASE + f"/threads/{thread}/attachments?filename=removed.txt", content=b"REMOVED"
        ).json()
        reply = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "read",
                "attachment_ids": [kept["id"], removed["id"]],
            },
        )
        assert reply.status_code == 200, reply.text
        # Simulate a later clear that removed only one file of an older turn.
        # 模拟后续清理只删除了旧轮次中的一个文件。
        import asyncio

        asyncio.run(
            app.state.conversations._connection.execute(
                "DELETE FROM conversation_files WHERE id=?", (removed["id"],)
            )
        )
        page = client.get(BASE + f"/threads/{thread}/messages")
        assert page.status_code == 200
        attachments = page.json()["items"][0]["attachments"]
        assert [item["id"] for item in attachments] == [kept["id"], removed["id"]]
        assert attachments[0]["missing"] is False
        assert attachments[1]["missing"] is True


def test_reference_removed_during_approval_keeps_the_run_alive(tmp_path):
    workspace = tmp_path / "artifacts"
    workspace.mkdir()
    (workspace / "keep.txt").write_text("REFERENCE-B body", encoding="utf-8")
    (workspace / "gone.txt").write_text("REFERENCE-A body", encoding="utf-8")
    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_write",
                        "args": {"action": "mkdir", "path": "approved-folder"},
                        "id": "mkdir-1",
                    }
                ],
            ),
            AIMessage(content="用剩下的证据回答"),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(
            sqlite_path=tmp_path / "refs.sqlite", _env_file=None
        ),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(
            ToolSettings(_env_file=None, workspace_path=workspace)
        ),
    )
    with TestClient(app) as client:
        review = client.post(
            BASE + "/chat",
            json={
                "message": "compare both files",
                "mode": "work",
                "approval_policy": "ask",
                "references": [
                    {"kind": "file", "source": "workspace", "id": "keep.txt"},
                    {"kind": "file", "source": "workspace", "id": "gone.txt"},
                ],
            },
        )
        assert review.status_code == 202, review.text
        review = review.json()
        # The accepted reference disappears while the run waits for approval.
        # 已接受的引用在运行等待审批期间被删除。
        (workspace / "gone.txt").unlink()
        finished = client.post(
            BASE + f"/threads/{review['thread_id']}/approval/{review['approval_id']}",
            json={"approved": True},
        )
        assert finished.status_code == 200, finished.text
        result = finished.json()
        assert result["response"] == "用剩下的证据回答"
        assert result["run_id"] == review["run_id"]
        last_input = json.dumps(model.inputs[-1], ensure_ascii=False, default=str)
        assert "REFERENCE-B body" in last_input
        assert "REFERENCE-A body" not in last_input
        assert "could not be read" in last_input
        assert "gone.txt" in last_input and "keep.txt" in last_input
        assert [item for item in result["used_reference_ids"]] == ["file:workspace:keep.txt"]
        page = client.get(BASE + f"/threads/{review['thread_id']}/messages").json()
        references = {item["id"]: item for item in page["items"][0]["references"]}
        assert references["gone.txt"]["included"] is False
        assert references["gone.txt"]["status"] == "missing"
        assert references["keep.txt"]["status"] == "ok"
        tools = [item for item in page["items"][0]["activity"] if item["kind"] == "tool"]
        assert tools and tools[0]["status"] == "done"
