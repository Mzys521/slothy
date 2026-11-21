"""Persistent planner configuration without implying an implemented planner runtime."""

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel
from langchain_core.messages import AIMessage

from app.config import CheckpointSettings, MemorySettings
from app.conversations import ConversationTurn
from app.infrastructure.conversations import SQLiteConversationStore
from app.memory.learning import MemoryLearner
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.personalization.settings import MemoryConfiguration
from tests.support.legacy_app import create_app

PATH = "/api/v1/settings/memory"


def application(tmp_path):
    return create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(
            get_chat_model=lambda: FakeListChatModel(responses=["Ready"])
        ),
    )


def memory(index, *, dimension="fact", protected=True, importance=0.5):
    """Build one manual memory that is or is not protected. / 构造一条受保护或非保护的手动记忆。"""

    return {
        "title": f"事实 {index}",
        "content": f"事实内容 {index}",
        "type": "preference" if dimension == "preference" else "semantic",
        "scope": "global",
        "importance": importance,
        "profile_dimension": dimension,
        "protected": protected,
    }


def create_memories(
    client, count, *, dimension="fact", protected=True, importance=0.5, start=0
):
    """Create unique memories through the API and return their record ids.

    Each call uses a distinct title so unrelated facts never share one slot.
    / 通过接口创建互不相同的记忆并返回 ID；标题各不相同，避免无关事实共用同一槽位。
    """

    ids = []
    for offset in range(count):
        index = start + offset
        response = client.post(
            "/api/v1/memories",
            json=memory(index, dimension=dimension, protected=protected, importance=importance),
        )
        assert response.status_code == 201, response.text
        ids.append(response.json()["id"])
        importance = min(1.0, importance + 0.1)
    return ids


def test_configuration_roundtrip_conflict_restart_and_legacy_gate(tmp_path):
    app = application(tmp_path)
    configuration = {
        "mode": "shadow",
        "model": " provider/model ",
        "batch_size": 12,
        "idle_seconds": 75,
        "consolidate_every": 25,
        "forget_interval_hours": 12,
        "active_limit": 800,
        "auto_confirm_score": 0.9,
        "observed_min_threads": 3,
        "episodic_ttl_days": 45,
        "fact_ttl_days": 200,
        "retention_days": 120,
        "daily_token_budget": 0,
        "strength_c": 4.0,
    }
    with TestClient(app) as client:
        initial = client.get(PATH).json()
        assert initial["revision"] == 0
        assert initial["configuration"] == initial["defaults"]
        assert len(initial["configuration"]) == 14
        assert {
            key: initial["runtime"][key]
            for key in ("engine", "planner_available", "personalization_enabled")
        } == {
            "engine": "rule_review",
            "planner_available": False,
            "personalization_enabled": True,
        }
        saved = client.patch(PATH, json={"expected_revision": 0, "configuration": configuration})
        assert saved.status_code == 200
        configuration["model"] = "provider/model"
        assert saved.json()["configuration"] == configuration
        assert saved.json()["revision"] == 1
        conflict = client.patch(
            PATH, json={"expected_revision": 0, "configuration": initial["defaults"]}
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "VERSION_CONFLICT"
        assert conflict.json()["request_id"]
        assert client.get(PATH).json()["configuration"] == configuration
        # Preconfiguration must not silently change existing personalization or data.
        assert client.get("/api/v1/settings").json() == {"personalization_enabled": True}
        assert client.get("/api/v1/profile").json()["version"] == 0
        assert client.get("/api/v1/memories").json()["items"] == []
        client.patch("/api/v1/settings", json={"personalization_enabled": False})
        assert not client.get(PATH).json()["runtime"]["personalization_enabled"]
    with TestClient(application(tmp_path)) as client:
        current = client.get(PATH).json()
        assert current["configuration"] == configuration
        assert current["revision"] == 1
        assert not current["runtime"]["personalization_enabled"]
        reset = client.patch(
            PATH, json={"expected_revision": 1, "configuration": current["defaults"]}
        )
        assert reset.status_code == 200
        assert reset.json()["revision"] == 2
        assert reset.json()["configuration"] == initial["defaults"]


@pytest.mark.parametrize(
    "change",
    [
        {"batch_size": 0},
        {"batch_size": True},
        {"idle_seconds": 1.5},
        {"auto_confirm_score": 1.1},
        {"retention_days": -1},
        {"daily_token_budget": 10000001},
        {"mode": "unrecognized"},
        {"unexpected": "value"},
    ],
)
def test_invalid_configuration_is_not_written(tmp_path, change):
    with TestClient(application(tmp_path)) as client:
        initial = client.get(PATH).json()
        response = client.patch(
            PATH,
            json={
                "expected_revision": 0,
                "configuration": {**initial["configuration"], **change},
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert client.get(PATH).json()["revision"] == 0


def test_environment_defaults_and_user_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_BATCH_SIZE", "13")
    monkeypatch.setenv("MEMORY_RETENTION_DAYS", "180")
    monkeypatch.setenv("MEMORY_MODEL", " configured/model ")
    assert MemorySettings(_env_file=None).batch_size == 13
    with TestClient(application(tmp_path)) as client:
        initial = client.get(PATH).json()
        assert initial["defaults"]["batch_size"] == 13
        assert initial["defaults"]["retention_days"] == 180
        assert initial["defaults"]["model"] == "configured/model"
        configuration = {**initial["configuration"], "batch_size": 9, "model": ""}
        assert (
            client.patch(
                PATH, json={"expected_revision": 0, "configuration": configuration}
            ).status_code
            == 200
        )
    monkeypatch.setenv("MEMORY_BATCH_SIZE", "15")
    with TestClient(application(tmp_path)) as client:
        current = client.get(PATH).json()
        assert current["configuration"]["batch_size"] == 9
        assert current["configuration"]["model"] is None
        assert current["defaults"]["batch_size"] == 15
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        assert schema["MemoryConfiguration"]["additionalProperties"] is False
        assert set(schema["MemoryConfiguration"]["properties"]) == set(
            MemoryConfiguration.model_fields
        )


def test_protected_capacity_conflict_keeps_revision_and_reports_count(tmp_path):
    """Protected facts above the target limit reject the save without advancing state.

    / 受保护事实多于目标上限时拒绝保存，且不推进任何状态。
    """

    with TestClient(application(tmp_path)) as client:
        create_memories(client, 3, dimension="preference", protected=True)
        initial = client.get(PATH).json()
        assert initial["revision"] == 0
        assert initial["applied_revision"] == 0
        assert initial["pending_application"] is False
        assert initial["capacity"] == {
            "limit": 500,
            "active": 3,
            "protected": 3,
            "required": 0,
            "archivable": 0,
            "reachable": True,
        }
        rejected = client.patch(
            PATH,
            json={
                "expected_revision": 0,
                "configuration": {**initial["configuration"], "active_limit": 2},
            },
        )
        assert rejected.status_code == 409
        assert rejected.json()["code"] == "MEMORY_CAPACITY_PROTECTED"
        assert rejected.json()["protected_count"] == 3
        assert rejected.json()["active_count"] == 3
        assert rejected.json()["archivable_count"] == 0
        assert rejected.json()["new_limit"] == 2
        current = client.get(PATH).json()
        assert (current["revision"], current["applied_revision"]) == (0, 0)
        assert current["pending_application"] is False
        assert current["configuration"]["active_limit"] == 500
        assert current["capacity"]["protected"] == 3
        assert len(client.get("/api/v1/memories").json()["items"]) == 3


def test_lowering_to_feasible_limit_archives_candidates_and_marks_applied(tmp_path):
    """A reachable limit archives the lowest-value candidates and advances applied state.

    / 可达上限归档价值最低的候选，并推进已生效状态。
    """

    with TestClient(application(tmp_path)) as client:
        protected = create_memories(client, 2, dimension="preference", protected=True)
        goals = create_memories(
            client, 3, dimension="goal", protected=False, importance=0.1, start=10
        )
        initial = client.get(PATH).json()
        version_before = client.get("/api/v1/profile").json()["version"]
        assert initial["capacity"]["protected"] == 2
        assert initial["capacity"]["archivable"] == 3
        saved = client.patch(
            PATH,
            json={
                "expected_revision": 0,
                "configuration": {**initial["configuration"], "active_limit": 4},
            },
        )
        assert saved.status_code == 200, saved.text
        body = saved.json()
        assert (body["revision"], body["applied_revision"]) == (1, 1)
        assert body["pending_application"] is False
        assert body["capacity"] == {
            "limit": 4,
            "active": 4,
            "protected": 2,
            "required": 0,
            "archivable": 2,
            "reachable": True,
        }
        active = client.get("/api/v1/memories").json()["items"]
        archived = client.get("/api/v1/memories?status=archived").json()["items"]
        assert len(active) == 4
        assert [item["id"] for item in archived] == goals[:1]
        assert set(protected) <= {item["id"] for item in active}
        profile = client.get("/api/v1/profile").json()
        assert profile["version"] == version_before + 1
        assert {attribute["memory_id"] for attribute in profile["attributes"]} == {
            item["id"] for item in active
        }


def test_saved_revision_with_failed_apply_converges_once_after_restart(tmp_path, monkeypatch):
    """A rebuild failure after save leaves a pending revision that restart applies once.

    / 保存后重建失败会留下待应用修订；重启后恰好应用一次。
    """

    original_rebuild = MemoryService.rebuild
    failure = {"enabled": False}

    async def failing_rebuild(self, user, summary="Updated from confirmed facts"):
        if failure["enabled"]:
            raise RuntimeError("simulated profile rebuild failure")
        return await original_rebuild(self, user, summary)

    monkeypatch.setattr(MemoryService, "rebuild", failing_rebuild)
    with TestClient(application(tmp_path)) as client:
        create_memories(client, 2, dimension="preference", protected=True)
        goals = create_memories(
            client, 3, dimension="goal", protected=False, importance=0.1, start=10
        )
        initial = client.get(PATH).json()
        version_before = client.get("/api/v1/profile").json()["version"]
        failure["enabled"] = True
        saved = client.patch(
            PATH,
            json={
                "expected_revision": 0,
                "configuration": {**initial["configuration"], "active_limit": 4},
            },
        )
        assert saved.status_code == 200, saved.text
        pending = saved.json()
        assert (pending["revision"], pending["applied_revision"]) == (1, 0)
        assert pending["pending_application"] is True
        # The workspace transaction rolled back: facts and profile remain untouched.
        # 工作区事务已回滚：事实与画像都保持不变。
        assert len(client.get("/api/v1/memories").json()["items"]) == 5
        assert client.get("/api/v1/memories?status=archived").json()["items"] == []
        assert client.get("/api/v1/profile").json()["version"] == version_before
    # The next process has a working projection and applies the pending revision exactly once.
    # 下一个进程的投影恢复正常，并恰好应用一次待处理修订。
    failure["enabled"] = False
    with TestClient(application(tmp_path)) as client:
        applied = client.get(PATH).json()
        assert (applied["revision"], applied["applied_revision"]) == (1, 1)
        assert applied["pending_application"] is False
        active = client.get("/api/v1/memories").json()["items"]
        archived = client.get("/api/v1/memories?status=archived").json()["items"]
        assert len(active) == 4
        assert [item["id"] for item in archived] == goals[:1]
        profile = client.get("/api/v1/profile").json()
        assert {attribute["memory_id"] for attribute in profile["attributes"]} == {
            item["id"] for item in active
        }
        first_version = profile["version"]
    # A second restart must not re-apply or rebuild again. / 再次重启不得重复应用或重建。
    with TestClient(application(tmp_path)) as client:
        assert client.get(PATH).json()["applied_revision"] == 1
        assert client.get("/api/v1/profile").json()["version"] == first_version


def test_concurrent_settings_requests_accept_only_the_current_revision(tmp_path):
    """Two racing saves accept exactly one write and reject the stale expectation.

    / 两个并发保存只接受一个写入，另一个因修订过期被拒绝。
    """

    with TestClient(application(tmp_path)) as client:
        initial = client.get(PATH).json()
        barrier = threading.Barrier(2)

        def save(limit):
            barrier.wait(timeout=15)
            return client.patch(
                PATH,
                json={
                    "expected_revision": 0,
                    "configuration": {**initial["configuration"], "active_limit": limit},
                },
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = (pool.submit(save, 300), pool.submit(save, 400))
            responses = [future.result(timeout=30) for future in futures]
        assert sorted(response.status_code for response in responses) == [200, 409]
        conflict = next(response for response in responses if response.status_code == 409)
        assert conflict.json()["code"] == "VERSION_CONFLICT"
        accepted = next(response for response in responses if response.status_code == 200).json()
        assert accepted["revision"] == 1
        current = client.get(PATH).json()
        assert current["revision"] == 1
        assert current["configuration"]["active_limit"] == accepted["configuration"]["active_limit"]


async def test_failed_profile_rebuild_persists_and_worker_retry_converges(tmp_path, monkeypatch):
    """A projection failure is persisted with the facts and repaired without another batch.

    / 投影失败与事实一同持久化，且无需下一批消息即可由工作器重试修复。
    """

    class PlannerModel:
        async def ainvoke(self, messages, **_kwargs):
            payload = json.loads(messages[-1].text)
            turn = payload["turns"][0]
            return AIMessage(
                content=json.dumps(
                    {
                        "candidates": [
                            {
                                "action": "add",
                                "kind": "preference",
                                "key": "answer_structure",
                                "title": "回答结构",
                                "content": "先给结论，再补充说明",
                                "scope": "global",
                                "basis": "explicit",
                                "durable": True,
                                "confidence": 0.96,
                                "evidence": [{"turn": turn["turn"], "quote": turn["text"]}],
                            }
                        ]
                    },
                    ensure_ascii=False,
                )
            )

    settings = CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite")
    async with (
        SQLiteConversationStore.open(settings) as conversations,
        MemoryStore.open(tmp_path / "workspace.sqlite", 5) as store,
    ):
        service = MemoryService(store, conversations)

        async def configuration(_user):
            return 0, MemoryConfiguration()

        learner = MemoryLearner(
            service,
            configuration,
            lambda _name: ("test-model", PlannerModel()),
        )
        service.planner = learner
        await conversations.save_turn(
            ConversationTurn(
                user_id="u",
                thread_id="t",
                run_id="r",
                user_input="结论先行对我最重要。",
                response="明白。",
                learn_memory=True,
            )
        )
        original_rebuild = MemoryService.rebuild

        async def failing_rebuild(self, user, summary="Updated from confirmed facts"):
            raise RuntimeError("simulated profile rebuild failure")

        monkeypatch.setattr(MemoryService, "rebuild", failing_rebuild)
        await learner.process_jobs(await conversations.learning_jobs())
        # Facts commit, the learning queue settles, and the projection marker survives.
        # 事实提交、学习队列照常结算，且投影标记持久化。
        assert len(await store.list_memories("u", status="active")) == 1
        assert (await conversations.learning_status("u"))["pending"] == 0
        assert await service.pending_rebuilds() == ["u"]
        assert await store.get_profile("u") is None
        monkeypatch.setattr(MemoryService, "rebuild", original_rebuild)
        assert await learner.retry_pending_projections() is False
        assert await service.pending_rebuilds() == []
        profile = await store.get_profile("u")
        assert profile["version"] == 1
        assert profile["attributes"][0]["value"] == "先给结论，再补充说明"
