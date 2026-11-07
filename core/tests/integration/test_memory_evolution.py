from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


def flush_learning(client, app):
    async def drain():
        for job in await app.state.conversations.learning_jobs():
            await app.state.memory_evolution.process(job)

    client.portal.call(drain)


def test_explicit_memory_updates_profile_and_is_used_and_removed(tmp_path):
    provider = SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["好的"]))
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=provider,
    )
    with TestClient(app) as client:
        path = "/api/v1"
        saved = client.post(path + "/chat", json={"message": "记住，写方案先给结论"}).json()
        flush_learning(client, app)
        thread = path + "/threads/" + saved["thread_id"]
        receipt = client.get(thread + "/memory-receipts", params={"run_id": saved["run_id"]}).json()
        assert not receipt["pending"]
        assert len(receipt["items"]) == 1
        memory_id = receipt["items"][0]["id"]
        memory = client.get(path + "/memories/" + memory_id).json()
        assert memory["evidence"][0]["quote"] == "记住，写方案先给结论"
        assert client.get(path + "/profile").json()["attributes"][0]["memory_id"] == memory_id
        answer = client.post(path + "/chat", json={"message": "给我写一份实施方案"}).json()
        assert answer["used_memory_ids"] == [memory_id]
        conflict = client.post(
            path + "/chat", json={"message": "记住，写方案先解释再给结论"}
        ).json()
        flush_learning(client, app)
        pending = client.get(
            path + "/threads/" + conflict["thread_id"] + "/memory-receipts",
            params={"run_id": conflict["run_id"]},
        ).json()
        assert pending["items"] == [] and len(pending["suggestion_ids"]) == 1
        assert client.delete(thread).status_code == 204
        assert client.get(path + "/memories/" + memory_id).status_code == 404
        assert client.get(path + "/profile").json()["version"] == 2
        assert not client.get(path + "/profile").json().get("attributes")
        assert not client.get(path + "/profile/versions/1").json().get("attributes")


def test_explicit_personal_preferences_enter_review_in_the_same_conversation(tmp_path):
    provider = SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["好的"]))
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=provider,
    )
    with TestClient(app) as client:
        path = "/api/v1"
        first = client.post(path + "/chat", json={"message": "我喜欢简洁的回答"}).json()
        client.post(
            path + "/chat", json={"message": "我喜欢简洁的回答", "thread_id": first["thread_id"]}
        )
        client.post(path + "/chat", json={"message": "这次只给命令"})
        flush_learning(client, app)
        initial = client.get(path + "/memory-suggestions").json()["items"]
        assert len(initial) == 1
        assert len(initial[0]["evidence"]) == 2
        assert all(entry["source"] == "explicit" for entry in initial[0]["evidence"])
        client.post(path + "/chat", json={"message": "我喜欢简洁的回答"})
        client.post(
            path + "/chat", json={"message": "以后在这个项目里先给结论", "project_id": "p1"}
        )
        flush_learning(client, app)
        suggestions = client.get(path + "/memory-suggestions").json()["items"]
        assert len(suggestions) == 2
        project = next(item for item in suggestions if item["scope"] == "project")
        global_item = next(item for item in suggestions if item["scope"] == "global")
        assert len({e["thread_id"] for e in global_item["evidence"]}) == 2
        assert client.get(path + "/profile").json()["version"] == 0
        review = path + "/memory-suggestions/" + project["id"] + "/review"
        accepted = client.post(review, json={"action": "confirm", "value": "写方案先给结论"}).json()
        assert client.post(review, json={"action": "confirm"}).json() == accepted
        for project_id, expected in [("p1", [accepted["memory_id"]]), ("p2", [])]:
            answer = client.post(
                path + "/chat", json={"message": "写方案", "project_id": project_id}
            ).json()
            assert answer["used_memory_ids"] == expected
        assert client.patch(path + "/settings", json={"personalization_enabled": False}).is_success
        disabled = client.post(
            path + "/chat",
            json={
                "message": "记住，写方案要详细",
                "project_id": "p1",
                "personalization_enabled": True,
            },
        ).json()
        flush_learning(client, app)
        assert disabled["used_memory_ids"] == []
        assert client.get(path + "/profile").json()["version"] == 1


def test_confirmed_episode_reaches_model_only_in_its_workspace(tmp_path):
    model_inputs = []

    def reply(messages):
        model_inputs.append(messages)
        return AIMessage(content="收到")

    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "episodes.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(reply)),
    )
    with TestClient(app) as client:
        base = "/api/v1"
        for workspace in ("alpha", "beta"):
            assert (
                client.post(
                    base + "/workspaces", json={"id": workspace, "name": workspace}
                ).status_code
                == 201
            )

        def thread_in(workspace):
            return client.post(base + "/threads", json={"workspace_id": workspace}).json()[
                "thread_id"
            ]

        source = thread_in("alpha")
        report = "昨天我们完成了附件索引迁移，数据库换成了 SQLite。"
        turn = client.post(base + "/chat", json={"thread_id": source, "message": report}).json()
        flush_learning(client, app)
        receipt = client.get(
            f"{base}/threads/{source}/memory-receipts", params={"run_id": turn["run_id"]}
        ).json()
        assert not receipt["pending"] and receipt["items"] == []
        (suggestion_id,) = receipt["suggestion_ids"]
        (suggestion,) = client.get(base + "/memory-suggestions").json()["items"]
        assert suggestion["id"] == suggestion_id
        assert suggestion["dimension"] == "episodic"
        assert (suggestion["scope"], suggestion["scope_ref"]) == ("project", "alpha")
        (evidence,) = suggestion["evidence"]
        assert evidence["quote"] == report and evidence["recorded_at"]

        sibling = thread_in("alpha")
        query = "你还记得上次的附件索引迁移吗？"
        assert (
            client.post(
                base + "/chat",
                json={
                    "thread_id": sibling,
                    "message": query,
                },
            ).json()["used_memory_ids"]
            == []
        )
        confirmed = client.post(
            f"{base}/memory-suggestions/{suggestion_id}/review", json={"action": "confirm"}
        ).json()
        memory_id = confirmed["memory_id"]
        memory = client.get(f"{base}/memories/{memory_id}").json()
        assert memory["type"] == "episodic" and memory["evidence"] == [evidence]
        assert client.get(base + "/profile").json()["version"] == 0
        assert client.get(base + "/graph?project_id=alpha").json()["nodes"][0]["type"] == "episodic"

        assert client.post(
            base + "/chat",
            json={
                "thread_id": sibling,
                "message": query,
            },
        ).json()["used_memory_ids"] == [memory_id]
        reference = next(
            message for message in model_inputs[-1] if message.name == "personalization_reference"
        )
        assert "附件索引迁移" in reference.text
        assert f"Episode reported on {evidence['recorded_at'][:10]}" in reference.text
        other = thread_in("beta")
        assert (
            client.post(
                base + "/chat",
                json={
                    "thread_id": other,
                    "message": query,
                },
            ).json()["used_memory_ids"]
            == []
        )
        assert (
            client.post(
                base + "/chat",
                json={
                    "thread_id": other,
                    "message": query,
                    "project_id": "alpha",
                },
            ).status_code
            == 409
        )
        assert (
            client.post(
                base + "/chat",
                json={
                    "thread_id": sibling,
                    "message": "你还记得上次的字体调整吗？",
                },
            ).json()["used_memory_ids"]
            == []
        )
        assert client.delete(f"{base}/threads/{source}").status_code == 204
        assert client.get(f"{base}/memories/{memory_id}").status_code == 404


def test_explicit_episode_deduplicates_and_unfinished_work_is_not_learned(tmp_path):
    provider = SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["OK"]))
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=provider,
    )
    with TestClient(app) as client:
        base = "/api/v1"
        for text in (
            "我计划明天完成 Orion 部署",
            "如果我修复了 Orion，你会怎么做？",
            "我修复了 Orion 吗",
        ):
            assert client.post(base + "/chat", json={"message": text}).is_success
        report = "Remember that we deployed Orion to the staging cluster."
        turns = [client.post(base + "/chat", json={"message": report}).json() for _ in range(2)]
        flush_learning(client, app)
        assert client.get(base + "/memory-suggestions").json()["items"] == []
        (memory,) = client.get(base + "/memories?type=episodic").json()["items"]
        detail = client.get(f"{base}/memories/{memory['id']}").json()
        assert len(detail["evidence"]) == 2
        receipt = client.get(
            f"{base}/threads/{turns[0]['thread_id']}/memory-receipts",
            params={"run_id": turns[0]["run_id"]},
        ).json()
        assert receipt["items"][0]["type"] == "episodic"
        assert client.post(
            base + "/chat",
            json={
                "message": "Where did we deploy Orion?",
            },
        ).json()["used_memory_ids"] == [memory["id"]]
        assert client.delete(f"{base}/threads/{turns[0]['thread_id']}").status_code == 204
        assert len(client.get(f"{base}/memories/{memory['id']}").json()["evidence"]) == 1
