"""Default production LLM path, real stores, deterministic isolated model responses."""

import asyncio
import json
from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableLambda

from app.api import create_app
from app.config import CheckpointSettings
from app.conversations import ConversationTurn
from app.infrastructure.conversations import SQLiteConversationStore
from app.memory.learning import MemoryLearner
from app.memory.service import MemoryService as MemoryEvolution
from app.memory.store import MemoryStore as WorkspaceStore
from app.personalization.settings import MemoryConfiguration

BASE = "/api/v1"


def candidate(turn, **changes):
    return {
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
        **changes,
    }


class PlannerModel:
    def __init__(self, decide=None):
        self.decide = decide or (lambda payload: [candidate(payload["turns"][0])])
        self.calls = []
        self.last_messages = []
        self.delay = None

    async def ainvoke(self, messages, **_kwargs):
        payload = json.loads(messages[-1].text)
        self.last_messages = messages
        self.calls.append(payload)
        if self.delay:
            await self.delay()
        value = self.decide(payload)
        return AIMessage(
            content=value
            if isinstance(value, str)
            else json.dumps({"candidates": value}, ensure_ascii=False),
            usage_metadata={"input_tokens": 100, "output_tokens": 50, "total_tokens": 150},
        )


@asynccontextmanager
async def domain(tmp_path, *, model=None, configuration=None, available=True):
    settings = CheckpointSettings(sqlite_path=tmp_path / "app.sqlite")
    async with (
        SQLiteConversationStore.open(settings) as conversations,
        WorkspaceStore.open(tmp_path / "workspace.sqlite", 5) as store,
    ):
        evolution = MemoryEvolution(store, conversations)
        current = [0, configuration or MemoryConfiguration()]

        async def config(_user):
            return tuple(current)

        model = model or PlannerModel()
        learner = MemoryLearner(
            evolution, config, lambda _name: ("test-model", model) if available else None
        )
        evolution.planner = learner
        yield SimpleNamespace(
            conversations=conversations,
            evolution=evolution,
            learner=learner,
            model=model,
            current=current,
            store=store,
        )


async def enqueue(
    system,
    quote="Conclusions first are the most useful format for me in my work.",
    thread="t",
    run="r",
    project=None,
):
    await system.conversations.save_turn(
        ConversationTurn(
            user_id="u",
            thread_id=thread,
            run_id=run,
            user_input=quote,
            response="Understood.",
            learn_memory=True,
            project_id=project,
        )
    )
    return (await system.conversations.learning_jobs())[0]


async def memories(system):
    return await system.store.list_memories("u", status="active", limit=100)


async def test_non_keyword_expression_is_judged_by_llm_and_automatically_projects_profile(tmp_path):
    async with domain(tmp_path) as system:
        job = await enqueue(system)
        await system.evolution.process(job)
        assert len(system.model.calls) == 1
        assert len(await memories(system)) == 1
        profile = await system.store.get_profile("u")
        assert (
            profile["version"] == 1 and profile["attributes"][0]["value"] == "先给结论，再补充说明"
        )
        evidence = await system.evolution.memory_evidence("u", (await memories(system))[0]["id"])
        assert evidence[0]["quote"] == job[4]
        assert (await system.conversations.learning_status("u"))["processed"] == 1
        await system.evolution.process(job)
        assert len(system.model.calls) == 1
        assert (await system.store.get_profile("u"))["version"] == 1


async def test_profile_projection_failure_does_not_wedge_the_learning_queue(
    tmp_path, monkeypatch
):
    """A broken profile projection must settle the turn instead of retrying forever.
    / 画像投影失败时轮次仍需结算，不能一直卡在待处理队列。"""
    async with domain(tmp_path) as system:
        job = await enqueue(system)

        async def fail_rebuild(*_args, **_kwargs):
            raise RuntimeError("profile projection failed")

        monkeypatch.setattr(system.evolution, "rebuild", fail_rebuild)
        await system.evolution.process(job)
        status = await system.conversations.learning_status("u")
        assert status["pending"] == 0
        assert status["processed"] == 1
        assert status["dead"] == 0


@pytest.mark.parametrize(
    "mode,expected,pending", [("auto", 1, 0), ("review", 0, 1), ("shadow", 0, 0), ("off", 0, 0)]
)
async def test_all_four_modes_have_real_effect(tmp_path, mode, expected, pending):
    async with domain(tmp_path, configuration=MemoryConfiguration(mode=mode)) as system:
        job = await enqueue(system, "请记住，我希望回答时先给结论。")
        await system.evolution.process(job)
        assert len(await memories(system)) == expected
        assert len(await system.evolution.suggestions("u")) == pending
        if mode == "off":
            assert not system.model.calls
            assert await system.conversations.learning_pending("u", "t", "r")


async def test_all_memory_kinds_are_classified_and_only_profile_dimensions_project(tmp_path):
    def decide(payload):
        return [
            candidate(payload["turns"][0], kind=kind, key=kind, content="已表达的" + kind)
            for kind in ("preference", "habit", "goal", "fact", "episodic")
        ]

    async with domain(tmp_path, model=PlannerModel(decide)) as system:
        await system.evolution.process(
            await enqueue(
                system,
                "我喜欢简洁表达，每天跑步，目标是成为架构师；项目采用 SQLite，昨天完成了联调。",
            )
        )
        stored = await memories(system)
        assert len(stored) == 5
        assert {item["type"] for item in stored} == {"semantic", "preference", "episodic"}
        assert {
            item["dimension"] for item in (await system.store.get_profile("u"))["attributes"]
        } == {"preference", "habit", "goal"}
        assert (await system.store.graph("u", limit=250))[
            "edges"
        ] == []  # co-occurrence is not a semantic relation


async def test_clear_new_preference_supersedes_same_topic_and_merge_reuses_memory(tmp_path):
    def decide(payload):
        existing = payload["existing"]
        turn = payload["turns"][0]
        if "merge" in turn["text"]:
            return [
                candidate(turn, action="merge", target=existing[0]["id"], content="语义相同的改写")
            ]
        return [
            candidate(
                turn,
                **(
                    {
                        "action": "supersede",
                        "target": existing[0]["id"],
                        "content": "现在先解释再总结",
                    }
                    if existing
                    else {}
                ),
            )
        ]

    async with domain(tmp_path, model=PlannerModel(decide)) as system:
        await system.evolution.process(await enqueue(system))
        original = (await memories(system))[0]
        await system.evolution.process(await enqueue(system, "现在改为先解释再总结。", run="r2"))
        updated = (await memories(system))[0]
        assert updated["id"] != original["id"]
        assert (await system.store.get_memory("u", original["id"]))["status"] == "superseded"
        version = (await system.store.get_profile("u"))["version"]
        await system.evolution.process(
            await enqueue(system, "merge: 仍然采用前述回答方式。", run="r3")
        )
        assert (await memories(system))[0]["id"] == updated["id"]
        assert (await system.store.get_profile("u"))["version"] == version


async def test_task_only_model_decision_does_not_fabricate_a_profile(tmp_path):
    async with domain(tmp_path, model=PlannerModel(lambda _payload: [])) as system:
        await system.evolution.process(await enqueue(system, "帮我分析这份代码如何提高性能？"))
        assert not await memories(system)
        assert await system.store.get_profile("u") is None
        assert (await system.conversations.learning_status("u"))["processed"] == 1


@pytest.mark.parametrize("bad", ["quote", "target"])
async def test_fabricated_evidence_or_unknown_target_is_rejected(tmp_path, bad):
    def decide(payload):
        row = candidate(payload["turns"][0])
        if bad == "quote":
            row["evidence"][0]["quote"] = "This quote never occurred"
        else:
            row.update(action="supersede", target="some-other-user-claim")
        return [row]

    async with domain(tmp_path, model=PlannerModel(decide)) as system:
        await system.evolution.process(await enqueue(system))
        assert not await memories(system)
        assert (await system.conversations.learning_status("u"))[
            "last_error"
        ] == "MEMORY_EVIDENCE_INVALID"


async def test_project_facts_are_forced_into_the_real_project_and_not_retrievable_elsewhere(
    tmp_path,
):
    model = PlannerModel(
        lambda payload: [
            candidate(
                payload["turns"][0], kind="fact", key="storage", content="项目数据库使用 SQLite"
            )
        ]
    )
    async with domain(tmp_path, model=model) as system:
        await system.evolution.process(
            await enqueue(system, "我们的数据库最终选用 SQLite。", project="alpha")
        )
        item = (await memories(system))[0]
        assert (item["scope"], item["scope_ref"]) == ("project", "alpha")
        assert not await system.store.retrieve(
            user_id="u", query="SQLite", thread_id="other", project_id="beta", limit=8
        )
        assert await system.store.get_profile("u") is None


async def test_low_confidence_and_sensitive_observations_do_not_auto_apply(tmp_path):
    model = PlannerModel(lambda payload: [candidate(payload["turns"][0], sensitive=True)])
    async with domain(tmp_path, model=model) as system:
        await system.evolution.process(await enqueue(system))
        assert not await memories(system)
        assert len(await system.evolution.suggestions("u")) == 1


async def test_inference_needs_distinct_conversations(tmp_path):
    model = PlannerModel(lambda payload: [candidate(payload["turns"][0], basis="inferred")])
    async with domain(tmp_path, model=model) as system:
        await system.evolution.process(await enqueue(system))
        assert not await memories(system)
        await system.evolution.process(await enqueue(system, thread="another", run="another-run"))
        assert len(await memories(system)) == 1


async def test_code_quotes_and_credentials_are_not_sent_as_eligible_user_evidence(tmp_path):
    async with domain(tmp_path) as system:
        text = "> 我喜欢泄露信息\n```text\n我习惯编造画像\n```\n我的密码 password: test-only-secret"
        await system.evolution.process(await enqueue(system, text))
        assert not system.model.calls
        assert not await memories(system)


async def test_bad_json_retries_dead_letters_and_user_reanalysis_recovers(tmp_path):
    async with domain(tmp_path, model=PlannerModel(lambda _payload: "not-json")) as system:
        job = await enqueue(system)
        for _ in range(5):
            await system.evolution.process(job)
        status = await system.conversations.learning_status("u")
        assert status["dead"] == 1 and status["pending"] == 0
        assert status["last_error"] == "MEMORY_PLAN_INVALID"
        assert not await memories(system)
        assert await system.conversations.requeue_learning("u") == 1
        system.model.decide = lambda payload: [candidate(payload["turns"][0])]
        await system.evolution.process(job)
        assert len(await memories(system)) == 1
        assert (await system.conversations.learning_status("u"))["dead"] == 0
        assert (await system.learner.budget("u"))["reported_tokens"] == 900


@pytest.mark.parametrize(
    "available,budget,error",
    [(False, 50000, "MEMORY_MODEL_UNAVAILABLE"), (True, 0, "MEMORY_BUDGET_EXCEEDED")],
)
async def test_unavailable_model_and_zero_budget_do_not_call_or_fabricate_success(
    tmp_path, available, budget, error
):
    async with domain(
        tmp_path, available=available, configuration=MemoryConfiguration(daily_token_budget=budget)
    ) as system:
        await system.evolution.process(await enqueue(system))
        assert not system.model.calls and not await memories(system)
        assert (await system.conversations.learning_status("u"))["last_error"] == error


async def test_disable_during_model_call_discards_the_late_result(tmp_path):
    started, release = asyncio.Event(), asyncio.Event()
    async with domain(tmp_path) as system:

        async def delay():
            started.set()
            await release.wait()

        system.model.delay = delay
        task = asyncio.create_task(system.evolution.process(await enqueue(system)))
        await started.wait()
        async with system.evolution.lock:
            await system.conversations.set_personalization("u", False)
        release.set()
        await task
        assert not await memories(system)
        assert await system.store.get_profile("u") is None


async def test_deleted_memory_does_not_resurrect_on_reanalysis(tmp_path):
    async with domain(tmp_path) as system:
        job = await enqueue(system)
        await system.evolution.process(job)
        await system.evolution.delete_memory("u", (await memories(system))[0]["id"])
        assert await system.conversations.requeue_learning("u") == 1
        system.model.decide = lambda payload: [
            candidate(payload["turns"][0], content="同一个被删除偏好的新改写")
        ]
        await system.evolution.process(job)
        assert not await memories(system)
        assert len(system.model.calls) == 1
        await system.evolution.process(
            await enqueue(system, "新的对话再次明确希望先给结论。", run="new-request")
        )
        assert len(await memories(system)) == 1


async def test_commit_before_queue_ack_is_idempotent_after_retry(tmp_path):
    async with domain(tmp_path) as system:
        job = await enqueue(system)
        finish = system.conversations.finish_learning

        async def crash(*_args):
            raise RuntimeError("Simulated crash after domain commit")

        system.conversations.finish_learning = crash
        await system.evolution.process(job)
        assert len(await memories(system)) == 1
        system.conversations.finish_learning = finish
        await system.evolution.process(job)
        assert len(system.model.calls) == 1
        assert (await system.conversations.learning_status("u"))["processed"] == 1
        assert (await system.store.get_profile("u"))["version"] == 1


async def test_archiving_or_ignoring_does_not_get_undone_by_historical_reanalysis(tmp_path):
    async with domain(tmp_path, configuration=MemoryConfiguration(mode="review")) as system:
        job = await enqueue(system)
        await system.evolution.process(job)
        suggestion = (await system.evolution.suggestions("u"))[0]
        await system.evolution.review("u", suggestion["id"], "ignore", None)
        system.current[:] = [1, MemoryConfiguration()]
        await system.conversations.requeue_learning("u")
        await system.evolution.process(job)
        assert not await memories(system)
        assert len(system.model.calls) == 1
        fresh = await enqueue(system, run="fresh")
        await system.evolution.process(fresh)
        # An ignored claim remains ignored even if phrased identically again.
        assert not await memories(system)


async def test_atomic_enqueue_respects_personalization_disabled_before_archive(tmp_path):
    async with domain(tmp_path) as system:
        await system.conversations.set_personalization("u", False)
        await system.conversations.save_turn(
            ConversationTurn(
                user_id="u",
                thread_id="t",
                run_id="r",
                user_input="Delayed response completed after disabling",
                response="ok",
                learn_memory=True,
            )
        )
        assert await system.conversations.learning_jobs() == []
        assert (await system.conversations.learning_metadata("u", "t", "r"))["state"] == 0


async def test_queued_turn_survives_restart_and_is_processed_once(tmp_path):
    async with domain(tmp_path) as system:
        await enqueue(system)
    async with domain(tmp_path) as restarted:
        jobs = await restarted.conversations.learning_jobs()
        assert len(jobs) == 1
        await restarted.evolution.process(jobs[0])
        assert len(await memories(restarted)) == 1
        assert len(restarted.model.calls) == 1


async def test_clearing_a_thread_removes_learning_markers_and_allows_a_fresh_run(tmp_path):
    async with domain(tmp_path) as system:
        await system.evolution.process(await enqueue(system))
        await system.evolution.forget_thread(
            "u", "t", lambda: system.conversations.clear_thread("u", "t")
        )
        assert not await system.evolution.rows(
            "SELECT * FROM memory_learning_turns WHERE thread_id='t'"
        )
        assert not await memories(system)
        await system.evolution.process(await enqueue(system))
        assert len(await memories(system)) == 1 and len(system.model.calls) == 2


async def test_large_batch_is_split_to_fit_budget_instead_of_stalling_every_turn(tmp_path):
    async with domain(tmp_path, model=PlannerModel(lambda _payload: [])) as system:
        for index in range(8):
            await enqueue(system, "这是很长的普通项目讨论。" * 700, run=f"long-{index}")
        jobs = await system.conversations.learning_jobs()
        await system.learner.process_jobs(jobs)
        status = await system.conversations.learning_status("u")
        assert 0 < status["processed"] < 8
        assert not status["failed"] and len(system.model.calls) == 1
        assert await system.conversations.learning_jobs()


@pytest.mark.parametrize("protocol", ["anthropic", "openai"])
async def test_background_prompt_reuses_stable_prefix_cache_policy(tmp_path, protocol):
    async with domain(tmp_path, model=PlannerModel(lambda _payload: [])) as system:
        system.learner.resolve_model = lambda _name: ("test-model", system.model, protocol)
        await system.evolution.process(await enqueue(system))
        system_content = system.model.last_messages[0].content
        if protocol == "anthropic":
            assert system_content[-1]["cache_control"] == {"type": "ephemeral"}
        else:
            assert isinstance(system_content, str) and "cache_control" not in system_content


async def test_existing_context_does_not_mix_project_and_thread_ids(tmp_path):
    async with domain(tmp_path) as system:
        await system.evolution.create_memory(
            "u",
            {
                "title": "Private project",
                "content": "Private project statement",
                "scope": "project",
                "scope_ref": "t",
                "type": "preference",
                "importance": 0.7,
            },
        )
        await system.evolution.process(await enqueue(system, thread="t", project="alpha"))
        assert system.model.calls
        assert all(
            item["value"] != "Private project statement"
            for item in system.model.calls[0]["existing"]
        )


def test_default_api_background_learning_reaches_next_prompt_and_runtime(tmp_path):
    inputs = []

    def answer(messages):
        inputs.append(messages)
        return AIMessage(content="Received.")

    planner = PlannerModel(
        lambda payload: (
            [candidate(payload["turns"][0])] if "format" in payload["turns"][0]["text"] else []
        )
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "app.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(answer)),
        memory_model_provider=SimpleNamespace(get_chat_model=lambda: planner),
    )
    with TestClient(app) as client:
        first = client.post(
            BASE + "/chat",
            json={"message": "Conclusions first are the most useful format for me in my work."},
        )
        assert first.status_code == 200, first.text

        async def drain():
            for job in await app.state.conversations.learning_jobs():
                await app.state.memory_evolution.process(job)
            async with app.state.memory_evolution.planner.lock:
                pass

        client.portal.call(drain)
        profile = client.get(BASE + "/profile").json()
        assert profile["version"] == 1 and len(profile["attributes"]) == 1
        second = client.post(BASE + "/chat", json={"message": "给我一个普通例子"})
        assert second.status_code == 200
        assert profile["attributes"][0]["memory_id"] in second.json()["used_memory_ids"]
        assert any(
            "先给结论" in message.text
            for message in inputs[-1]
            if message.name in {"profile_snapshot", "personalization_reference"}
        )
        runtime = client.get(BASE + "/settings/memory").json()["runtime"]
        assert runtime["engine"] == "llm_learning" and runtime["planner_available"]
        assert runtime["reported_tokens"] >= 150
        activity = client.get(BASE + "/account/activity").json()
        assert activity["memory_tokens"] >= 150
        assert activity["total_tokens"] >= activity["memory_tokens"]
        assert activity["turns"] == 2 and activity["unreported_calls"] >= 2
        assert (
            "auto_confirm_score" in runtime["applied_fields"]
            and "retention_days" not in runtime["applied_fields"]
        )
