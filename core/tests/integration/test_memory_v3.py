"""V3 semantic facts, per-session frequency, scope and reliable identity acceptance.
/ V3 语义事实、会话强化、作用域和可靠身份验收。"""

import asyncio
import json
import math
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.api import create_app
from app.config import CheckpointSettings
from app.conversations import ConversationTurn
from app.infrastructure.conversations import SQLiteConversationStore
from app.infrastructure.storage_version import prepare_storage
from app.infrastructure.workspace import BoundedWorkspaceContext
from app.memory.learning import MemoryLearner
from app.memory.semantics import frequency_strength, identity_intent
from app.memory.service import MemoryService
from app.memory.store import MemoryStore
from app.personalization.settings import MemoryConfiguration

BASE = "/api/v1"


@pytest.mark.parametrize(
    "f,expected", [(0, 0), (1, 0.2), (2, 1 / 3), (4, 0.5), (10000, 10000 / 10004)]
)
def test_strength_boundaries_and_independence(f, expected):
    before = frequency_strength(f)
    assert before == pytest.approx(expected)
    assert frequency_strength(1000000) < 1
    assert frequency_strength(f) == before


def test_strength_monotonicity_and_no_clock_dependency():
    assert frequency_strength(1) < frequency_strength(2) < frequency_strength(10)
    assert frequency_strength(10) < frequency_strength(100) < frequency_strength(10000) < 1


@pytest.mark.parametrize(
    "f,c,error",
    [
        (-1, 4, ValueError),
        (True, 4, TypeError),
        (1.5, 4, TypeError),
        (1, 0, ValueError),
        (1, -4, ValueError),
        (1, math.nan, ValueError),
        (1, math.inf, ValueError),
    ],
)
def test_strength_rejects_invalid(f, c, error):
    with pytest.raises(error):
        frequency_strength(f, c)


def skill_plan(payload, *, level=True, target="python", value="beginner", label="新手"):
    turn = payload["turns"][0]
    concept = {"key": target, "kind": "skill", "label": target.title(), "aliases": [target]}
    common = {
        "action": "add",
        "kind": "skill",
        "scope": "global",
        "basis": "explicit",
        "durable": True,
        "confidence": 0.98,
        "evidence": [{"turn": turn["turn"], "quote": turn["text"]}],
    }
    facts = [
        {
            **common,
            "key": f"skills.{target}.learning",
            "title": f"学习 {target}",
            "content": f"用户正在学习 {target}",
            "value": True,
            "predicate": "learning",
            "target_concept": target,
            "concept_keys": [target],
        }
    ]
    concepts = [concept]
    if level:
        existing = next(
            (f for f in payload["existing"] if f["profile_key"] == f"skills.{target}.level"), None
        )
        concepts.append({"key": value, "kind": "level", "label": label, "aliases": [value]})
        facts.append(
            {
                **common,
                "action": "supersede" if existing else "add",
                "target": existing["id"] if existing else None,
                "key": f"skills.{target}.level",
                "title": f"{target} 熟练度",
                "content": f"用户的 {target} 熟练度为{label}",
                "value": value,
                "predicate": "proficiency",
                "source_concept": value,
                "target_concept": target,
                "concept_keys": [target, value],
            }
        )
    return {"concepts": concepts, "facts": facts}


class Planner:
    def __init__(self):
        self.decide = skill_plan
        self.calls = []

    async def ainvoke(self, messages, **kwargs):
        payload = json.loads(messages[-1].text)
        self.calls.append(payload)
        return AIMessage(
            content=json.dumps(self.decide(payload), ensure_ascii=False),
            usage_metadata={"input_tokens": 200, "output_tokens": 100, "total_tokens": 300},
        )


@asynccontextmanager
async def domain(tmp_path, config=None):
    settings = CheckpointSettings(sqlite_path=tmp_path / "app.sqlite")
    async with (
        SQLiteConversationStore.open(settings) as conversations,
        MemoryStore.open(tmp_path / "workspace.sqlite", 5) as store,
    ):
        service = MemoryService(store, conversations)
        planner = Planner()
        configuration = [config or MemoryConfiguration()]

        async def load(_user):
            return 0, configuration[0]

        learner = MemoryLearner(service, load, lambda _name: ("test", planner))
        service.planner = learner

        async def observe(text, session, *, user="alice", project="project-a"):
            await conversations.save_turn(
                ConversationTurn(
                    user,
                    session,
                    uuid4().hex,
                    text,
                    "已收到",
                    learn_memory=True,
                    project_id=project,
                )
            )
            jobs = await conversations.learning_jobs(64)
            await learner.process_jobs(jobs)
            return await conversations.learning_status(user)

        yield SimpleNamespace(
            store=store,
            service=service,
            conversations=conversations,
            model=planner,
            observe=observe,
            configuration=configuration,
        )


async def test_python_beginner_independent_semantic_reinforcement(tmp_path):
    async with domain(tmp_path) as d:
        result = await d.observe("我最近在学习Python，我还是新手", "s1")
        assert result["pending"] == 0, result
        facts = await d.store.list_memories("alice")
        assert len(facts) == 2
        assert all(f["effective_frequency"] == 1 for f in facts)
        for _ in range(50):
            await d.observe("我最近在学习Python，我还是新手", "s1")
        facts = await d.store.list_memories("alice")
        assert all(f["effective_frequency"] == 1 for f in facts)
        d.model.decide = lambda p: skill_plan(p, level=False)
        await d.observe("我还在学习Python", "s2")
        facts = {f["memory_key"]: f for f in await d.store.list_memories("alice")}
        assert facts["skills.python.learning"]["effective_frequency"] == 2
        assert facts["skills.python.level"]["effective_frequency"] == 1
        graph = await d.store.graph("alice", 100, project_id="another-workspace")
        python = next(n for n in graph["nodes"] if n["concept_kind"] == "skill")
        beginner = next(n for n in graph["nodes"] if n["concept_kind"] == "level")
        assert python["effective_frequency"] == 2
        assert beginner["effective_frequency"] == 1
        edge = graph["edges"][0]
        assert (edge["source"], edge["target"]) == (beginner["id"], python["id"])
        assert edge["effective_frequency"] == 1
        recalled = await d.store.retrieve(
            user_id="alice",
            query="怎么学Python",
            thread_id="new",
            project_id="another-workspace",
            limit=8,
        )
        assert {f["memory_key"] for f in recalled} == set(facts)
        assert (
            await d.store.retrieve(
                user_id="bob", query="Python", thread_id="new", project_id="project-a", limit=8
            )
            == []
        )
        await d.observe("我还在学习Python", "s3")
        assert (await d.store.get_memory("alice", facts["skills.python.learning"]["id"]))[
            "effective_frequency"
        ] == 3
        async with d.service.transaction():
            await d.service.db.execute(
                "UPDATE workspace_memories SET created_at='2000-01-01',updated_at='2000-01-01' "
                "WHERE user_id='alice'"
            )
            await d.service.rebuild("alice")
        assert (await d.store.get_memory("alice", facts["skills.python.level"]["id"]))[
            "strength"
        ] == pytest.approx(0.2)


async def test_skill_change_versions_and_other_targets(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "s1")
        old = next(
            f for f in await d.store.list_memories("alice") if f["predicate"] == "proficiency"
        )
        d.model.decide = lambda p: skill_plan(p, target="java", value="advanced", label="熟练")
        await d.observe("我在学习Java，我很熟练", "s2")
        assert (await d.store.get_memory("alice", old["id"]))["status"] == "active"
        d.model.decide = lambda p: skill_plan(p, value="advanced", label="熟练")
        await d.observe("我仍在学习Python，现在已经熟练了", "s3")
        retired = await d.store.get_memory("alice", old["id"])
        assert retired["status"] == "superseded" and retired["superseded_by"]
        recalled = await d.store.retrieve(
            user_id="alice", query="Python", thread_id="new", project_id=None, limit=8
        )
        assert old["id"] not in [f["id"] for f in recalled]
        assert next(f for f in recalled if f["predicate"] == "proficiency")["value"] == "advanced"


async def test_duplicate_fact_concurrency_and_capacity(tmp_path):
    async with domain(tmp_path, MemoryConfiguration(active_limit=2)) as d:
        await d.observe("我在学习Python，我是新手", "s1")
        facts = await d.store.list_memories("alice")

        async def reinforce():
            async with d.service.transaction():
                await d.service.reinforce("alice", facts[0]["id"], "parallel", "same-event")

        await asyncio.gather(*(reinforce() for _ in range(20)))
        assert (await d.store.get_memory("alice", facts[0]["id"]))["effective_frequency"] == 2
        d.model.decide = lambda p: skill_plan(p, target="java", level=False)
        await d.observe("我也在学习Java", "s2")
        assert len(await d.store.list_memories("alice")) == 2
        assert len(await d.store.list_memories("alice", status="archived")) == 1
        assert await d.service.delete_memory("alice", facts[0]["id"])
        assert await d.store.get_memory("alice", facts[0]["id"]) is None
        await d.conversations.requeue_learning("alice")
        await d.service.planner.process_jobs(await d.conversations.learning_jobs())
        assert await d.store.get_memory("alice", facts[0]["id"]) is None


def test_storage_reset_once_preserves_files_and_configuration(tmp_path):
    checkpoint = tmp_path / "checkpoints.sqlite"
    for name in (
        "checkpoints.sqlite",
        "checkpoints.sessions.sqlite",
        "checkpoints.workspace.sqlite",
        "checkpoints.sqlite-wal",
        "checkpoints.sessions.sqlite-shm",
    ):
        (tmp_path / name).write_bytes(b"old")
    (tmp_path / "providers.json").write_text("provider-config")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "important.py").write_text("print('keep')")
    metadata = prepare_storage(checkpoint)
    assert metadata["version"] == 3 and metadata["reset"]
    assert not checkpoint.exists()
    checkpoint.write_bytes(b"new-data")
    assert prepare_storage(checkpoint) == metadata
    assert checkpoint.read_bytes() == b"new-data"
    assert (tmp_path / "providers.json").read_text() == "provider-config"
    assert (tmp_path / "workspaces" / "important.py").exists()


def test_identity_api_unified_profile_no_model_fabrication(tmp_path):
    inputs = []

    def reply(messages):
        inputs.append(messages)
        return AIMessage(content="一个不可靠的名字")

    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "app.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(reply)),
        memory_processing="rules",
    )
    with TestClient(app) as client:
        unknown = client.post(BASE + "/chat", json={"message": "我叫什么名字？"}).json()
        assert "没有" in unknown["response"] and "不可靠" not in unknown["response"]
        assert unknown["response_source"] == "identity_fact" and not inputs
        created = client.post(
            BASE + "/memories",
            json={
                "title": "姓名",
                "content": "林舟",
                "value": "林舟",
                "profile_dimension": "identity",
                "profile_key": "user.name",
            },
        )
        assert created.status_code == 201, created.text
        profile = client.get(BASE + "/profile").json()
        assert "preferences" not in profile
        assert profile["attributes"][0]["key"] == "identity.name"
        known = client.post(BASE + "/chat", json={"message": "我是谁？"}).json()
        assert "林舟" in known["response"] and known["used_memory_ids"] == [created.json()["id"]]
        assert known["metrics"]["total_tokens"] == 0 and not inputs
        saved = client.patch(
            BASE + "/profile",
            json={
                "expected_version": profile["version"],
                "attributes": [{"key": "identity.name", "dimension": "identity", "value": "林海"}],
            },
        )
        assert saved.status_code == 200, saved.text
        corrected = client.post(BASE + "/chat", json={"message": "what is my name?"}).json()
        assert "林海" in corrected["response"] and "林舟" not in corrected["response"]
        history = client.get(BASE + "/threads/" + known["thread_id"] + "/messages").json()
        assert history["items"][0]["memory_trace"]["response_source"] == "identity_fact"


@pytest.mark.parametrize(
    "query,expected",
    [
        ("我是谁？", "identity"),
        ("我叫什么名字", "name"),
        ("你还记得我吗", "identity"),
        ("what is my name?", "name"),
        ("帮我解释‘我是谁’这句话", None),
        ("我是谁？再写一个脚本", None),
        ("他叫什么名字", None),
    ],
)
def test_identity_intent_is_bounded(query, expected):
    assert identity_intent(query) == expected


async def test_level_aliases_and_project_concept_credit_are_scoped(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "global")
        d.model.decide = lambda p: skill_plan(p, level=False)
        await d.observe("在这个项目里我在学习Python", "private", project="project-a")
        recalled = await d.store.retrieve(
            user_id="alice", query="初学者", thread_id="new", project_id="project-b", limit=8
        )
        assert len(recalled) == 1 and recalled[0]["value"] == "beginner"
        assert all(f["scope"] == "global" for f in recalled)
        other = await d.store.graph("alice", 100, project_id="project-b")
        own = await d.store.graph("alice", 100, project_id="project-a")
        assert (
            next(n for n in other["nodes"] if n["concept_kind"] == "skill")["effective_frequency"]
            == 1
        )
        own_skills = [n for n in own["nodes"] if n["concept_kind"] == "skill"]
        assert len(own_skills) == 2
        assert {n["scope"] for n in own_skills} == {"global", "project"}
        assert sum(n["effective_frequency"] for n in own_skills) == 2


async def test_sqlite_uniqueness_across_two_writer_connections(tmp_path):
    async with (
        domain(tmp_path) as d,
        MemoryStore.open(tmp_path / "workspace.sqlite", 5) as other_store,
    ):
        await d.observe("我在学习Python，我是新手", "first")
        mid = (await d.store.list_memories("alice"))[0]["id"]
        second = MemoryService(other_store, d.conversations)

        async def submit(service):
            async with service.transaction():
                return await service.reinforce("alice", mid, "same-session", "replayed-event")

        results = await asyncio.gather(*(submit(s) for s in [d.service, second] * 5))
        assert sum(results) == 1
        assert (await d.store.get_memory("alice", mid))["effective_frequency"] == 2


async def test_protected_capacity_pending_and_atomic_erasure(tmp_path, monkeypatch):
    async with domain(tmp_path, MemoryConfiguration(active_limit=1)) as d:
        first = await d.service.create_memory(
            "alice",
            {
                "title": "name",
                "content": "林舟",
                "value": "林舟",
                "type": "semantic",
                "scope": "global",
                "scope_ref": None,
            },
            "identity",
            "identity.name",
        )
        second = await d.service.create_memory(
            "alice",
            {
                "title": "style",
                "content": "简洁",
                "type": "preference",
                "scope": "global",
                "scope_ref": None,
            },
        )
        assert second is None
        assert (await d.store.get_memory("alice", first["id"]))["status"] == "active"
        assert (await d.service.suggestions("alice"))[0]["pending_reason"] == "capacity"
        original = d.service.redact_profile_history

        async def fail(*args):
            raise RuntimeError("injected crash before privacy cleanup")

        monkeypatch.setattr(d.service, "redact_profile_history", fail)
        with pytest.raises(RuntimeError):
            await d.service.delete_memory("alice", first["id"])
        assert await d.store.get_memory("alice", first["id"])
        monkeypatch.setattr(d.service, "redact_profile_history", original)
        claim_id = (
            await d.service.rows("SELECT id FROM memory_claims WHERE memory_id=?", (first["id"],))
        )[0]["id"]
        assert await d.service.delete_memory("alice", first["id"])
        erased = (await d.service.rows("SELECT * FROM memory_claims WHERE id=?", (claim_id,)))[0]
        assert erased["value"] == erased["title"] == "" and erased["semantic_json"] == "{}"
        assert "林舟" not in json.dumps(await d.store.get_profile("alice"), ensure_ascii=False)
        assert not await d.service.rows(
            "SELECT * FROM memory_search WHERE memory_id=?", (first["id"],)
        )


async def test_llm_identity_global_projection_and_foreign_source_rejected(tmp_path):
    def name_plan(payload, name="林舟"):
        t = payload["turns"][0]
        return {
            "concepts": [],
            "facts": [
                {
                    "action": "add",
                    "kind": "fact",
                    "key": "user.name",
                    "title": "用户姓名",
                    "content": "用户姓名是" + name,
                    "value": name,
                    "scope": "project",
                    "basis": "explicit",
                    "durable": True,
                    "confidence": 0.99,
                    "evidence": [{"turn": t["turn"], "quote": t["text"]}],
                }
            ],
        }

    async with domain(tmp_path) as d:
        d.model.decide = name_plan
        await d.observe("我叫林舟", "identity", project="project-a")
        fact = (await d.store.list_memories("alice"))[0]
        assert fact["dimension"] == "identity" and fact["scope"] == "global"
        assert fact["memory_key"] == "identity.name" and fact["value"] == "林舟"
        assert (await d.store.get_profile("alice"))["attributes"][0]["memory_id"] == fact["id"]
        d.model.decide = lambda p: name_plan(p, "张三")
        await d.observe("我的同事说他叫张三", "foreign")
        assert len(await d.store.list_memories("alice")) == 1
        assert (await d.store.list_memories("alice"))[0]["value"] == "林舟"


async def test_ambiguous_skill_and_task_mentions_never_become_profile_facts(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("推荐一本Python书，我的朋友是新手", "task")
        # A deliberately bad model decision does not become a reliable skill profile.
        # 刻意错误的模型输出不能靠高置信度绕过来源检查。
        assert not await d.store.list_memories("alice")
        d.model.decide = lambda p: {
            "concepts": [],
            "facts": [
                {
                    "action": "add",
                    "kind": "skill",
                    "key": "skills.unknown.level",
                    "title": "程度",
                    "content": "用户是新手，但技能对象不明确",
                    "value": "beginner",
                    "predicate": "proficiency",
                    "ambiguous": True,
                    "scope": "global",
                    "basis": "explicit",
                    "durable": True,
                    "confidence": 0.99,
                    "evidence": [{"turn": p["turns"][0]["turn"], "quote": p["turns"][0]["text"]}],
                }
            ],
        }
        await d.observe("我还是新手", "ambiguous")
        assert not await d.store.list_memories("alice")
        assert any(
            s["pending_reason"] == "ambiguous_target" for s in await d.service.suggestions("alice")
        )


def test_storage_refuses_future_version_and_retries_interrupted_old_reset(tmp_path):
    checkpoint = tmp_path / "app.sqlite"
    checkpoint.write_bytes(b"legacy")
    marker = checkpoint.with_suffix(".storage.json")
    marker.write_text(json.dumps({"version": 4, "epoch": "future"}))
    with pytest.raises(RuntimeError):
        prepare_storage(checkpoint)
    assert checkpoint.read_bytes() == b"legacy"
    marker.write_text(json.dumps({"version": 2, "epoch": "old"}))
    metadata = prepare_storage(checkpoint)
    assert metadata["version"] == 3 and not checkpoint.exists()


async def test_denial_invalidates_without_reinforcing_or_inventing_a_level(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "first")
        old = next(
            f for f in await d.store.list_memories("alice") if f["predicate"] == "proficiency"
        )

        def denial(payload):
            turn = payload["turns"][0]
            target = next(
                f for f in payload["existing"] if f["profile_key"] == "skills.python.level"
            )
            return {
                "concepts": [],
                "facts": [
                    {
                        "action": "invalidate",
                        "target": target["id"],
                        "kind": "skill",
                        "key": "skills.python.level",
                        "title": "否认旧程度",
                        "content": "用户不再是Python新手，但尚未说明新程度",
                        "scope": "global",
                        "basis": "explicit",
                        "durable": True,
                        "confidence": 0.99,
                        "evidence": [{"turn": turn["turn"], "quote": turn["text"]}],
                    }
                ],
            }

        d.model.decide = denial
        await d.observe("我已经不是Python新手了", "denial")
        changed = await d.store.get_memory("alice", old["id"])
        assert changed["status"] == "invalidated" and changed["effective_frequency"] == 1
        assert all(
            a["key"] != "skills.python.level"
            for a in (await d.store.get_profile("alice"))["attributes"]
        )
        assert not any(
            f["predicate"] == "proficiency"
            for f in await d.store.retrieve(
                user_id="alice", query="Python", thread_id="new", project_id=None, limit=8
            )
        )


async def test_canonical_concept_prevents_alias_fact_duplication(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "first")

        def renamed(payload):
            value = skill_plan(payload)
            value["concepts"][0]["key"] = "python_language"
            value["concepts"][0]["same_as"] = "python"
            for f in value["facts"]:
                f["target_concept"] = "python_language"
                f["concept_keys"] = [
                    "python_language" if k == "python" else k for k in f["concept_keys"]
                ]
            return value

        d.model.decide = renamed
        await d.observe("我仍在学习Python，还是新手", "second")
        facts = await d.store.list_memories("alice")
        assert len(facts) == 2
        assert {f["memory_key"] for f in facts} == {"skills.python.learning", "skills.python.level"}
        assert all(f["effective_frequency"] == 2 for f in facts)


async def test_skill_names_do_not_merge_by_substring_aliases(tmp_path):
    async with domain(tmp_path) as d:
        d.model.decide = lambda p: skill_plan(p, target="java", level=False)
        await d.observe("我在学习Java", "java")

        def javascript(payload):
            value = skill_plan(payload, target="javascript", level=False)
            value["concepts"][0]["aliases"].append("java")
            return value

        d.model.decide = javascript
        await d.observe("我还在学习JavaScript", "javascript")
        facts = await d.store.list_memories("alice")
        assert len({f["target_concept_id"] for f in facts}) == 2
        assert {f["memory_key"] for f in facts} == {
            "skills.java.learning",
            "skills.javascript.learning",
        }


def test_manual_skill_profile_and_configured_strength_share_one_fact(tmp_path):
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "app.sqlite"),
        model_provider=SimpleNamespace(
            get_chat_model=lambda: RunnableLambda(lambda _: AIMessage(content="ok"))
        ),
        memory_processing="rules",
    )
    with TestClient(app) as client:
        created = client.post(
            BASE + "/memories",
            json={
                "title": "Python熟练度",
                "content": "新手",
                "value": "新手",
                "profile_dimension": "skill",
                "profile_key": "skills.python.level",
            },
        )
        assert created.status_code == 201, created.text
        original = created.json()
        assert (
            original["value"] == "beginner"
            and original["source_concept_id"]
            and original["target_concept_id"]
        )
        profile = client.get(BASE + "/profile").json()
        assert profile["attributes"][0]["memory_id"] == original["id"]
        edited = client.patch(
            BASE + "/profile",
            json={
                "expected_version": profile["version"],
                "attributes": [
                    {"key": "skills.python.level", "dimension": "skill", "value": "熟练"}
                ],
            },
        )
        assert edited.status_code == 200, edited.text
        assert edited.json()["attributes"][0]["value"] == "advanced"
        assert client.get(BASE + "/memories/" + original["id"]).json()["status"] == "superseded"
        graph = client.get(BASE + "/graph").json()
        assert len(graph["edges"]) == 1 and graph["edges"][0]["relation"] == "proficiency"
        config = client.get(BASE + "/settings/memory").json()
        config["configuration"]["strength_c"] = 8
        assert (
            client.patch(
                BASE + "/settings/memory",
                json={
                    "expected_revision": config["revision"],
                    "configuration": config["configuration"],
                },
            ).status_code
            == 200
        )
        fact = client.get(BASE + "/memories").json()["items"][0]
        assert fact["strength"] == pytest.approx(1 / 9)


async def test_low_confidence_evidence_cannot_strengthen_existing_relationships(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "confirmed")

        def uncertain(payload):
            plan = skill_plan(payload)
            for fact in plan["facts"]:
                fact["confidence"] = 0.4
                fact["basis"] = "inferred"
            return plan

        d.model.decide = uncertain
        await d.observe("我在学习Python，我是新手", "uncertain")
        facts = await d.store.list_memories("alice")
        assert all(f["effective_frequency"] == 1 for f in facts)
        graph = await d.store.graph("alice", 100)
        assert all(n["effective_frequency"] == 1 for n in graph["nodes"])
        d.model.decide = skill_plan
        await d.observe("我仍在学习Python，还是新手", "reliable")
        assert all(f["effective_frequency"] == 2 for f in await d.store.list_memories("alice"))


async def test_archived_sources_do_not_reactivate_but_new_confirmation_can(tmp_path):
    async with domain(tmp_path) as d:
        d.model.decide = lambda p: skill_plan(p, level=False)
        await d.observe("我在学习Python", "first")
        original = (await d.store.list_memories("alice"))[0]
        # Capacity archiving preserves evidence, unlike an explicit manual removal.
        async with d.service.transaction():
            await d.store.update_memory("alice", original["id"], {"status": "archived"})
            await d.service.rebuild("alice")
        await d.conversations.requeue_learning("alice")
        await d.service.planner.process_jobs(await d.conversations.learning_jobs(64))
        assert not await d.store.list_memories("alice")
        await d.observe("我仍在学习Python", "new")
        restored = (await d.store.list_memories("alice"))[0]
        assert restored["id"] == original["id"]
        assert restored["effective_frequency"] == 2


async def test_manual_value_reversion_keeps_each_version_claims(tmp_path):
    async with domain(tmp_path) as d:
        versions = []
        for name in ["林舟", "林海", "林舟"]:
            memory = await d.service.create_memory(
                "alice",
                {
                    "title": "name",
                    "content": name,
                    "value": name,
                    "type": "semantic",
                    "scope": "global",
                },
                "identity",
                "identity.name",
            )
            versions.append(memory)
        assert len({m["id"] for m in versions}) == 3
        for memory in versions:
            claims = await d.service.rows(
                "SELECT value FROM memory_claims WHERE memory_id=?", (memory["id"],)
            )
            assert claims and {c["value"] for c in claims} == {memory["value"]}


async def test_private_concept_alias_does_not_recall_other_workspaces(tmp_path):
    async with domain(tmp_path) as d:
        d.model.decide = lambda p: skill_plan(p, level=False)
        await d.observe("我在学习Python", "global")

        def private(payload):
            plan = skill_plan(payload, level=False)
            plan["concepts"][0]["aliases"].append("私有蛇")
            return plan

        d.model.decide = private
        await d.observe("在这个项目里我在学习Python，也称私有蛇", "private")
        assert not await d.store.retrieve(
            user_id="alice", query="私有蛇", thread_id="new", project_id="project-b", limit=8
        )
        visible = await d.store.retrieve(
            user_id="alice", query="私有蛇", thread_id="new", project_id="project-a", limit=8
        )
        assert visible and any(f["scope"] == "project" for f in visible)


async def test_homonymous_labels_do_not_merge_distinct_semantic_concepts(tmp_path):
    async with domain(tmp_path) as d:
        for key, meaning in [("java_island", "岛屿"), ("java_coffee", "咖啡")]:

            def propose(payload, key=key, meaning=meaning):
                turn = payload["turns"][0]
                return {
                    "concepts": [{"key": key, "kind": "entity", "label": "Java"}],
                    "facts": [
                        {
                            "action": "add",
                            "kind": "fact",
                            "key": key,
                            "title": meaning,
                            "content": "Java表示" + meaning,
                            "value": meaning,
                            "scope": "project",
                            "basis": "explicit",
                            "durable": True,
                            "confidence": 0.99,
                            "concept_keys": [key],
                            "target_concept": key,
                            "predicate": "meaning",
                            "evidence": [{"turn": turn["turn"], "quote": turn["text"]}],
                        }
                    ],
                }

            d.model.decide = propose
            await d.observe("本项目的Java表示" + meaning, key)
        facts = await d.store.list_memories("alice")
        assert len(facts) == 2 and len({f["target_concept_id"] for f in facts}) == 2


async def test_concept_metadata_and_relationship_share_the_context_budget(tmp_path):
    async with domain(tmp_path) as d:
        await d.observe("我在学习Python，我是新手", "first")
        facts = await d.store.retrieve(
            user_id="alice", query="Python", thread_id="new", project_id=None, limit=8
        )
        result = await BoundedWorkspaceContext().build(
            user_input="Python",
            user_profile=await d.store.get_profile("alice"),
            relevant_memories=facts,
            project_id=None,
            token_budget=1200,
        )
        assert '"predicate":"proficiency"' in result.text
        assert '"target":"Python"' in result.text
        assert '"source":"新手"' in result.text
        assert len(result.text.encode()) <= result.token_estimate <= 1200
        tiny = await BoundedWorkspaceContext().build(
            user_input="Python",
            user_profile=None,
            relevant_memories=facts,
            project_id=None,
            token_budget=10,
        )
        assert not tiny.memory_ids and not tiny.text
