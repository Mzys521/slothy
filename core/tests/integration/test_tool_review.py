import json
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, ToolMessage

from app import api
from app.agent.graph import build_graph_config
from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from app.infrastructure.llm.catalog import ModelItem
from tests.support.legacy_app import create_app


class ToolModel(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def events(response, name):
    prefix = f"event: {name}\ndata: "
    return [
        json.loads(frame.removeprefix(prefix))
        for frame in response.text.split("\n\n")
        if frame.startswith(prefix)
    ]


def test_selected_model_survives_approval_and_archives_usage(tmp_path, monkeypatch):
    usage = {
        "input_tokens": 100,
        "output_tokens": 25,
        "total_tokens": 125,
        "input_token_details": {"cache_read": 60},
    }
    models = {
        "deepseek-flash": ToolModel(responses=[AIMessage(content="DeepSeek")]),
        "mimo-v2.6-flash": ToolModel(
            responses=[
                AIMessage(
                    content="",
                    tool_calls=[
                        {
                            "name": "calculate",
                            "args": {"operation": "add", "a": 1, "b": 2},
                            "id": "sum",
                        }
                    ],
                    usage_metadata=usage,
                ),
                AIMessage(content="MiMo: 3", usage_metadata=usage),
            ]
        ),
    }
    monkeypatch.setattr(
        api,
        "configured_models",
        lambda: {
            model: (
                ModelItem(
                    id=model,
                    provider=provider,
                    context_window=1_000_000,
                    reasoning_levels=("none", "high"),
                    default_effort="none",
                ),
                model,
            )
            for model, provider in (("deepseek-flash", "deepseek"), ("mimo-v2.6-flash", "mimo"))
        },
    )
    selected = []

    def provider(model, *, effort):
        selected.append((model, effort))
        return SimpleNamespace(get_chat_model=lambda: models[model])

    monkeypatch.setattr(api, "OpenAICompatibleProvider", provider)
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "selection.sqlite"),
        work_tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path / "artifacts")),
    )
    with TestClient(app) as client:
        base = "/api/v1"
        invalid = client.post(
            base + "/chat",
            json={
                "message": "test",
                "model": "mimo-v2.6-flash",
                "reasoning_effort": "max",
            },
        )
        assert invalid.status_code == 422
        initial = client.post(
            base + "/chat",
            json={
                "message": "Calculate 1 + 2",
                "model": "mimo-v2.6-flash",
                "reasoning_effort": "high",
                "approval_policy": "every",
            },
        )
        assert initial.status_code == 202, initial.text
        review = initial.json()
        assert review["metrics"]["total_tokens"] == 125
        thread = f"{base}/threads/{review['thread_id']}"
        reply = client.post(thread + "/approval/" + review["approval_id"], json={"approved": True})
        assert reply.status_code == 200, reply.text
        result = reply.json()
        assert result["response"] == "MiMo: 3"
        assert result["metrics"]["model"] == "mimo-v2.6-flash"
        assert result["metrics"]["total_tokens"] == 250
        assert result["metrics"]["cached_input_tokens"] == 120
        assert client.get(thread + "/messages").json()["items"][0]["metrics"] == result["metrics"]
        assert selected.count(("mimo-v2.6-flash", "high")) == 1


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_agent_can_read_previously_uploaded_file(tmp_path, mode):
    model = ToolModel(responses=[AIMessage(content="Ready")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "work.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path / "artifacts")),
    )
    with TestClient(app) as client:
        base = "/api/v1"
        runtime = client.get(base + "/runtime").json()
        assert runtime["chat_tools"] == [
            "calculate",
            "calculate_financial",
            "workspace_read",
            "workspace_delete",
            "export_document",
            "get_datetime",
            "list_uploaded_files",
            "read_uploaded_file",
        ]
        thread = client.post(base + "/threads", json={"mode": mode}).json()["thread_id"]
        upload = client.post(
            f"{base}/threads/{thread}/attachments",
            params={"filename": "notes.txt"},
            content=b"x" * 60_000 + b"Only the scoped reader may return this.",
        ).json()
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_uploaded_file",
                        "args": {"file_id": upload["id"], "offset": 60_000},
                        "id": "read",
                    }
                ],
            ),
            AIMessage(content="Read it.", additional_kwargs={"reasoning_content": "Private"}),
        ]
        reply = client.post(
            base + "/chat", json={"thread_id": thread, "message": "Read my file", "stream": True}
        )
        assert reply.status_code == 200, reply.text
        progress = events(reply, "activity")
        assert any(
            entry["kind"] == "model" and entry["status"] == "running"
            for frame in progress
            for entry in frame["items"]
        )
        assert [
            entry["status"]
            for frame in progress
            for entry in frame["items"]
            if entry["kind"] == "tool"
        ][:3] == ["requested", "running", "done"]
        (done,) = events(reply, "done")
        assert done["response"] == "Read it."
        read = next(entry for entry in done["activity"] if entry["kind"] == "tool")
        assert read["name"] == "read_uploaded_file"
        assert json.loads(read["arguments"]) == {"file_id": upload["id"], "offset": 60_000}
        assert "Only the scoped reader may return this." in read["result"]
        assert all("Private" not in frame["text"] for frame in events(reply, "delta"))
        assert done["reasoning"] == "Private"
        (saved,) = client.get(f"{base}/threads/{thread}/messages").json()["items"]
        assert saved["reasoning"] == "Private"
        assert "Private" not in saved["response"]
        assert saved["activity"] == done["activity"]
        config = build_graph_config(
            {"user_id": "local-user", "thread_id": thread}, app.state.agent_settings
        )
        runner = app.state.work_runner if mode == "work" else app.state.runner
        snapshot = client.portal.call(runner.graph.aget_state, config)
        output = next(item for item in snapshot.values["messages"] if isinstance(item, ToolMessage))
        assert "Only the scoped reader may return this." in output.content
        model.responses[:] = [
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "read_uploaded_file",
                        "args": {"file_id": "missing"},
                        "id": "missing",
                    }
                ],
            ),
            AIMessage(content="The file could not be read."),
        ]
        failed = client.post(
            base + "/chat",
            json={
                "thread_id": thread,
                "message": "Read the other file",
                "stream": True,
            },
        )
        (completed,) = events(failed, "done")
        (failed_tool,) = [entry for entry in completed["activity"] if entry["kind"] == "tool"]
        assert failed_tool["status"] == "error"
        assert "Only the scoped reader may return this." not in failed.text


def request_mkdir(path):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "workspace_write", "args": {"action": "mkdir", "path": path}, "id": path}
        ],
    )


def test_pending_review_persists_and_cannot_be_bypassed(tmp_path):
    tool_settings = ToolSettings(workspace_path=tmp_path / "artifacts")
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite")
    model = ToolModel(
        responses=[
            request_mkdir("rejected"),
            AIMessage(content="Declined."),
            request_mkdir("approved"),
            AIMessage(content="Created."),
        ]
    )
    tools = create_builtin_tools(tool_settings)
    app = create_app(
        checkpoint_settings=checkpoint,
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=tools,
    )
    with TestClient(app) as client:
        runtime = client.get("/api/v1/runtime")
        assert runtime.status_code == 200
        assert runtime.json()["ready"] is True
        assert runtime.json()["work_tools"] == [
            *(
                tool.name
                for tool in tools
                if tool.descriptor.enabled and not tool.descriptor.deprecated
            ),
            "list_uploaded_files",
            "read_uploaded_file",
        ]
        response = client.post("/api/v1/chat", json={"mode": "work", "message": "Make a folder"})
        assert response.status_code == 202, response.text
        review = response.json()
        assert review["used_memory_ids"] == []
        assert review["activity"][-1]["status"] == "approval"
        thread = "/api/v1/threads/" + review["thread_id"]
        assert not (tool_settings.workspace_path / "rejected").exists()
        assert client.get(thread + "/messages").json()["items"] == []
        assert client.post(thread + "/resume").json()["code"] == "REVIEW_REQUIRED"
        assert client.post(thread + "/approval/stale", json={"approved": True}).status_code == 409
    with TestClient(app) as client:
        assert client.get(thread + "/approval").json() == review
        decision_path = thread + "/approval/" + review["approval_id"]
        assert client.post(decision_path, json={"approved": "true"}).status_code == 422
        denied = client.post(decision_path, json={"approved": False})
        assert denied.status_code == 200, denied.text
        assert (
            next(entry for entry in denied.json()["activity"] if entry["kind"] == "tool")["status"]
            == "rejected"
        )
        assert not (tool_settings.workspace_path / "rejected").exists()
        again = client.post(
            "/api/v1/chat",
            json={
                "thread_id": review["thread_id"],
                "message": "Make another folder",
                "stream": True,
            },
        )
        assert "event: approval_required" in again.text
        assert "event: done" not in again.text
        next_review = client.get(thread + "/approval").json()
        assert events(again, "approval_required") == [next_review]
        assert not (tool_settings.workspace_path / "approved").exists()
        assert client.post(decision_path, json={"approved": True}).status_code == 409
        approved = client.post(
            thread + "/approval/" + next_review["approval_id"] + "?stream=true",
            json={
                "approved": True,
            },
        )
        assert approved.status_code == 200, approved.text
        (completed,) = events(approved, "done")
        assert completed["response"] == "Created."
        assert next(entry for entry in completed["activity"] if entry["kind"] == "tool")["approved"]
        assert any(
            entry["kind"] == "tool" and entry["status"] == "running"
            for frame in events(approved, "activity")
            for entry in frame["items"]
        )
        assert (tool_settings.workspace_path / "approved").is_dir()
        assert client.get(thread + "/approval").json() is None
        assert len(client.get(thread + "/messages").json()["items"]) == 2


def test_every_policy_also_pauses_read_only_calls(tmp_path):
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "calculate", "args": {"operation": "add", "a": 2, "b": 3}, "id": "sum"}
                ],
            ),
            AIMessage(content="Five."),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(ToolSettings(workspace_path=tmp_path / "artifacts")),
    )
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/chat",
            json={
                "mode": "work",
                "message": "Add two and three",
                "approval_policy": "every",
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["calls"][0]["name"] == "calculate"


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_denied_delete_is_not_executed_and_run_continues(tmp_path, mode):
    """A denied high-risk call never executes and the same run resumes.
    / 拒绝后工具不执行并继续运行。"""
    workspace = tmp_path / "artifacts"
    workspace.mkdir()
    target = workspace / "keep.txt"
    target.write_text("keep", encoding="utf-8")
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_delete",
                        "args": {"path": "keep.txt"},
                        "id": "delete-call",
                    }
                ],
            ),
            AIMessage(content="The deletion was denied; nothing was removed."),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "deny.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(ToolSettings(workspace_path=workspace)),
    )
    with TestClient(app) as client:
        review = client.post(
            "/api/v1/chat",
            json={
                "message": "delete keep.txt",
                "approval_policy": "ask",
                "mode": mode,
            },
        )
        assert review.status_code == 202, review.text
        assert review.json()["calls"][0]["risk"] == "delete"
        assert review.json()["calls"][0]["irreversible"] is True
        thread = f"/api/v1/threads/{review.json()['thread_id']}"
        restored = client.get(thread + "/approval").json()
        assert restored["calls"][0]["irreversible"] is True
        result = client.post(
            thread + "/approval/" + review.json()["approval_id"],
            json={"approved": False},
        )
        assert result.status_code == 200, result.text
        payload = result.json()
        assert target.exists(), "denied delete must not touch the filesystem"
        assert "denied" in payload["response"].lower()
        rejected = [
            entry for entry in payload["activity"] if entry.get("name") == "workspace_delete"
        ]
        assert rejected and rejected[0]["status"] == "rejected"
        assert rejected[0]["risk"] == "delete"


@pytest.mark.parametrize("mode", ["chat", "work"])
def test_delete_requires_review_then_approved_result_keeps_delete_label(tmp_path, mode):
    workspace = tmp_path / "files"
    workspace.mkdir()
    target = workspace / "remove.txt"
    target.write_text("temporary test content", encoding="utf-8")
    model = ToolModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {"name": "workspace_delete", "args": {"path": "remove.txt"}, "id": "delete"}
                ],
            ),
            AIMessage(content="Deleted."),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "approval.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        work_tools=create_builtin_tools(ToolSettings(_env_file=None, workspace_path=workspace)),
    )
    with TestClient(app) as client:
        response = client.post("/api/v1/chat", json={"message": "Delete remove.txt", "mode": mode})
        assert response.status_code == 202 and target.exists()
        review = response.json()
        assert review["calls"][0]["risk"] == "delete"
        assert review["calls"][0]["irreversible"] is True
        thread = f"/api/v1/threads/{review['thread_id']}"
        completed = client.post(
            thread + "/approval/" + review["approval_id"], json={"approved": True}
        )
        assert completed.status_code == 200 and not target.exists(), completed.text
        entry = next(item for item in completed.json()["activity"] if item["kind"] == "tool")
        assert entry["risk"] == "delete" and entry["status"] == "done"
        archived = client.get(thread + "/messages").json()["items"][0]
        assert archived["activity"] == completed.json()["activity"]
