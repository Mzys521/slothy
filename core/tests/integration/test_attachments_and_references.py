"""Attachment metadata reads and runtime reference revalidation (F05, F08).

/ 附件元数据读取与运行时引用重验（F05、F08）。
"""

import json
from io import BytesIO
from types import SimpleNamespace

import pytest
from docx import Document
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from pypdf import PdfWriter

from app import api
from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from app.tools.workspace import WorkspaceFiles
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


@pytest.fixture(params=["pdf", "docx", "pptx"])
def office_document(request):
    stream = BytesIO()
    if request.param == "pdf":
        canvas = pytest.importorskip("reportlab.pdfgen.canvas").Canvas(stream)
        canvas.drawString(40, 770, "First document marker")
        canvas.showPage()
        canvas.drawString(40, 770, "Second document marker")
        canvas.save()
    elif request.param == "docx":
        document = Document()
        document.add_paragraph("First document marker")
        document.add_table(1, 1).cell(0, 0).text = "Second document marker"
        document.save(stream)
    else:
        pptx = pytest.importorskip("pptx")
        deck = pptx.Presentation()
        slide = deck.slides.add_slide(deck.slide_layouts[5])
        slide.shapes.title.text = "First document marker"
        slide = deck.slides.add_slide(deck.slide_layouts[6])
        table = slide.shapes.add_table(1, 1, 900000, 900000, 4000000, 800000).table
        table.cell(0, 0).text = "Second document marker"
        deck.save(stream)
    return f"document.{request.param}", stream.getvalue()


def test_document_text_reaches_agent_from_upload_and_workspace(tmp_path, office_document):
    filename, content = office_document
    workspace = tmp_path / "artifacts"
    workspace.mkdir()
    (workspace / filename).write_bytes(content)
    settings = ToolSettings(_env_file=None, workspace_path=workspace)
    model = RecordingModel(
        responses=[
            AIMessage(content="Read the attachment"),
            AIMessage(content="Read the reference"),
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_read",
                        "args": {"action": "read", "path": filename},
                        "id": "read-document",
                    }
                ],
            ),
            AIMessage(content="Read the workspace file"),
        ]
    )
    app = chat_app(tmp_path, model, work_tools=create_builtin_tools(settings))
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        uploaded = client.post(
            BASE + f"/threads/{thread}/attachments", params={"filename": filename}, content=content
        )
        assert uploaded.status_code == 201, uploaded.text
        result = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "Read the attachment",
                "attachment_ids": [uploaded.json()["id"]],
            },
        )
        assert result.status_code == 200, result.text
        attachment = next(m.text for m in model.inputs[-1] if m.name == "attachment_reference")
        assert attachment.index("First document marker") < attachment.index(
            "Second document marker"
        )

        referenced = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "Read the workspace reference",
                "references": [{"kind": "file", "source": "workspace", "id": filename}],
            },
        )
        assert referenced.status_code == 200, referenced.text
        reference = next(m.text for m in model.inputs[-1] if m.name == "explicit_reference")
        assert "First document marker" in reference and "Second document marker" in reference

        tool_reply = client.post(
            BASE + "/chat", json={"thread_id": thread, "message": f"Read {filename}"}
        )
        assert tool_reply.status_code == 200, tool_reply.text
        tool_text = next(m.text for m in model.inputs[-1] if m.type == "tool")
        assert "First document marker" in tool_text and "Second document marker" in tool_text

        files = WorkspaceFiles(settings)
        first = client.portal.call(files.read, "read", filename, 1, 0, 10)
        assert first["next_offset"] == 10
        second = client.portal.call(files.read, "read", filename, 1, first["next_offset"], 8000)
        assert second["next_offset"] is None
        assert "First document marker" in first["data"]["text"] + second["data"]["text"]


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


def test_pdf_without_extractable_text_reports_ocr_requirement(tmp_path):
    stream = BytesIO()
    document = PdfWriter()
    document.add_blank_page(width=612, height=792)
    document.write(stream)
    model = RecordingModel(responses=[AIMessage(content="unused")])
    with TestClient(chat_app(tmp_path, model)) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        response = client.post(
            BASE + f"/threads/{thread}/attachments?filename=scan.pdf", content=stream.getvalue()
        )
        assert response.status_code == 422
        assert response.json()["code"] == "ATTACHMENT_UNREADABLE"
        assert "OCR" in response.json()["message"]


def test_large_upload_keeps_a_preview_in_a_smaller_context(tmp_path, monkeypatch):
    monkeypatch.setenv("AGENT_CONTEXT_WINDOW_DEFAULT", "8000")
    monkeypatch.setenv("AGENT_OUTPUT_TOKEN_RESERVE", "1024")
    model = RecordingModel(responses=[AIMessage(content="Read the beginning")])
    content = b"Opening paragraph\n" + b"Continuation.\n" * 5000
    with TestClient(chat_app(tmp_path, model)) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        response = client.post(
            BASE + f"/threads/{thread}/attachments?filename=long.txt", content=content
        )
        assert response.status_code == 201, response.text
        result = client.post(
            BASE + "/chat",
            json={
                "thread_id": thread,
                "message": "Read this file",
                "attachment_ids": [response.json()["id"]],
            },
        )
        assert result.status_code == 200, result.text
        reference = next(m for m in model.inputs[-1] if m.name == "attachment_reference")
        (preview,) = json.loads(reference.text)
        assert preview["text"].startswith("Opening paragraph")
        assert len(preview["text"]) == preview["next_offset"] < preview["total_chars"]


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
