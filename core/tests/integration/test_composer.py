"""Real API, archive and prompt boundaries for composer commands and references."""

import io
import json
import sys
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest
from docx import Document
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage
from pydantic import Field

from app.agent.graph import build_graph_config
from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings
from app.conversations import ConversationTurn
from tests.support.legacy_app import create_app


class RecordingModel(FakeMessagesListChatModel):
    seen: list = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.seen.append(deepcopy(messages))
        result = super()._generate(messages, *args, **kwargs)
        for generation in result.generations:
            generation.message = generation.message.model_copy(update={"id": str(uuid4())})
        return result


@pytest.fixture
def context(tmp_path):
    model = RecordingModel(responses=[AIMessage(content="Recorded answer")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"title": "Composer test"}).json()["thread_id"]
        settings = client.portal.call(app.state.reference_settings_for, "local-user", thread)
        yield client, app, model, thread, settings.workspace_path


def reference_text(model):
    return next(item.text for item in model.seen[-1] if item.name == "explicit_reference")


def test_workspace_search_reference_l3_and_archive_reset(context):
    client, app, model, thread, root = context
    (root / "reports").mkdir()
    (root / "reports" / "notes.md").write_text(
        "EXPLICIT_FILE_MARKER " + "文" * 10000, encoding="utf-8"
    )
    folders = client.get("/api/v1/references/folders", params={"thread_id": thread}).json()["items"]
    assert folders[0]["path"] == "reports"
    files = client.get(
        "/api/v1/references/files", params={"thread_id": thread, "q": "notes"}
    ).json()["items"]
    assert files[0]["id"] == "reports/notes.md"
    selected = [{"kind": "folder", "path": "reports"}]
    result = client.post(
        "/api/v1/chat", json={"thread_id": thread, "message": "List folder", "references": selected}
    )
    assert result.status_code == 200, result.text
    assert "notes.md" in reference_text(model) and "EXPLICIT_FILE_MARKER" not in reference_text(
        model
    )
    first_prefix = model.seen[-1][0].text
    result = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read file",
            "references": [{"kind": "file", "id": files[0]["id"], "source": "workspace"}],
        },
    ).json()
    assert "EXPLICIT_FILE_MARKER" in reference_text(model)
    assert model.seen[-1][0].text == first_prefix
    assert result["references"][0]["truncated"]
    assert result["used_reference_ids"] == ["file:workspace:reports/notes.md"]
    saved = client.get(f"/api/v1/threads/{thread}/messages").json()["items"][-1]
    assert saved["references"] == result["references"]
    client.post("/api/v1/chat", json={"thread_id": thread, "message": "No references now"})
    assert not any(item.name == "explicit_reference" for item in model.seen[-1])
    assert "EXPLICIT_FILE_MARKER" not in "\n".join(item.text for item in model.seen[-1])


@pytest.mark.parametrize(
    "path", ["../outside.md", ".env", ".git/config", "C:/Windows/win.ini", ".slothy/artifacts"]
)
def test_invalid_private_or_escaping_reference_never_calls_model(context, path):
    client, _, model, thread, _ = context
    result = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read",
            "references": [{"kind": "file", "id": path, "source": "workspace"}],
        },
    )
    assert result.status_code == 422
    assert not model.seen


def test_session_and_disabled_plugin_reference_do_not_grant_capabilities(context):
    client, app, model, thread, _ = context
    source = client.post("/api/v1/threads", json={"title": "Source conversation"}).json()[
        "thread_id"
    ]
    for index in range(8):
        response = client.post(
            "/api/v1/chat",
            json={"thread_id": source, "message": f"SOURCE_TURN_{index} " + "长" * 1000},
        )
        assert response.status_code == 200, response.text
    sessions = client.get("/api/v1/references/sessions?q=SOURCE_TURN_7").json()["items"]
    assert sessions[0]["id"] == source and sessions[0]["message_count"] == 8
    payload = {
        "mcpServers": {
            "reference-plugin": {
                "command": sys.executable,
                "args": ["-c", "print('NOT_EXECUTED')"],
                "env": {"REFERENCE_SECRET": "SENTINEL_SECRET"},
            }
        }
    }
    imported = client.post(
        "/api/v1/connectors/import?kind=mcp&filename=plugin.json",
        content=json.dumps(payload).encode(),
        headers={"Content-Type": "application/octet-stream"},
    )
    assert imported.status_code == 201, imported.text
    plugin = imported.json()["items"][0]
    result = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Use these references",
            "references": [
                {"kind": "session", "id": source},
                {"kind": "plugin", "id": plugin["id"]},
            ],
        },
    )
    assert result.status_code == 200, result.text
    text = reference_text(model)
    assert "SOURCE_TURN_7" in text and "SOURCE_TURN_0" not in text
    assert "SENTINEL_SECRET" not in text and "NOT_EXECUTED" not in text
    assert result.json()["references"][1]["status"] == "disabled"
    assert not result.json()["metrics"] or not result.json()["metrics"]["tools"]
    assert not next(item for item in app.state.connectors.list() if item["id"] == plugin["id"])[
        "enabled"
    ]


def test_attachment_ownership_and_reference_budget(context):
    client, app, model, thread, root = context
    other = client.post("/api/v1/threads", json={}).json()["thread_id"]
    attached = client.post(
        f"/api/v1/threads/{other}/attachments?filename=other.txt", content=b"OTHER_ATTACHMENT"
    ).json()
    bad = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read",
            "references": [{"kind": "file", "id": attached["id"], "source": "attachment"}],
        },
    )
    assert bad.status_code == 422 and not model.seen
    refs = []
    for index in range(8):
        name = f"long-{index}.txt"
        (root / name).write_text("多" * 20000, encoding="utf-8")
        refs.append({"kind": "file", "id": name, "source": "workspace"})
    metadata, text = client.portal.call(app.state.references.resolve, "local-user", thread, refs)
    assert len(text) <= 12000 and sum(item["included"] for item in metadata) < 8
    duplicate = client.post(
        "/api/v1/chat",
        json={"thread_id": thread, "message": "Read", "references": [refs[0], refs[0]]},
    )
    assert duplicate.status_code == 422


@pytest.mark.parametrize("format", ["md", "docx"])
def test_confirmed_export_reuses_template_journal_and_download(context, format):
    client, app, model, thread, _ = context
    client.post("/api/v1/chat", json={"thread_id": thread, "message": "EXPORT_SOURCE"})
    before = len(model.seen)
    endpoint = f"/api/v1/threads/{thread}/export"
    assert client.post(endpoint, json={"confirmed": False, "format": format}).status_code == 409
    request = {"confirmed": True, "format": format, "request_id": str(uuid4())}
    exported = client.post(endpoint, json=request)
    assert exported.status_code == 200, exported.text
    result = exported.json()
    assert result["ok"] and result["data"]["exported_turns"] == 1
    artifact = result["artifacts"][0]
    download = client.get(artifact["url"])
    assert download.status_code == 200
    if format == "md":
        assert b"EXPORT_SOURCE" in download.content
    else:
        assert "EXPORT_SOURCE" in "\n".join(
            item.text for item in Document(io.BytesIO(download.content)).paragraphs
        )
    again = client.post(endpoint, json=request).json()
    assert again["data"]["reused"] and again["artifacts"] == result["artifacts"]
    assert len(model.seen) == before
    usage = client.get("/api/v1/account/activity").json()
    assert usage["tool_calls"] == 0


def test_compact_changes_only_context_and_preserves_archive(context):
    client, app, model, thread, _ = context
    for index in range(5):
        response = client.post(
            "/api/v1/chat", json={"thread_id": thread, "message": f"COMPACT_TURN_{index}"}
        )
        assert response.status_code == 200, response.text
    prefix = model.seen[-1][0].text
    endpoint = f"/api/v1/threads/{thread}/compact"
    assert client.post(endpoint, json={"confirmed": False}).status_code == 409
    before = len(model.seen)
    result = client.post(endpoint, json={"confirmed": True})
    assert result.status_code == 200, result.text
    assert result.json()["compacted"] and result.json()["version"] == 1
    config = build_graph_config(
        {"user_id": "local-user", "thread_id": thread}, app.state.agent_settings
    )
    saved = client.portal.call(app.state.runner.graph.aget_state, config)
    assert sum(isinstance(item, HumanMessage) for item in saved.values["messages"]) == 2
    assert saved.values["history_summary"]["through_turn"] == 3
    assert len(model.seen) == before
    assert len(client.get(f"/api/v1/threads/{thread}/messages").json()["items"]) == 5
    response = client.post("/api/v1/chat", json={"thread_id": thread, "message": "After compact"})
    assert response.status_code == 200, response.text
    assert model.seen[-1][0].text == prefix
    assert any("COMPACT_TURN_0" in item.text for item in model.seen[-1])


def test_reference_search_uses_existing_authorization_and_bounded_query(context, monkeypatch):
    client, _, _, thread, _ = context
    monkeypatch.setenv("SLOTHY_API_TOKEN", "composer-test-key")
    for kind in ("folders", "files", "sessions", "plugins"):
        endpoint = "/api/v1/references/" + kind
        assert client.get(endpoint).status_code == 401
        assert (
            client.get(
                endpoint + "?limit=9", headers={"X-Slothy-Token": "composer-test-key"}
            ).status_code
            == 422
        )
        assert (
            client.get(
                endpoint,
                headers={"X-Slothy-Token": "composer-test-key", "Origin": "https://example.com"},
            ).status_code
            == 403
        )


def test_foreign_session_and_invalidated_reference_metadata(context):
    client, app, model, thread, root = context

    async def foreign():
        await app.state.conversations.create_thread(
            "foreign-user", "foreign-thread", "FOREIGN", "chat"
        )
        await app.state.conversations.save_turn(
            ConversationTurn(
                "foreign-user", "foreign-thread", "foreign-run", "FOREIGN_SECRET", "Private answer"
            )
        )

    client.portal.call(foreign)
    assert not client.get("/api/v1/references/sessions?q=FOREIGN").json()["items"]
    rejected = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Reference",
            "references": [{"kind": "session", "id": "foreign-thread"}],
        },
    )
    assert rejected.status_code == 422 and not model.seen
    target = root / "revocable.txt"
    target.write_text("REVOCABLE_CONTENT", encoding="utf-8")
    posted = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read reference",
            "references": [{"kind": "file", "id": target.name, "source": "workspace"}],
        },
    )
    assert posted.status_code == 200, posted.text
    target.unlink()
    saved = client.get(f"/api/v1/threads/{thread}/messages").json()["items"][-1]
    assert saved["references"][0]["status"] == "missing"
    forked = client.post(f"/api/v1/threads/{thread}/fork", json={"title": "Fork"})
    assert forked.status_code == 201, forked.text
    rows = client.get(f"/api/v1/threads/{forked.json()['thread_id']}/messages").json()["items"]
    assert rows[0]["references"][0]["label"] == target.name


def test_command_audit_and_reference_validation_counts_are_separate_from_tools(context):
    client, _, model, thread, root = context
    empty = client.get("/api/v1/commands/usage").json()
    assert empty["references"]["success_rate"] is None
    assert (
        client.post(
            "/api/v1/commands/events", json={"name": "help", "thread_id": thread}
        ).status_code
        == 200
    )
    assert client.post("/api/v1/commands/events", json={"name": "run_command"}).status_code == 422
    assert not model.seen
    (root / "valid.txt").write_text("VALID", encoding="utf-8")
    client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read",
            "references": [{"kind": "file", "id": "valid.txt", "source": "workspace"}],
        },
    )
    client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "message": "Read",
            "references": [{"kind": "file", "id": "missing.txt", "source": "workspace"}],
        },
    )
    usage = client.get("/api/v1/commands/usage").json()
    assert usage["commands"] == [{"name": "help", "calls": 1}]
    assert usage["references"]["attempts"] == 2 and usage["references"]["success_rate"] == 0.5
    assert client.get("/api/v1/account/activity").json()["tool_calls"] == 0


def test_approval_reload_and_decline_preserve_reference_identity(context):
    client, _, model, _, root = context
    model.i = 0
    model.responses = [
        AIMessage(
            content="",
            tool_calls=[
                {
                    "name": "workspace_write",
                    "id": "review-write",
                    "args": {
                        "action": "write_file",
                        "path": "should-not-exist.txt",
                        "content": "approved only",
                    },
                }
            ],
        ),
        AIMessage(content="Declined"),
    ]
    (root / "review-reference.txt").write_text("REFERENCE_WITH_REVIEW", encoding="utf-8")
    thread = client.post("/api/v1/threads", json={"mode": "work"}).json()["thread_id"]
    result = client.post(
        "/api/v1/chat",
        json={
            "thread_id": thread,
            "mode": "work",
            "message": "Use reference",
            "references": [{"kind": "file", "id": "review-reference.txt", "source": "workspace"}],
        },
    )
    assert result.status_code == 202, result.text
    review = result.json()
    reloaded = client.get(f"/api/v1/threads/{thread}/approval").json()
    assert reloaded["references"] == review["references"]
    assert reloaded["used_reference_ids"] == ["file:workspace:review-reference.txt"]
    ended = client.post(
        f"/api/v1/threads/{thread}/approval/{review['approval_id']}", json={"approved": False}
    )
    assert ended.status_code == 200, ended.text
    assert ended.json()["message"] == "Use reference"
    assert ended.json()["references"] == review["references"]
    assert not (root / "should-not-exist.txt").exists()
