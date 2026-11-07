import json
from functools import partial
from types import SimpleNamespace

import pytest
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage
from langgraph.graph import START, StateGraph
from langgraph.types import Command

from app.agent.graph import (
    AgentServices,
    AgentState,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.agent.graph.review import pending_review, review_tools
from app.agent.tools import ToolRegistry, create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from app.infrastructure.checkpoints import SQLiteCheckpointStore
from app.tools.artifacts import ArtifactStore
from app.tools.context import ToolExecutionContext
from app.tools.contracts import ToolOperationError, ToolResult, artifact_for
from tests.support.tool_calls import call, execute_calls


async def test_completed_call_reuses_validated_artifacts_and_uncertain_call_never_reexecutes(
    tmp_path,
):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    store = ArtifactStore(settings)
    context = ToolExecutionContext("a" * 64, "u", "t", "r")
    calls = []

    async def invoke():
        calls.append(1)
        path = tmp_path / "report.txt"
        path.write_text("saved", encoding="utf-8")
        return ToolResult(
            ok=True, summary="Saved.", artifacts=[artifact_for(path, tmp_path, context.key)]
        )

    first, reused, _ = await store.execute(context, "export_document", invoke)
    assert first.ok and not reused
    second, reused, _ = await ArtifactStore(settings).execute(context, "export_document", invoke)
    assert second == first and reused and len(calls) == 1
    (tmp_path / "report.txt").write_text("changed", encoding="utf-8")
    with pytest.raises(ToolOperationError) as error:
        await store.execute(context, "export_document", invoke)
    assert error.value.result.error.code == "RECOVERY_REQUIRED" and len(calls) == 1

    uncertain = ToolExecutionContext("b" * 64, "u", "t", "r")
    path = store.directory(uncertain.key) / "manifest.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"status": "running"}))
    with pytest.raises(ToolOperationError):
        await store.execute(uncertain, "export_document", invoke)
    assert len(calls) == 1


async def test_gateway_replay_restores_same_file_manifest_and_per_run_keys(tmp_path):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path)
    registry = ToolRegistry.from_tools(create_builtin_tools(settings))
    command = call(
        "export_document", {"format": "txt", "filename": "report.txt", "title": "Report"}
    )
    first, _ = await execute_calls(registry, [command])
    second, state = await execute_calls(registry, [command])
    assert (
        first[0] == second[0]
        and state["messages"][-1].additional_kwargs["tool_metric"]["reused"] == 1
    )
    assert len(list((tmp_path / "runs").glob("*/manifest.json"))) == 1
    changed = create_initial_state(user_id="u", thread_id="t", user_input="test", run_id="other")
    third, _ = await execute_calls(registry, [command], state=changed)
    assert third[0].artifacts[0].rel_path != first[0].artifacts[0].rel_path


async def test_journal_retention_only_removes_owned_completed_runs(tmp_path):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path, max_artifact_runs=1)
    store = ArtifactStore(settings)
    legacy = store.root / "legacy-user-output"
    legacy.mkdir(parents=True)
    (legacy / "keep.txt").write_text("keep")
    for key in ("a" * 64, "b" * 64):

        async def invoke():
            return ToolResult(ok=True, summary="Done.")

        await store.execute(ToolExecutionContext(key, "u", "t", key), "export_document", invoke)
    assert not (store.root / ("a" * 64)).exists()
    assert (store.root / ("b" * 64)).is_dir()
    assert (legacy / "keep.txt").read_text() == "keep"


@pytest.mark.parametrize(
    "name,args,target",
    [
        ("run_command", {"command": "mkdir", "arguments": ["legacy-folder"]}, "legacy-folder"),
        (
            "write_document_python",
            {"code": "from pathlib import Path\nPath('legacy.txt').write_text('saved')"},
            None,
        ),
    ],
)
async def test_pre_upgrade_approval_survives_sqlite_restart_and_uses_hidden_alias(
    tmp_path, name, args, target
):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path / "files")
    registry = ToolRegistry.from_tools(create_builtin_tools(settings))
    checkpoint = CheckpointSettings(_env_file=None, sqlite_path=tmp_path / "checkpoint.sqlite")
    config = build_graph_config({"user_id": "u", "thread_id": "t"})
    old = StateGraph(AgentState)
    old.add_node(
        "agent", lambda state: {"messages": [AIMessage(content="", tool_calls=[call(name, args)])]}
    )
    old.add_node("review_tools", partial(review_tools, registry=registry))
    old.add_node("tools", lambda state: {})
    old.add_edge(START, "agent")
    old.add_edge("agent", "review_tools")
    state = create_initial_state(
        user_id="u", thread_id="t", user_input="legacy request", run_id="legacy"
    )
    state["tool_contract_version"] = 1
    async with SQLiteCheckpointStore(checkpoint) as saver:
        graph = old.compile(checkpointer=saver)
        await graph.ainvoke(state, config, durability="sync")
        review = pending_review(await graph.aget_state(config))
        assert review is not None

    class Model(FakeMessagesListChatModel):
        def bind_tools(self, tools, **kwargs):
            assert all(tool.name not in {"run_command", "write_document_python"} for tool in tools)
            return self

    async with SQLiteCheckpointStore(checkpoint) as saver:
        model = Model(responses=[AIMessage(content="Recovered.")])
        graph = build_agent_graph(
            AgentServices(SimpleNamespace(get_chat_model=lambda: model)),
            tools=registry.for_mode("work"),
            tool_registry=registry,
            mode="work",
            checkpointer=saver,
            require_tool_review=True,
        )
        recovered = await graph.ainvoke(
            Command(resume={review["approval_id"]: True}), config, durability="sync"
        )
    assert recovered["response"] == "Recovered."
    output = next(item for item in recovered["messages"] if item.type == "tool")
    result = ToolResult.model_validate_json(output.content)
    assert result.ok, result
    if target:
        assert (settings.workspace_path / target).is_dir()
    else:
        assert (settings.workspace_path / result.artifacts[0].rel_path).read_text() == "saved"
