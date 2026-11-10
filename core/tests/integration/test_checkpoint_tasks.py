"""Native task results survive interruption inside a tool batch.
/ 原生 task 结果在工具批次内部中断后仍可恢复。"""

import asyncio
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.graph import START

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.agent.graph.review import pending_review
from app.agent.tools import ToolRegistry
from app.config import CheckpointSettings, ToolSettings
from app.infrastructure.checkpoints import SQLiteCheckpointStore
from app.services import AgentRunner
from app.services.recovery import checkpoint_state


class Model(FakeMessagesListChatModel):
    def bind_tools(self, _tools):
        return self


async def test_resume_mid_batch_restores_successful_tool_task_after_sqlite_restart(tmp_path):
    calls = {"first": 0, "second": 0}
    entered = asyncio.Event()

    async def first(text: str):
        calls["first"] += 1
        return "first-result"

    async def second(text: str):
        calls["second"] += 1
        if calls["second"] == 1:
            entered.set()
            await asyncio.Event().wait()
        return "second-result"

    tools = [
        StructuredTool.from_function(coroutine=fn, name=name, description=name)
        for name, fn in [("first", first), ("second", second)]
    ]
    registry = ToolRegistry.from_tools(
        tools, settings=ToolSettings(_env_file=None, workspace_path=tmp_path)
    )
    database = CheckpointSettings(_env_file=None, sqlite_path=tmp_path / "tasks.sqlite")
    scope = {"user_id": "u", "thread_id": "t"}
    config = build_graph_config(scope)
    proposal = AIMessage(
        content="",
        tool_calls=[{"name": name, "args": {"text": "original"}, "id": name} for name in calls],
    )

    def compile_graph(saver, responses):
        return build_agent_graph(
            AgentServices(SimpleNamespace(get_chat_model=lambda: Model(responses=responses))),
            tools=tools,
            tool_registry=registry,
            mode="work",
            require_tool_review=True,
            checkpointer=saver,
        )

    async with SQLiteCheckpointStore(database) as saver:
        graph = compile_graph(saver, [proposal])
        await graph.ainvoke(
            create_initial_state(**scope, user_input="original", run_id="original-run"),
            config,
            durability="sync",
        )
        review = pending_review(await graph.aget_state(config))
        runner = AgentRunner(graph)

        async def run():
            return [
                event
                async for event in runner.astream(**scope, decision=(review["approval_id"], True))
            ]

        running = asyncio.create_task(run())
        try:
            await asyncio.wait_for(entered.wait(), 5)
        finally:
            running.cancel()
            with pytest.raises(asyncio.CancelledError):
                await running
        assert calls == {"first": 1, "second": 1}
        assert (await graph.aget_state(config)).next == ("tools",)

    async with SQLiteCheckpointStore(database) as saver:
        graph = compile_graph(saver, [AIMessage(content="Completed")])
        result = await AgentRunner(graph).ainvoke(**scope)
        assert result["response"] == "Completed" and result["run_id"] == "original-run"
        assert calls == {"first": 1, "second": 2}
        assert len([m for m in result["messages"] if isinstance(m, HumanMessage)]) == 1
        outputs = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        assert [m.tool_call_id for m in outputs] == ["first", "second"]
        assert [m.additional_kwargs["tool_metric"]["calls"] for m in outputs] == [1, 1]
        assert pending_review(await graph.aget_state(config)) is None


@pytest.mark.parametrize("previous_run", [False, True])
async def test_first_input_checkpoint_can_resume_without_recreating_request(tmp_path, previous_run):
    database = CheckpointSettings(_env_file=None, sqlite_path=tmp_path / "input.sqlite")
    scope = {"user_id": "u", "thread_id": "t"}
    config = build_graph_config(scope)
    async with SQLiteCheckpointStore(database) as saver:
        graph = build_agent_graph(
            AgentServices(
                SimpleNamespace(
                    get_chat_model=lambda: Model(
                        responses=[AIMessage(content="Old"), AIMessage(content="Done")]
                    )
                )
            ),
            checkpointer=saver,
        )
        runner = AgentRunner(graph)
        if previous_run:
            await runner.ainvoke(**scope, message="old question", run_id="old-run")
        seed = create_initial_state(
            **scope, user_input="new question", run_id="new-run", personalization_enabled=False
        )
        seed["model_selection"] = {"id": "saved-model", "effort": "high"}
        stream = graph.astream(seed, config, stream_mode="checkpoints", durability="sync")
        await anext(stream)
        await stream.aclose()
        snapshot, values = await checkpoint_state(graph, config)
        assert START in snapshot.next
        assert values["run_id"] == "new-run" and values["user_input"] == "new question"
        assert values["model_selection"]["id"] == "saved-model"
        result = await runner.ainvoke(**scope)
        assert result["run_id"] == "new-run" and result["user_input"] == "new question"
