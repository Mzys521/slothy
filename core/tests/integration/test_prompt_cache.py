"""Prompt bytes, real SQLite invalidation and existing API boundary regression tests."""

import json
from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.runnables import RunnableLambda
from langgraph.checkpoint.memory import InMemorySaver

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.agent.metrics import run_metrics
from app.agent.prompting.plan import canonical, compose, stable_message
from app.agent.prompting.telemetry import PromptTelemetry, cache_write_tokens
from app.config import AgentSettings, CheckpointSettings, PromptSettings
from app.infrastructure.prompt_cache import CachedMemoryReader, ProfileSnapshots
from app.infrastructure.workspace import WorkspaceStore
from tests.support.legacy_app import create_app
from tests.support.profiles import attributes


def settings(**values):
    return PromptSettings(_env_file=None, **values)


def provider(record):
    def respond(messages):
        record.append(deepcopy(messages))
        return AIMessage(
            content="Actual response",
            usage_metadata={
                "input_tokens": 100,
                "output_tokens": 20,
                "total_tokens": 120,
                "input_token_details": {"cache_read": 70, "cache_creation": 10},
            },
        )

    return SimpleNamespace(get_chat_model=lambda: RunnableLambda(respond))


def prompt_plan(**overrides):
    messages = [
        HumanMessage(content="old user", id="old:user"),
        AIMessage(content="old answer"),
        HumanMessage(content="current", id="run:user"),
        AIMessage(content="", tool_calls=[{"id": "call", "name": "read", "args": {}}]),
        ToolMessage(content="real tool result", tool_call_id="call"),
    ]
    return compose(
        system="trusted",
        settings=settings(),
        messages=messages,
        run_id="run",
        references=[HumanMessage(name="personalization_reference", content="fresh")],
        **overrides,
    )


def test_tail_preserves_the_entire_current_tool_round_and_history():
    plan = prompt_plan()
    assert [item.content for item in plan.messages[:4]] == [
        "trusted\nprompt_version=2026-11-07.memory-v3",
        "old user",
        "old answer",
        "fresh",
    ]
    assert [item.type for item in plan.messages[4:]] == ["human", "ai", "tool"]
    assert plan.messages[-1].tool_call_id == "call"
    assert plan.metadata["l2_turns"] == 1
    other = prompt_plan()
    other.messages[1].id = "another-checkpoint-id"
    assert stable_message(other.messages[1]) == stable_message(plan.messages[1])
    assert canonical({"b": 2, "a": "中"}) == '{"a":"中","b":2}'


def test_anthropic_wire_breakpoints_are_only_on_stable_layers_and_not_thinking():
    plan = prompt_plan()
    model = ChatAnthropic(model_name="claude-test", api_key="offline-test-key")
    adapted = plan.for_provider("anthropic", "auto")
    wire = model._get_request_payload(adapted)
    assert wire["system"][0]["cache_control"] == {"type": "ephemeral"}
    marked = [
        item
        for message in wire["messages"]
        for item in message["content"]
        if isinstance(item, dict) and "cache_control" in item
    ]
    assert len(marked) == 1 and marked[0]["text"] == "old answer"
    assert not any(
        "cache_control"
        in (item.content if isinstance(item.content, str) else canonical(item.content))
        for item in plan.messages
    )
    assert all(isinstance(item.content, str) for item in plan.for_provider("openai", "auto"))
    assert all(isinstance(item.content, str) for item in plan.for_provider("anthropic", "off"))


def test_provider_adaptation_preserves_thinking_signatures_and_deterministic_tool_instructions():
    from app.agent.prompts.tools import tool_instructions

    thought = {
        "type": "thinking",
        "thinking": "Provider supplied process",
        "signature": "opaque-signature",
    }
    history = [
        HumanMessage(content="old"),
        AIMessage(content=[thought, {"type": "text", "text": "answer"}]),
        HumanMessage(content="current", id="run:user"),
    ]
    plan = compose(
        system="trusted", settings=settings(), messages=history, run_id="run", references=[]
    )
    adapted = plan.for_provider("anthropic", "auto")
    assert adapted[2].content[0] == thought
    assert "cache_control" not in adapted[2].content[0]
    assert adapted[2].content[1]["cache_control"] == {"type": "ephemeral"}
    assert "cache_control" not in history[1].content[1]
    assert tool_instructions(["read_uploaded_file", "calculate"]) == tool_instructions(
        ["calculate", "read_uploaded_file"]
    )
    assert (
        plan.metadata
        == compose(
            system="trusted",
            settings=settings(),
            messages=deepcopy(history),
            run_id="run",
            references=[],
        ).metadata
    )


async def test_batch_summary_persists_across_new_inputs_and_only_rewrites_at_boundaries():
    record, config = [], AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(provider(record)),
        settings=config,
        prompt_settings=settings(),
        checkpointer=InMemorySaver(),
    )
    boundaries, summaries = [], []
    for index in range(100):
        state = create_initial_state(
            user_id="u",
            thread_id="t",
            run_id=f"r{index}",
            user_input=f"Question {index}",
            settings=config,
        )
        result = await graph.ainvoke(state, build_graph_config(state, config))
        summary = result.get("history_summary")
        if summary and (not summaries or summary["version"] != summaries[-1]["version"]):
            boundaries.append(index + 1)
            summaries.append(summary)
        assert sum(isinstance(item, HumanMessage) for item in result["messages"]) <= 24
    assert boundaries == list(range(25, 101, 8))
    assert summaries[0]["through_turn"] == 8
    assert summaries[-1]["through_turn"] == 80
    assert summaries[-1]["version"] == 10
    assert len(summaries[-1]["text"].encode()) <= 10000
    summary_at_26 = next(item for item in record[25] if item.name == "conversation_summary")
    summary_at_32 = next(item for item in record[31] if item.name == "conversation_summary")
    assert summary_at_26.content == summary_at_32.content
    assert record[24][-1].content == "Question 24"


async def test_optional_summarizer_falls_back_and_does_not_break_chat():
    async def broken(_messages, _previous):
        raise RuntimeError("offline summarizer failure")

    config, record = AgentSettings(_env_file=None), []
    graph = build_agent_graph(
        AgentServices(provider(record), history_summarizer=broken),
        settings=config,
        prompt_settings=settings(
            history_soft_limit=2, history_hard_limit=3, history_compression_batch=2
        ),
        checkpointer=InMemorySaver(),
    )
    for index in range(4):
        state = create_initial_state(
            user_id="u", thread_id="t", user_input=f"q{index}", settings=config
        )
        result = await graph.ainvoke(state, build_graph_config(state, config))
    assert result["response"] == "Actual response"
    assert result["history_summary"]["method"] == "extractive"
    assert "HISTORY_SUMMARY_FALLBACK" in result["warnings"]


async def memory(store, owner="u", scope="global", scope_ref=None):
    return await store.create_memory(
        owner,
        dict(
            title="Python",
            content="Python preference",
            type="preference",
            status="active",
            importance=0.7,
            scope=scope,
            scope_ref=scope_ref,
            source="test",
        ),
    )


async def test_retrieval_two_tiers_ttl_scope_mutation_and_other_connection_invalidation(tmp_path):
    path = tmp_path / "memory.sqlite"
    now = [1000.0]
    async with WorkspaceStore.open(path, 5) as store, WorkspaceStore.open(path, 5) as writer:
        first = await memory(store)
        project = await memory(store, scope="project", scope_ref="project-a")
        await memory(store, "other-user")
        cache = CachedMemoryReader(store, settings(), lambda: now[0])
        options = dict(user_id="u", query="Python", thread_id="t", project_id=None, limit=5)
        result = await cache.retrieve(**options)
        assert [item["id"] for item in result] == [first["id"]]
        result[0]["content"] = "caller mutation"
        assert (await cache.retrieve(**options))[0]["content"] == "Python preference"
        assert cache.hits == 1
        cache.entries.clear()
        assert (await cache.retrieve(**options))[0]["id"] == first["id"]
        assert cache.disk_hits == 1
        assert len(await cache.retrieve(**{**options, "project_id": "project-a"})) == 2
        await writer.connection.execute(
            "UPDATE workspace_memories SET status='archived' WHERE id=?", (first["id"],)
        )
        assert await cache.retrieve(**options) == []
        now[0] += 601
        misses = cache.misses
        assert await cache.retrieve(**options) == []
        assert cache.misses == misses + 1
        await writer.delete_memory("u", project["id"])
        assert await cache.retrieve(**{**options, "project_id": "project-a"}) == []
        async with store.connection.execute(
            "SELECT count(*) FROM retrieval_cache WHERE user_id='u'"
        ) as rows:
            assert (await rows.fetchone())[0] == 1


async def test_profile_snapshot_is_frozen_scoped_persisted_forked_and_revocable(tmp_path):
    path = tmp_path / "memory.sqlite"
    async with WorkspaceStore.open(path, 5) as store:
        item = await memory(store)
        profile = {
            "user_id": "u",
            "version": 1,
            "preferences": {"language": "Python"},
            "attributes": [
                {
                    "memory_id": item["id"],
                    "key": "language",
                    "value": "Python",
                    "dimension": "preference",
                    "scope": "global",
                    "scope_ref": None,
                },
                {
                    "memory_id": "private",
                    "key": "project",
                    "value": "secret",
                    "dimension": "goal",
                    "scope": "project",
                    "scope_ref": "project-b",
                },
            ],
        }
        snapshots = ProfileSnapshots(store)
        saved = await snapshots.read("u", "t", profile)
        assert len(saved["profile"]["attributes"]) == 1
        changed = {**profile, "version": 2, "preferences": {"language": "Go", "style": "concise"}}
        assert await snapshots.read("u", "t", changed) == saved
        await snapshots.copy("u", "t", "fork")
        assert await snapshots.read("u", "fork", changed) == saved
    async with WorkspaceStore.open(path, 5) as store:
        snapshots = ProfileSnapshots(store)
        assert await snapshots.read("u", "t", changed) == saved
        await store.delete_memory("u", item["id"])
        removed = await snapshots.read("u", "t", {**changed, "preferences": {}})
        assert "preferences" not in removed["profile"]
        assert removed["profile"]["attributes"] == []
        assert removed["revision"] > saved["revision"]
        await snapshots.reset("u", "t")
        refreshed = await snapshots.read("u", "t", changed)
        assert refreshed["version"] == 2


def test_api_learning_changes_only_the_tail_and_disable_clear_fork_remain_compatible(tmp_path):
    record = []
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "app.sqlite"),
        model_provider=provider(record),
    )
    base = "/api/v1"
    with TestClient(app) as client:
        assert (
            client.patch(
                base + "/profile",
                json={"expected_version": 0, "attributes": attributes({"language": "Python"})},
            ).status_code
            == 200
        )
        thread = client.post(base + "/threads", json={}).json()["thread_id"]
        first = client.post(base + "/chat", json={"thread_id": thread, "message": "First question"})
        assert first.status_code == 200, first.text
        assert any(
            item.name == "profile_snapshot" and "Python" in item.content for item in record[-1]
        )
        client.patch(
            base + "/profile",
            json={
                "expected_version": 1,
                "attributes": attributes({"language": "Go", "detail": "short"}),
            },
        )
        second = client.post(base + "/chat", json={"thread_id": thread, "message": "Next question"})
        assert second.status_code == 200, second.text
        assert not any(
            item.name == "profile_snapshot" and "Python" in item.content for item in record[-1]
        )
        names = [item.name for item in record[-1]]
        assert names.index("personalization_reference") >= 3
        assert "Go" in next(
            item.content for item in record[-1] if item.name == "personalization_reference"
        )
        assert record[-1][-1].content == "Next question"
        assert second.json()["metrics"]["l1_hash"] != first.json()["metrics"]["l1_hash"]
        fork_result = client.post(base + f"/threads/{thread}/fork", json={"title": "Branch"})
        assert fork_result.status_code == 201, fork_result.text
        fork = fork_result.json()["thread_id"]
        assert (
            client.post(
                base + "/chat", json={"thread_id": fork, "message": "Fork question"}
            ).status_code
            == 200
        )
        assert not any(
            item.name == "profile_snapshot" and "Python" in item.content for item in record[-1]
        )
        client.patch(base + "/settings", json={"personalization_enabled": False})
        assert (
            client.post(
                base + "/chat", json={"thread_id": thread, "message": "No memory"}
            ).status_code
            == 200
        )
        assert not any(
            item.name in {"profile_snapshot", "personalization_reference"} for item in record[-1]
        )
        runtime = client.get(base + "/runtime").json()
        assert runtime["cache_stats"]["model_calls"] == 4
        assert runtime["cache_stats"]["prefix_hit_ratio"] == 0.7
        assert client.delete(base + f"/threads/{thread}/messages").status_code == 204
        client.patch(base + "/settings", json={"personalization_enabled": True})
        assert (
            client.post(
                base + "/chat", json={"thread_id": thread, "message": "Fresh start"}
            ).status_code
            == 200
        )
        refreshed = next(item for item in record[-1] if item.name == "profile_snapshot")
        assert "Go" in refreshed.content and "Python" not in refreshed.content
        assert all(item.content != "First question" for item in record[-1])


def test_default_summary_uses_main_model_reports_its_usage_and_keeps_the_full_archive(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("HISTORY_SOFT_LIMIT", "2")
    monkeypatch.setenv("HISTORY_HARD_LIMIT", "3")
    monkeypatch.setenv("HISTORY_COMPRESSION_BATCH", "2")
    record = []
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "app.sqlite"),
        model_provider=provider(record),
    )
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={}).json()["thread_id"]
        for index in range(4):
            response = client.post(
                "/api/v1/chat", json={"thread_id": thread, "message": f"q{index}"}
            )
            assert response.status_code == 200, response.text
        assert len(record) == 5
        assert record[-2][0].content.startswith("Summarize conversation data")
        summary = next(item for item in record[-1] if item.name == "conversation_summary")
        assert json.loads(summary.content)["method"] == "model"
        assert response.json()["metrics"]["summary_tokens"] == 120
        assert response.json()["metrics"]["total_tokens"] == 240
        runtime = client.get("/api/v1/runtime").json()["cache_stats"]
        assert runtime["model_calls"] == 5 and runtime["summary_calls"] == 1
        assert len(client.get(f"/api/v1/threads/{thread}/messages").json()["items"]) == 4


async def test_lru_and_sqlite_limits_and_disabled_cache_are_transparent(tmp_path):
    async with WorkspaceStore.open(tmp_path / "memory.sqlite", 5) as store:
        await memory(store)
        cache = CachedMemoryReader(store, settings(retrieval_cache_max_entries=2))
        options = dict(user_id="u", query="Python", thread_id="t", project_id=None, limit=5)
        for limit in range(1, 6):
            assert len(await cache.retrieve(**{**options, "limit": limit})) == 1
        assert len(cache.entries) == 2
        async with store.connection.execute("SELECT count(*) FROM retrieval_cache") as rows:
            assert (await rows.fetchone())[0] == 2
        disabled = CachedMemoryReader(store, settings(retrieval_cache_ttl_seconds=0))
        assert await disabled.retrieve(**options) == await store.retrieve(**options)
        assert not disabled.entries
        await store.connection.execute("DROP TABLE retrieval_cache")
        cache.entries.clear()
        assert await cache.retrieve(**options) == await store.retrieve(**options)
        assert cache.failures == 1


def test_cache_metrics_use_upstream_counts_and_anthropic_specific_ttls_without_double_counting():
    usage = {
        "input_tokens": 100,
        "input_token_details": {
            "cache_read": 70,
            "cache_creation": 0,
            "ephemeral_5m_input_tokens": 20,
            "ephemeral_1h_input_tokens": 5,
        },
    }
    assert cache_write_tokens(usage) == 25
    telemetry = PromptTelemetry()
    telemetry.observe("u", "t", "r1", "m", {"l1_hash": "same", "history_segment_version": 0}, usage)
    telemetry.observe("u", "t", "r2", "m", {"l1_hash": "same", "history_segment_version": 0}, None)
    assert telemetry.stats()["prefix_stable_turns"] == 2
    assert telemetry.stats()["prefix_hit_ratio"] == 0.7
    result = run_metrics(
        {
            "model_stats": [
                {
                    "input_tokens": 100,
                    "output_tokens": 5,
                    "total_tokens": 105,
                    "cached_tokens": 70,
                    "seconds": 1,
                    "cache_write_tokens": 25,
                    "prompt_plan_id": "plan",
                }
            ]
        }
    )
    assert result.prefix_hit_ratio == 0.7 and result.cache_write_tokens == 25
    assert run_metrics({}) is None


@pytest.mark.parametrize("values", [{"history_soft_limit": 24}, {"history_compression_batch": 24}])
def test_invalid_history_configuration_is_rejected(values):
    with pytest.raises(ValueError):
        settings(**values)
