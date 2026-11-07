"""Exercise HTTP contracts against real SQLite stores and an offline model."""

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.graph import build_graph_config
from app.config import CheckpointSettings
from tests.support.legacy_app import create_app
from tests.support.profiles import attributes


def test_workspace_contract_and_legacy_thread_updates(tmp_path):
    app = create_app(checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "api.sqlite"))
    with TestClient(app) as client:
        prefix = "/api/v1"
        workspace = prefix + "/workspaces"
        thread = prefix + "/threads"
        assert client.get(workspace).json()["items"][0]["id"] == "personal"
        project = client.post(workspace, json={"name": "Project"}).json()["id"]
        assert client.patch(f"{workspace}/{project}", json={"name": "Renamed"}).status_code == 200
        assert client.get(f"{workspace}/{project}").json()["name"] == "Renamed"
        assert client.delete(workspace + "/personal").status_code == 409
        assert client.post(workspace, json={"name": "  "}).status_code == 422

        created = client.post(thread, json={})
        assert created.status_code == 201
        assert set(created.json()) == {
            "thread_id",
            "title",
            "mode",
            "archived",
            "created_at",
            "updated_at",
        }
        path = thread + "/" + created.json()["thread_id"]
        assert client.patch(path, json={"archived": True}).json()["archived"] is True
        scoped = client.post(thread, json={"workspace_id": project}).json()["thread_id"]
        updated = client.patch(
            f"{thread}/{scoped}", json={"title": "Renamed", "archived": True, "pinned": True}
        ).json()
        assert updated["title"] == "Renamed" and updated["pinned"]
        listing = client.get(thread, params={"archived": True, "workspace_id": project}).json()
        assert [row["thread_id"] for row in listing["items"]] == [scoped]
        assert client.delete(f"{workspace}/{project}").status_code == 204
        assert client.get(f"{thread}/{scoped}").status_code == 404
        assert client.get(path).status_code == 200

        schema = client.get("/openapi.json").json()
        assert "204" in schema["paths"][workspace + "/{workspace_id}"]["delete"]["responses"]
        assert "workspace_id" in schema["components"]["schemas"]["ThreadInput"]["properties"]
        chat_response = schema["paths"][prefix + "/chat"]["post"]["responses"]["200"]
        assert "text/event-stream" in chat_response["content"]


def test_uploaded_text_reaches_model_only_in_its_thread(tmp_path):
    received = []

    def reply(messages):
        received.extend(messages)
        return AIMessage(content="**Read the file.**")

    provider = SimpleNamespace(get_chat_model=lambda: RunnableLambda(reply))
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "api.sqlite"),
        model_provider=provider,
    )
    with TestClient(app) as client:
        base = "/api/v1"
        source, other = [
            client.post(base + "/threads", json={}).json()["thread_id"] for _ in range(2)
        ]
        path = f"{base}/threads/{source}"
        upload = client.post(
            path + "/attachments",
            params={"filename": "notes.md"},
            content=("独有的附件内容" + "甲" * 60_000 + "文末内容").encode(),
        )
        assert upload.status_code == 201, upload.text
        attachment_id = upload.json()["id"]
        assert (
            client.post(
                base + "/chat",
                json={
                    "thread_id": other,
                    "attachment_ids": [attachment_id],
                },
            ).status_code
            == 404
        )
        assert not received
        reply_result = client.post(
            base + "/chat",
            json={
                "thread_id": source,
                "attachment_ids": [attachment_id],
            },
        )
        assert reply_result.status_code == 200, reply_result.text
        reference = next(message for message in received if message.name == "attachment_reference")
        assert "独有的附件内容" in reference.content
        (document,) = json.loads(reference.text)
        assert document["id"] == attachment_id
        assert len(document["text"]) == document["next_offset"] == 60_000
        assert document["total_chars"] > document["next_offset"]
        assert "文末内容" not in document["text"]
        assert reply_result.json()["response"] == "**Read the file.**"
        messages = client.get(path + "/messages").json()["items"]
        assert messages[0]["attachments"] == [upload.json()]
        assert (
            client.post(
                path + "/attachments", params={"filename": "../x.md"}, content=b"no"
            ).status_code
            == 422
        )
        assert (
            client.post(
                path + "/attachments", params={"filename": "x.exe"}, content=b"no"
            ).status_code
            == 422
        )
        assert client.delete(path + "/messages").status_code == 204
        assert client.get(base + "/attachments").json()["items"] == []


def test_graph_contract_filters_service_internal_fields(tmp_path):
    app = create_app(checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "api.sqlite"))
    graph = AsyncMock(
        return_value={
            "nodes": [
                {
                    "id": "mem-1",
                    "label": "Brief",
                    "type": "preference",
                    "importance": 0.5,
                    "internal": "private",
                }
            ],
            "edges": [],
            "internal": "private",
        }
    )
    with TestClient(app) as client:
        app.state.workspace = SimpleNamespace(graph=graph)
        result = client.get("/api/v1/graph?limit=10")
        assert result.status_code == 200
        assert result.json() == {
            "nodes": [
                {
                    "id": "mem-1",
                    "label": "Brief",
                    "type": "preference",
                    "importance": 0.5,
                    "node_kind": "fact",
                    "concept_kind": None,
                    "scope": None,
                    "scope_ref": None,
                    "fact_ids": [],
                    "effective_frequency": 0,
                    "strength": 0,
                }
            ],
            "edges": [],
            "total": 1,
            "truncated": False,
            "limit": 10,
        }
        graph.assert_awaited_once_with("local-user", 10, None, thread_id="", project_id=None)


def test_chat_history_and_checkpoint_lifecycle(tmp_path):
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite")
    model = FakeListChatModel(responses=["First", "Second", "Third"])
    provider = SimpleNamespace(get_chat_model=lambda: model)
    app = create_app(checkpoint_settings=checkpoint, model_provider=provider)
    with TestClient(app) as client:
        base = "/api/v1"
        assert client.get(base + "/ready").status_code == 200
        created = client.post(base + "/threads", json={"mode": "work"})
        assert created.status_code == 201
        thread_id = created.json()["thread_id"]
        path = base + "/threads/" + thread_id
        result = client.post(base + "/chat", json={"thread_id": thread_id, "message": "Hi"})
        assert result.status_code == 200
        assert result.json()["response"] == "First"
        streamed = client.post(
            base + "/chat", json={"thread_id": thread_id, "message": "Again", "stream": True}
        )
        assert streamed.status_code == 200
        assert "event: delta" in streamed.text
        assert "event: done" in streamed.text
        assert '"response": "Second"' in streamed.text
        history = client.get(path + "/messages", params={"limit": 1}).json()
        assert [turn["response"] for turn in history["items"]] == ["First"]
        assert (
            client.get(path + "/messages", params={"after_id": history["next_cursor"]}).json()[
                "items"
            ][0]["response"]
            == "Second"
        )
        assert client.patch(path, json={"archived": True}).json()["archived"] is True
        assert client.get(base + "/threads").json()["items"] == []
        assert (
            client.get(base + "/threads", params={"archived": True}).json()["items"][0]["thread_id"]
            == thread_id
        )
        assert (
            client.post(base + "/chat", json={"thread_id": thread_id, "message": "Hi"}).status_code
            == 409
        )
        assert client.patch(path, json={"archived": False}).json()["archived"] is False
        assert client.delete(path + "/messages").status_code == 204
        assert client.get(path + "/messages").json()["items"] == []
        assert client.post(path + "/resume").json()["code"] == "NO_PENDING_RUN"
        assert (
            client.post(base + "/chat", json={"thread_id": thread_id, "message": "Fresh"}).json()[
                "response"
            ]
            == "Third"
        )
        assert client.delete(path).status_code == 204
        assert client.get(path).status_code == 404
    with TestClient(app) as client:
        assert client.get(path).status_code == 404


def test_fork_copies_history_and_continues_independently(tmp_path):
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite")
    provider = SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["One", "Two"]))
    app = create_app(checkpoint_settings=checkpoint, model_provider=provider)
    with TestClient(app) as client:
        chat = "/api/v1/chat"
        threads = "/api/v1/threads"
        source = client.post(chat, json={"message": "Original question"}).json()["thread_id"]
        response = client.post(f"{threads}/{source}/fork", json={"title": "Branch"})
        assert response.status_code == 201, response.text
        branch = response.json()["thread_id"]
        config = build_graph_config(
            {"user_id": "local-user", "thread_id": branch}, app.state.agent_settings
        )
        snapshot = client.portal.call(app.state.runner.graph.aget_state, config)
        assert [message.content for message in snapshot.values["messages"]] == [
            "Original question",
            "One",
        ]
        continued = client.post(chat, json={"thread_id": branch, "message": "Follow-up"})
        assert continued.status_code == 200, continued.text
        assert len(client.get(f"{threads}/{source}/messages").json()["items"]) == 1
        assert [
            row["message"] for row in client.get(f"{threads}/{branch}/messages").json()["items"]
        ] == ["Original question", "Follow-up"]


def test_personalization_and_profile_version_conflicts(tmp_path):
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite")
    provider = SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["Ready"]))
    app = create_app(checkpoint_settings=checkpoint, model_provider=provider)
    with TestClient(app) as client:
        base = "/api/v1"
        assert client.get(base + "/settings").json() == {"personalization_enabled": True}
        memory = client.post(
            base + "/memories",
            json={"title": "Atlas", "content": "Use a short written brief", "type": "preference"},
        )
        assert memory.status_code == 201
        memory_id = memory.json()["id"]
        other = client.post(
            base + "/memories", json={"title": "Meeting", "content": "Schedule a review"}
        ).json()["id"]
        assert (
            client.put(
                base + "/graph/links",
                json={
                    "source_id": memory_id,
                    "target_id": other,
                    "relation": "related",
                    "relevance": 0.8,
                },
            ).status_code
            == 200
        )
        assert client.get(base + "/graph").json()["edges"][0]["relevance"] == 0.8
        assert (
            client.post(
                base + f"/memories/{other}/feedback",
                json={
                    "feedback_type": "confirm",
                    "feedback_content": "This is correct.",
                },
            ).status_code
            == 201
        )
        assert client.delete(base + f"/memories/{other}").status_code == 204
        assert client.get(base + "/graph").json()["edges"] == []
        assert client.get(base + "/profile").json()["attributes"][0]["memory_id"] == memory_id
        profile = {"expected_version": 1, "attributes": attributes({"format": "brief"})}
        assert client.patch(base + "/profile", json=profile).json()["version"] == 2
        assert client.patch(base + "/profile", json=profile).status_code == 409
        assert client.get(base + "/profile/versions/2").json()["attributes"][0]["value"] == "brief"
        result = client.post(base + "/chat", json={"message": "Atlas"}).json()
        assert result["used_memory_ids"] == [
            client.get(base + "/profile").json()["attributes"][0]["memory_id"]
        ]
        assert result["profile_version"] == 2
        client.patch(base + "/settings", json={"personalization_enabled": False})
        result = client.post(base + "/chat", json={"message": "Atlas"}).json()
        assert result["used_memory_ids"] == []
        assert result["profile_version"] is None
        assert (
            client.post(
                base + "/threads", json={}, headers={"Origin": "https://example.invalid"}
            ).status_code
            == 403
        )
    with TestClient(app) as client:
        assert client.get(base + "/settings").json() == {"personalization_enabled": False}
        assert (
            client.get(base + "/memories").json()["items"][0]["id"]
            == client.get(base + "/profile").json()["attributes"][0]["memory_id"]
        )
