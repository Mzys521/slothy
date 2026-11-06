"""Exercise the audited feature chains with real stores and offline model boundaries."""

import json
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings
from tests.support.legacy_app import create_app

BASE = "/api/v1"


def drain(client, app):
    async def flush():
        for job in await app.state.conversations.learning_jobs():
            await app.state.memory_evolution.process(job)

    client.portal.call(flush)


def application(tmp_path, inputs=None):
    def reply(messages):
        if inputs is not None:
            inputs.append(messages)
        return AIMessage(content="收到")

    return create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(reply)),
    )


def test_multiline_learning_profile_injection_trace_and_reanalysis(tmp_path):
    inputs = []
    app = application(tmp_path, inputs)
    with TestClient(app) as client:
        result = client.post(
            BASE + "/chat",
            json={
                "message": "我喜欢简洁的回答。\n我习惯每天早上跑步。\n我的长期目标是成为架构师。\n"
                "```text\n我喜欢泄露密码\n```\n> 我喜欢引用中的错误偏好"
            },
        ).json()
        assert result["response"] == "收到"
        drain(client, app)
        suggestions = client.get(BASE + "/memory-suggestions").json()["items"]
        assert {item["dimension"] for item in suggestions} == {"preference", "habit", "goal"}
        assert client.get(BASE + "/profile").json()["version"] == 0
        for item in suggestions:
            assert (
                item["evidence"][0]["quote"]
                in "我喜欢简洁的回答。\n我习惯每天早上跑步。\n我的长期目标是成为架构师。"
            )
            assert (
                client.post(
                    f"{BASE}/memory-suggestions/{item['id']}/review", json={"action": "confirm"}
                ).status_code
                == 200
            )
        profile = client.get(BASE + "/profile").json()
        assert len(profile["attributes"]) == 3
        answer = client.post(BASE + "/chat", json={"message": "给我一个不相关的例子"}).json()
        reference = "\n".join(
            message.text
            for message in inputs[-1]
            if message.name in {"profile_snapshot", "personalization_reference"}
        )
        assert "简洁" in reference and "早上跑步" in reference and "架构师" in reference
        assert len(answer["used_memory_ids"]) == 3
        history = client.get(f"{BASE}/threads/{answer['thread_id']}/messages").json()["items"][0]
        assert history["used_memory_ids"] == answer["used_memory_ids"]
        assert history["profile_version"] == profile["version"]
        graph = client.get(BASE + "/graph").json()
        assert graph["edges"] and all(edge["origin"] == "conversation" for edge in graph["edges"])
        edge = graph["edges"][0]
        assert (
            client.delete(
                BASE + "/graph/links",
                params={"source_id": edge["source"], "target_id": edge["target"]},
            ).status_code
            == 204
        )
        assert client.post(BASE + "/memory-learning/reprocess", json={}).json()["queued"] >= 1
        drain(client, app)
        assert client.get(BASE + "/profile").json()["version"] == profile["version"]
        assert len(client.get(BASE + "/memories").json()["items"]) == 3
        assert not any(
            e["source"] == edge["source"] and e["target"] == edge["target"]
            for e in client.get(BASE + "/graph").json()["edges"]
        )
        status = client.get(BASE + "/memory-learning").json()
        assert status["pending"] == status["failed"] == status["suggestions"] == 0
        assert status["processed"] >= 2
        client.patch(BASE + "/settings", json={"personalization_enabled": False})
        assert client.post(BASE + "/memory-learning/reprocess", json={}).status_code == 409


def test_manual_profile_memory_scopes_conflicts_archive_restore_and_erasure(tmp_path):
    inputs = []
    app = application(tmp_path, inputs)
    with TestClient(app) as client:
        ids = []
        source_thread = client.post(BASE + "/threads", json={}).json()["thread_id"]
        for title, content, scope, ref in [
            ("饮品", "我喜欢喝咖啡", "global", None),
            ("项目习惯", "必须先写项目测试", "project", "alpha"),
            ("会话习惯", "只在来源会话使用的标记", "thread", source_thread),
        ]:
            item = client.post(
                BASE + "/memories",
                json={
                    "title": title,
                    "content": content,
                    "type": "preference",
                    "scope": scope,
                    "scope_ref": ref,
                },
            ).json()
            ids.append(item["id"])
        current = client.get(BASE + "/profile").json()
        assert len(current["attributes"]) == 3
        answer = client.post(
            BASE + "/chat", json={"message": "不相关问题", "project_id": "beta"}
        ).json()
        assert answer["used_memory_ids"] == [ids[0]]
        ref = "\n".join(
            message.text
            for message in inputs[-1]
            if message.name in {"profile_snapshot", "personalization_reference"}
        )
        assert "项目测试" not in ref and "来源会话" not in ref
        client.post(BASE + "/chat", json={"message": "不相关问题", "project_id": "alpha"})
        ref = "\n".join(
            message.text
            for message in inputs[-1]
            if message.name in {"profile_snapshot", "personalization_reference"}
        )
        assert "项目测试" in ref and "来源会话" not in ref
        newer = client.post(
            BASE + "/memories",
            json={"title": "饮品", "content": "我不喜欢喝咖啡", "type": "preference"},
        ).json()
        assert client.get(BASE + "/memories/" + ids[0]).json()["status"] == "archived"
        assert any(
            item["id"] == ids[0]
            for item in client.get(BASE + "/memories?status=archived").json()["items"]
        )
        assert (
            client.patch(BASE + "/memories/" + ids[0], json={"status": "active"}).status_code == 200
        )
        assert client.get(BASE + "/memories/" + newer["id"]).json()["status"] == "archived"
        assert client.delete(BASE + "/memories/" + ids[0]).status_code == 204
        deleted_profile = client.get(BASE + "/profile").json()
        assert deleted_profile["removed_attributes"] == 1
        assert ids[0] not in json.dumps(client.get(BASE + "/profile/versions/1").json())
        assert client.get(BASE + "/profile/versions/1").json()["redacted_attributes"] == 1
        # Deleting a conversation does not erase unrelated manually entered attributes.
        assert client.delete(f"{BASE}/threads/{answer['thread_id']}").status_code == 204
        assert len(client.get(BASE + "/profile").json()["attributes"]) == 2


def test_graph_focus_direction_archived_rejection_and_total(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        nodes = [
            client.post(
                BASE + "/memories",
                json={"title": f"Memory {i}", "content": "a real saved fact", "importance": 0.8},
            ).json()["id"]
            for i in range(100)
        ]
        low = client.post(
            BASE + "/memories",
            json={"title": "Low priority", "content": "focus target", "importance": 0.01},
        ).json()["id"]
        graph = client.get(BASE + "/graph").json()
        assert graph["total"] == 101 and graph["truncated"] and len(graph["nodes"]) == 100
        assert low not in {node["id"] for node in graph["nodes"]}
        assert low in {
            node["id"]
            for node in client.get(BASE + "/graph", params={"focus_id": low}).json()["nodes"]
        }
        first, second = sorted(nodes[:2], reverse=True)
        link = client.put(
            BASE + "/graph/links",
            json={
                "source_id": first,
                "target_id": second,
                "relation": "depends on",
                "directed": True,
            },
        ).json()
        assert (link["source_id"], link["target_id"], link["directed"]) == (first, second, True)
        assert (
            client.delete(
                BASE + "/graph/links",
                params={"source_id": first, "target_id": second, "directed": True},
            ).status_code
            == 204
        )
        client.patch(BASE + "/memories/" + low, json={"status": "archived"})
        assert (
            client.get(BASE + "/graph", params={"focus_id": low}).json()["code"]
            == "GRAPH_FOCUS_UNAVAILABLE"
        )
        assert (
            client.put(
                BASE + "/graph/links",
                json={"source_id": first, "target_id": low, "relation": "hidden"},
            ).status_code
            == 404
        )


def test_profile_history_pagination_retention_conflict_and_capacity(tmp_path):
    app = application(tmp_path)
    with TestClient(app) as client:
        for version in range(205):
            result = client.patch(
                BASE + "/profile",
                json={"expected_version": version, "preferences": {"answer_detail": str(version)}},
            )
            assert result.status_code == 200
        first = client.get(BASE + "/profile/versions").json()
        assert (
            first["retention_limit"] == 200 and len(first["items"]) == 50 and first["next_cursor"]
        )
        second = client.get(
            BASE + "/profile/versions", params={"before": first["next_cursor"]}
        ).json()
        assert max(item["version"] for item in second["items"]) < min(
            item["version"] for item in first["items"]
        )
        assert len(client.get(BASE + "/profile/versions?limit=200").json()["items"]) == 200
        assert client.get(BASE + "/profile/versions/1").status_code == 404
        assert (
            client.patch(BASE + "/profile", json={"expected_version": 0, "preferences": {}}).json()[
                "code"
            ]
            == "VERSION_CONFLICT"
        )

        async def seed_capacity():
            await app.state.workspace.connection.execute(
                "INSERT INTO workspace_profiles (user_id,version,profile_json,change_summary) "
                "VALUES (?,?,?,?)",
                ("local-user", 2**63 - 2, "{}", "capacity boundary"),
            )

        client.portal.call(seed_capacity)
        assert (
            client.patch(
                BASE + "/profile", json={"expected_version": 2**63 - 2, "preferences": {}}
            ).json()["code"]
            == "PROFILE_VERSION_LIMIT"
        )
        assert client.get(BASE + "/profile").json()["version"] == 2**63 - 2


def test_review_can_replace_an_existing_generic_attribute_without_erasing_other_preferences(
    tmp_path,
):
    app = application(tmp_path)
    with TestClient(app) as client:
        old = client.post(
            BASE + "/memories",
            json={
                "title": "环境偏好",
                "content": "喜欢安静的环境",
                "type": "preference",
            },
        ).json()
        other = client.post(
            BASE + "/memories",
            json={
                "title": "饮品偏好",
                "content": "喜欢喝咖啡",
                "type": "preference",
            },
        ).json()
        profile = client.get(BASE + "/profile").json()
        key = next(
            attribute["key"]
            for attribute in profile["attributes"]
            if attribute["memory_id"] == old["id"]
        )
        client.post(BASE + "/chat", json={"message": "以后我喜欢热闹的环境"})
        drain(client, app)
        (suggestion,) = client.get(BASE + "/memory-suggestions").json()["items"]
        accepted = client.post(
            f"{BASE}/memory-suggestions/{suggestion['id']}/review",
            json={
                "action": "confirm",
                "profile_key": key,
            },
        )
        assert accepted.status_code == 200
        assert client.get(BASE + "/memories/" + old["id"]).json()["status"] == "archived"
        profile = client.get(BASE + "/profile").json()
        assert len(profile["attributes"]) == 2
        assert any(attribute["memory_id"] == other["id"] for attribute in profile["attributes"])
        assert (
            next(
                attribute["value"] for attribute in profile["attributes"] if attribute["key"] == key
            )
            == "我喜欢热闹的环境"
        )


class WorkspaceModel:
    def __init__(self):
        self.inputs = []

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        self.inputs.append(messages)
        if messages[-1].type == "tool":
            return AIMessage(content=messages[-1].content)
        prompt = messages[-1].text
        command = "mkdir" if prompt == "create" else "cat"
        argument = (
            "approved-folder"
            if prompt == "create"
            else "../beta/marker.txt"
            if prompt == "escape"
            else "marker.txt"
        )
        return AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "call-test",
                    "name": "run_command",
                    "args": {"command": command, "arguments": [argument]},
                }
            ],
        )


def test_workspace_directory_selection_concurrent_tools_and_escape(tmp_path):
    model = WorkspaceModel()
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )
    with TestClient(app) as client:
        sessions = []
        for name in ("alpha", "beta"):
            directory = tmp_path / name
            directory.mkdir()
            (directory / "marker.txt").write_text(name.upper(), encoding="utf-8")
            created = client.post(
                BASE + "/workspaces", json={"id": name, "name": name, "directory": str(directory)}
            )
            assert created.status_code == 201 and created.json()["directory"] == str(
                directory.resolve()
            )
            sessions.append(
                client.post(BASE + "/threads", json={"mode": "work", "workspace_id": name}).json()[
                    "thread_id"
                ]
            )
        child = tmp_path / "alpha" / "nested"
        child.mkdir()
        assert (
            client.post(
                BASE + "/workspaces", json={"name": "overlap", "directory": str(child)}
            ).json()["code"]
            == "WORKSPACE_DIRECTORY_CONFLICT"
        )
        assert (
            client.post(
                BASE + "/workspaces", json={"name": "relative", "directory": "relative/path"}
            ).status_code
            == 422
        )
        with ThreadPoolExecutor(max_workers=2) as pool:
            answers = list(
                pool.map(
                    lambda thread: client.post(
                        BASE + "/chat", json={"thread_id": thread, "message": "read"}
                    ),
                    sessions,
                )
            )
        assert all(answer.status_code == 200 for answer in answers)
        assert (
            "ALPHA" in answers[0].json()["response"] and "BETA" not in answers[0].json()["response"]
        )
        assert (
            "BETA" in answers[1].json()["response"] and "ALPHA" not in answers[1].json()["response"]
        )
        escape_thread = client.post(
            BASE + "/threads", json={"mode": "work", "workspace_id": "alpha"}
        ).json()["thread_id"]
        escape = client.post(BASE + "/chat", json={"thread_id": escape_thread, "message": "escape"})
        assert "BETA" not in escape.text
        assert escape.status_code == 502 or any(
            item["kind"] == "tool" and item["status"] == "error"
            for item in escape.json()["activity"]
        )
        assert client.delete(BASE + "/workspaces/alpha").status_code == 204
        assert (tmp_path / "alpha" / "marker.txt").read_text() == "ALPHA"


def test_approval_resume_rechecks_personalization_gate(tmp_path):
    model = WorkspaceModel()
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )
    with TestClient(app) as client:
        client.post(
            BASE + "/memories",
            json={"title": "回答方式", "content": "我喜欢简短的回答", "type": "preference"},
        )
        thread = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        review = client.post(BASE + "/chat", json={"thread_id": thread, "message": "create"})
        assert review.status_code == 202
        assert any(
            message.name in {"profile_snapshot", "personalization_reference"}
            for message in model.inputs[-1]
        )
        client.patch(BASE + "/settings", json={"personalization_enabled": False})
        approval = review.json()["approval_id"]
        client.post(BASE + "/workspaces", json={"id": "other", "name": "Other"})
        assert (
            client.patch(f"{BASE}/threads/{thread}", json={"workspace_id": "other"}).json()["code"]
            == "RUN_PENDING"
        )
        answer = client.post(
            f"{BASE}/threads/{thread}/approval/{approval}", json={"approved": True}
        )
        assert answer.status_code == 200, answer.text
        assert not any(
            message.name in {"profile_snapshot", "personalization_reference"}
            for message in model.inputs[-1]
        )
        assert answer.json()["used_memory_ids"] == [] and answer.json()["profile_version"] is None
