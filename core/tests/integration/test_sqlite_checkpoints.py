"""真实 SQLite 快照与恢复测试。 / Real SQLite persistence and recovery tests."""

import asyncio
from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from pydantic import Field

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.config import AgentSettings, CheckpointSettings
from app.infrastructure.checkpoints import SQLiteCheckpointStore
from app.infrastructure.conversations import SQLiteConversationStore
from app.services import AgentRunner


class ScenarioModel(FakeMessagesListChatModel):
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    fail_once: bool = False

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.calls.append(deepcopy(messages))
        if self.fail_once:
            self.fail_once = False
            raise RuntimeError("temporary model failure")
        return super()._generate(messages, *args, **kwargs)


@dataclass
class TestProvider:
    __test__ = False
    model: BaseChatModel

    def get_chat_model(self) -> BaseChatModel:
        return self.model


async def test_conversation_archive_is_scoped_and_survives_working_history_pruning(
    tmp_path: Path,
) -> None:
    database = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    settings = AgentSettings(history_max_turns=1, _env_file=None)
    model = ScenarioModel(responses=[AIMessage(content="answer")])
    async with (
        SQLiteCheckpointStore(database) as saver,
        SQLiteConversationStore.open(database) as archive,
    ):
        graph = build_agent_graph(
            AgentServices(TestProvider(model)), settings=settings, checkpointer=saver
        )
        runner = AgentRunner(graph, settings, conversation_writer=archive)
        for user, thread, run, question in (
            ("alice", "first", "one", "old question"),
            ("alice", "other", "one", "other conversation"),
            ("bob", "first", "one", "other user"),
            ("alice", "first", "two", "recent question"),
        ):
            await runner.ainvoke(user_id=user, thread_id=thread, run_id=run, message=question)
        for call, question in zip(
            model.calls,
            ("old question", "other conversation", "other user", "recent question"),
            strict=True,
        ):
            assert [m.content for m in call if isinstance(m, HumanMessage)] == [question]
        with pytest.raises(ValueError, match="already completed"):
            await runner.ainvoke(user_id="alice", thread_id="first", run_id="one", message="retry")
        async with saver.conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='conversation_turns'"
        ) as cursor:
            assert await cursor.fetchone() is None
    async with SQLiteConversationStore.open(database) as archive:
        page = await archive.list_turns(user_id="alice", thread_id="first", limit=1)
        assert [turn.user_input for _, turn in page] == ["old question"]
        next_page = await archive.list_turns(
            user_id="alice", thread_id="first", after_id=page[0][0]
        )
        assert [turn.user_input for _, turn in next_page] == ["recent question"]
        for user, thread, expected in (
            ("alice", "other", "other conversation"),
            ("bob", "first", "other user"),
        ):
            turns = await archive.list_turns(user_id=user, thread_id=thread)
            assert [turn.user_input for _, turn in turns] == [expected]
        assert await archive.list_turns(user_id="bob", thread_id="other") == []


async def test_archive_repairs_interrupted_write_and_only_records_completed_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    settings = AgentSettings(_env_file=None)
    model = ScenarioModel(
        responses=[
            AIMessage(id="first-answer", content="answer"),
            AIMessage(id="second-answer", content="answer"),
        ],
        fail_once=True,
    )
    async with (
        SQLiteCheckpointStore(database) as saver,
        SQLiteConversationStore.open(database) as archive,
    ):
        graph = build_agent_graph(
            AgentServices(TestProvider(model)), settings=settings, checkpointer=saver
        )
        runner = AgentRunner(graph, settings, conversation_writer=archive)
        scope = {"user_id": "user", "thread_id": "thread"}
        with pytest.raises(RuntimeError, match="temporary model failure"):
            await runner.ainvoke(**scope, message="first", run_id="one")
        assert await archive.list_turns(**scope) == []
        with monkeypatch.context() as patch:
            patch.setattr(
                archive, "save_turn", AsyncMock(side_effect=OSError("archive unavailable"))
            )
            with pytest.raises(OSError, match="archive unavailable"):
                await runner.ainvoke(**scope)
        assert await archive.list_turns(**scope) == []
        await runner.ainvoke(**scope, message="second", run_id="two")
        turns = await archive.list_turns(**scope)
        assert [(turn.run_id, turn.user_input, turn.response) for _, turn in turns] == [
            ("one", "first", "answer"),
            ("two", "second", "answer"),
        ]
        assert len(model.calls) == 3


async def test_sqlite_lifecycle_pragmas_and_independent_tables(tmp_path: Path) -> None:
    settings = CheckpointSettings(
        sqlite_path=tmp_path / "nested" / "checkpoints.sqlite", _env_file=None
    )
    resource = SQLiteCheckpointStore(settings)
    async with resource as saver:
        assert saver.conn is resource.connection
        for pragma, expected in (
            ("journal_mode", "wal"),
            ("synchronous", 2),
            ("busy_timeout", 5000),
        ):
            async with saver.conn.execute(f"PRAGMA {pragma}") as cursor:
                assert (await cursor.fetchone())[0] == expected
        async with saver.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ) as cursor:
            tables = {row[0] for row in await cursor.fetchall()}
        assert {"checkpoints", "writes"}.issubset(tables)
        assert not {"messages", "memory_items", "user_profiles"}.intersection(tables)
    assert settings.sqlite_path.is_file()
    assert resource.connection is None and resource.saver is None
    with pytest.raises(ValueError):
        await saver.aget_tuple({"configurable": {"thread_id": "closed"}})


async def test_reopen_database_restores_conversation_and_snapshot_history(tmp_path: Path) -> None:
    database = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(database) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(
                    ScenarioModel(responses=[AIMessage(id="answer-1", content="第一轮回答")])
                )
            ),
            settings=settings,
            checkpointer=saver,
        )
        first = await AgentRunner(graph, settings).ainvoke(
            user_id="user-1", thread_id="thread-1", message="第一轮问题", run_id="run-1"
        )
        assert first["response"] == "第一轮回答"
    model = ScenarioModel(responses=[AIMessage(id="answer-2", content="第二轮回答")])
    async with SQLiteCheckpointStore(database) as saver:
        graph = build_agent_graph(
            AgentServices(TestProvider(model)), settings=settings, checkpointer=saver
        )
        result = await AgentRunner(graph, settings).ainvoke(
            user_id="user-1", thread_id="thread-1", message="第二轮问题", run_id="run-2"
        )
        assert [message.content for message in result["messages"]] == [
            "第一轮问题",
            "第一轮回答",
            "第二轮问题",
            "第二轮回答",
        ]
        assert "第一轮回答" in [message.content for message in model.calls[0]]
        config = build_graph_config(result, settings)
        snapshot = await graph.aget_state(config)
        assert snapshot.values["response"] == "第二轮回答"
        assert snapshot.next == ()
        assert "remaining_steps" not in snapshot.values
        history = [entry async for entry in graph.aget_state_history(config)]
        assert len(history) > 2


async def test_same_business_thread_is_isolated_by_user(tmp_path: Path) -> None:
    database = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(database) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(FakeListChatModel(responses=["first answer", "second answer"]))
            ),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        await runner.ainvoke(user_id="first", thread_id="shared", message="first private question")
        await runner.ainvoke(
            user_id="second", thread_id="shared", message="second private question"
        )
        for user, expected in (
            ("first", "first private question"),
            ("second", "second private question"),
        ):
            config = build_graph_config({"user_id": user, "thread_id": "shared"}, settings)
            snapshot = await graph.aget_state(config)
            assert snapshot.values["user_id"] == user
            assert snapshot.values["messages"][0].content == expected
            assert len(snapshot.values["messages"]) == 2
        async with saver.conn.execute("SELECT DISTINCT thread_id FROM checkpoints") as cursor:
            assert len(await cursor.fetchall()) == 2


async def test_failure_reopens_and_resumes_only_the_pending_node(tmp_path: Path) -> None:
    database = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    settings = AgentSettings(_env_file=None)
    profile = SimpleNamespace(get_profile=AsyncMock(return_value=None))
    async with SQLiteCheckpointStore(database) as saver:
        model = ScenarioModel(responses=[AIMessage(content="unused")], fail_once=True)
        graph = build_agent_graph(
            AgentServices(TestProvider(model), profile_reader=profile),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        with pytest.raises(RuntimeError, match="temporary model failure"):
            await runner.ainvoke(
                user_id="user-1", thread_id="thread-1", message="原始问题", run_id="original-run"
            )
        config = build_graph_config({"user_id": "user-1", "thread_id": "thread-1"}, settings)
        assert (await graph.aget_state(config)).next == ("agent",)
        with pytest.raises(RuntimeError, match="unfinished run"):
            await runner.ainvoke(user_id="user-1", thread_id="thread-1", message="不要混入新问题")
    async with SQLiteCheckpointStore(database) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(ScenarioModel(responses=[AIMessage(content="恢复后回答")])),
                profile_reader=profile,
            ),
            settings=settings,
            checkpointer=saver,
        )
        result = await AgentRunner(graph, settings).ainvoke(user_id="user-1", thread_id="thread-1")
        assert result["response"] == "恢复后回答"
        assert result["run_id"] == "original-run"
        assert result["user_input"] == "原始问题"
        assert len(result["messages"]) == 2
        assert profile.get_profile.await_count == 1


async def test_resume_requires_pending_run_and_duplicate_completed_id_is_rejected(
    tmp_path: Path,
) -> None:
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(TestProvider(FakeListChatModel(responses=["done"]))),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        with pytest.raises(ValueError, match="No unfinished run"):
            await runner.ainvoke(user_id="u", thread_id="t")
        await runner.ainvoke(user_id="u", thread_id="t", message="question", run_id="run-1")
        await runner.ainvoke(user_id="u", thread_id="t", message="next question", run_id="run-2")
        completed = await runner.ainvoke(user_id="u", thread_id="t")
        assert completed["run_id"] == "run-2" and completed["response"] == "done"
        with pytest.raises(ValueError, match="already completed"):
            await runner.ainvoke(user_id="u", thread_id="t", message="question", run_id=" run-1 ")
        snapshot = await graph.aget_state(
            build_graph_config({"user_id": "u", "thread_id": "t"}, settings)
        )
        assert snapshot.next == ()
        with pytest.raises(ValueError, match="preserves saved inputs"):
            await runner.ainvoke(user_id="u", thread_id="t", personalization_enabled=False)


@pytest.mark.parametrize(
    "invalid_response",
    [
        AIMessage(content=""),
        AIMessage(
            content="", tool_calls=[{"id": "unsupported", "name": "unregistered", "args": {}}]
        ),
    ],
)
async def test_invalid_model_output_can_resume_at_model_node(
    tmp_path: Path,
    invalid_response: AIMessage,
) -> None:
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(
                    ScenarioModel(
                        responses=[invalid_response, AIMessage(content="recovered answer")]
                    )
                )
            ),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        with pytest.raises(ValueError, match="final text answer"):
            await runner.ainvoke(user_id="u", thread_id="t", message="question")
        snapshot = await graph.aget_state(
            build_graph_config({"user_id": "u", "thread_id": "t"}, settings)
        )
        assert snapshot.next == ("agent",)
        assert len(snapshot.values["messages"]) == 1
        result = await runner.ainvoke(user_id="u", thread_id="t")
        assert result["response"] == "recovered answer"


async def test_history_limit_prunes_complete_turns_but_keeps_old_snapshots(tmp_path: Path) -> None:
    settings = AgentSettings(history_max_turns=2, _env_file=None)
    model = ScenarioModel(
        responses=[AIMessage(id=f"a-{i}", content=f"answer-{i}") for i in (1, 2, 3)]
    )
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(TestProvider(model)), settings=settings, checkpointer=saver
        )
        runner = AgentRunner(graph, settings)
        for index in (1, 2, 3):
            result = await runner.ainvoke(user_id="u", thread_id="t", message=f"question-{index}")
        assert [message.content for message in result["messages"]] == [
            "question-2",
            "answer-2",
            "question-3",
            "answer-3",
        ]
        assert "question-1" not in [message.content for message in model.calls[-1]]
        history = [
            entry async for entry in graph.aget_state_history(build_graph_config(result, settings))
        ]
        assert any(
            message.content == "question-1"
            for entry in history
            for message in entry.values.get("messages", [])
        )


async def test_pruning_never_splits_a_completed_tool_round(tmp_path: Path) -> None:
    settings = AgentSettings(history_max_turns=2, _env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(
                    ScenarioModel(responses=[AIMessage(id="new-answer", content="new answer")])
                )
            ),
            settings=settings,
            checkpointer=saver,
        )
        saved = create_initial_state(
            user_id="u", thread_id="t", user_input="previous", settings=settings
        )
        saved["messages"] = [
            HumanMessage(id="old-user", content="old question"),
            AIMessage(id="old-answer", content="old answer"),
            HumanMessage(id="previous-user", content="previous question"),
            AIMessage(
                id="tool-request",
                content="",
                tool_calls=[{"id": "call-1", "name": "test", "args": {}}],
            ),
            ToolMessage(id="tool-result", tool_call_id="call-1", content="tool data"),
            AIMessage(id="previous-answer", content="previous answer"),
        ]
        await graph.aupdate_state(
            build_graph_config(saved, settings), saved, as_node="finalize_response"
        )
        result = await AgentRunner(graph, settings).ainvoke(
            user_id="u", thread_id="t", message="new question"
        )
        assert [message.type for message in result["messages"]] == [
            "human",
            "ai",
            "tool",
            "ai",
            "human",
            "ai",
        ]
        assert result["messages"][2].tool_call_id == "call-1"


async def test_one_runner_serializes_same_thread_requests(tmp_path: Path) -> None:
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        model = FakeListChatModel(responses=["answer-1", "answer-2"])
        graph = build_agent_graph(
            AgentServices(TestProvider(model)), settings=settings, checkpointer=saver
        )
        runner = AgentRunner(graph, settings)
        await asyncio.gather(
            runner.ainvoke(user_id="u", thread_id="t", message="question-1"),
            runner.ainvoke(user_id="u", thread_id="t", message="question-2"),
        )
        snapshot = await graph.aget_state(
            build_graph_config({"user_id": "u", "thread_id": "t"}, settings)
        )
        assert [message.content for message in snapshot.values["messages"]] == [
            "question-1",
            "answer-1",
            "question-2",
            "answer-2",
        ]


async def test_independent_threads_can_run_concurrently(tmp_path: Path) -> None:
    active, peak = 0, 0

    async def read_profile(user_id: str):
        nonlocal active, peak
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.05)
        active -= 1
        return None

    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(
                TestProvider(FakeListChatModel(responses=["answer"])),
                profile_reader=SimpleNamespace(get_profile=read_profile),
            ),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        await asyncio.gather(
            runner.ainvoke(user_id="u", thread_id="t1", message="question-1"),
            runner.ainvoke(user_id="u", thread_id="t2", message="question-2"),
        )
        assert peak == 2


async def test_streaming_commits_completed_snapshot(tmp_path: Path) -> None:
    settings = AgentSettings(_env_file=None)
    async with SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    ) as saver:
        graph = build_agent_graph(
            AgentServices(TestProvider(FakeListChatModel(responses=["你好"]))),
            settings=settings,
            checkpointer=saver,
        )
        runner = AgentRunner(graph, settings)
        chunks = [
            chunk.text
            async for chunk, metadata in runner.astream(
                user_id="u", thread_id="t", message="question", stream_mode="messages"
            )
            if metadata.get("langgraph_node") == "agent"
        ]
        assert "".join(chunks) == "你好"
        snapshot = await graph.aget_state(
            build_graph_config({"user_id": "u", "thread_id": "t"}, settings)
        )
        assert snapshot.values["response"] == "你好" and snapshot.next == ()


async def test_failed_startup_and_body_error_close_resources(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    resource = SQLiteCheckpointStore(
        CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite", _env_file=None)
    )
    with monkeypatch.context() as patch:
        patch.setattr(
            AsyncSqliteSaver, "setup", AsyncMock(side_effect=RuntimeError("setup failed"))
        )
        with pytest.raises(RuntimeError, match="setup failed"):
            async with resource:
                pass
    assert resource.connection is None
    with pytest.raises(RuntimeError, match="body failed"):
        async with resource:
            raise RuntimeError("body failed")
    assert resource.connection is None
