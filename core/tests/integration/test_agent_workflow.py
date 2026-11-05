"""实际在线图的离线验证。 / Offline execution tests for the real online graph."""

import asyncio
import json
from collections.abc import Sequence
from copy import deepcopy
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import (
    FakeListChatModel,
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from pydantic import Field

from app.agent.graph import (
    AgentServices,
    ContextBuildResult,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.config import AgentSettings, LLMSettings
from app.infrastructure.llm import OpenAICompatibleProvider


class RecordingModel(FakeMessagesListChatModel):
    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_names: list[str] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any) -> BaseChatModel:
        self.bound_names = [selected.name for selected in tools]
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        self.calls.append(deepcopy(messages))
        return super()._generate(messages, *args, **kwargs)


@dataclass
class FakeProvider:
    model: BaseChatModel

    def get_chat_model(self) -> BaseChatModel:
        return self.model


def sample_memory(identifier: str = "memory-1", **overrides: Any) -> dict:
    return {
        "id": identifier,
        "user_id": "user-1",
        "status": "active",
        "scope": "global",
        "scope_ref": None,
        "content": "用户通常偏好 Python",
        **overrides,
    }


def sample_ports():
    profile = SimpleNamespace(
        get_profile=AsyncMock(
            return_value={"user_id": "user-1", "version": 2, "preferences": {"language": "Python"}}
        )
    )
    memory = SimpleNamespace(retrieve=AsyncMock(return_value=[sample_memory()]))
    context = SimpleNamespace(
        build=AsyncMock(
            return_value=ContextBuildResult(
                text="历史偏好 Python，仅作参考。",
                memory_ids=("memory-1",),
                token_estimate=10,
            )
        )
    )
    return profile, memory, context


def make_state(settings: AgentSettings, **kwargs: Any):
    return create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="这次请使用 Go",
        settings=settings,
        **kwargs,
    )


async def test_plain_graph_runs_the_documented_nodes_without_memory_services() -> None:
    settings = AgentSettings(_env_file=None)
    model = RecordingModel(responses=[AIMessage(content="普通回答")])
    graph = build_agent_graph(AgentServices(FakeProvider(model)), settings=settings)
    updates = [
        update async for update in graph.astream(make_state(settings), stream_mode="updates")
    ]
    assert [next(iter(update)) for update in updates] == [
        "prepare_run",
        "load_profile",
        "retrieve_memories",
        "build_personalized_context",
        "agent",
        "finalize_response",
    ]
    assert updates[-1]["finalize_response"]["response"] == "普通回答"
    assert [message.type for message in model.calls[0]] == ["system", "human"]


async def test_injected_reads_are_scoped_and_context_is_separate_from_system_and_history() -> None:
    settings = AgentSettings(memory_top_k=4, memory_context_token_budget=100, _env_file=None)
    profile, memory, context = sample_ports()
    model = RecordingModel(responses=[AIMessage(content="使用 Go 的回答")])
    graph = build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    )
    state = make_state(settings, project_id="project-1")
    result = await graph.ainvoke(state, build_graph_config(state, settings))
    profile.get_profile.assert_awaited_once_with("user-1")
    memory.retrieve.assert_awaited_once_with(
        user_id="user-1",
        query="这次请使用 Go",
        thread_id="thread-1",
        project_id="project-1",
        limit=4,
    )
    assert context.build.await_args.kwargs["token_budget"] == 100
    assert result["profile_version"] == 2
    assert result["used_memory_ids"] == ["memory-1"]
    assert result["context_token_estimate"] == 10
    assert result["response"] == "使用 Go 的回答"
    prompt = model.calls[0]
    assert "本轮明确要求优先" in prompt[0].content
    assert "历史偏好 Python" not in prompt[0].content
    assert prompt[1].type == "human" and prompt[1].name == "personalization_reference"
    assert prompt[-1].content == "这次请使用 Go"
    assert all(message.name != "personalization_reference" for message in result["messages"])


@pytest.mark.parametrize("global_gate", [True, False])
async def test_disabled_personalization_never_calls_read_ports(global_gate: bool) -> None:
    settings = AgentSettings(personalization_enabled=global_gate, _env_file=None)
    profile, memory, context = sample_ports()
    model = RecordingModel(responses=[AIMessage(content="普通回答")])
    graph = build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    )
    state = make_state(settings, personalization_enabled=False)
    if not global_gate:
        state["personalization_enabled"] = True
    result = await graph.ainvoke(state)
    profile.get_profile.assert_not_awaited()
    memory.retrieve.assert_not_awaited()
    context.build.assert_not_awaited()
    assert result["personalization_enabled"] is False
    assert result["personalization_context"] == ""


@pytest.mark.parametrize(
    ("stage", "warning"),
    [
        ("profile", "PROFILE_READ_FAILED"),
        ("memory", "MEMORY_READ_FAILED"),
        ("context", "CONTEXT_BUILD_FAILED"),
    ],
)
async def test_read_failures_degrade_without_exposing_exception_details(stage: str, warning: str):
    settings = AgentSettings(_env_file=None)
    profile, memory, context = sample_ports()
    getattr(
        {"profile": profile, "memory": memory, "context": context}[stage],
        {"profile": "get_profile", "memory": "retrieve", "context": "build"}[stage],
    ).side_effect = RuntimeError("private-test-connection-detail")
    model = RecordingModel(responses=[AIMessage(content="仍可回答")])
    graph = build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    )
    result = await graph.ainvoke(make_state(settings))
    assert result["response"] == "仍可回答"
    assert warning in result["warnings"]
    assert "private-test-connection-detail" not in repr(model.calls)
    assert "private-test-connection-detail" not in repr(result)


@pytest.mark.parametrize(
    ("stage", "warning"),
    [
        ("profile", "PROFILE_READ_TIMEOUT"),
        ("memory", "MEMORY_READ_TIMEOUT"),
        ("context", "CONTEXT_BUILD_TIMEOUT"),
    ],
)
async def test_each_read_stage_has_a_timeout(stage: str, warning: str) -> None:
    settings = AgentSettings(context_read_timeout_seconds=0.01, _env_file=None)
    profile, memory, context = sample_ports()

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    getattr(
        {"profile": profile, "memory": memory, "context": context}[stage],
        {"profile": "get_profile", "memory": "retrieve", "context": "build"}[stage],
    ).side_effect = slow
    model = RecordingModel(responses=[AIMessage(content="降级回答")])
    result = await build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    ).ainvoke(make_state(settings))
    assert result["response"] == "降级回答"
    assert warning in result["warnings"]


async def test_candidate_guards_enforce_ownership_status_scope_dedup_and_top_k() -> None:
    settings = AgentSettings(memory_top_k=3, _env_file=None)
    profile, memory, context = sample_ports()
    memory.retrieve.return_value = [
        sample_memory("foreign", user_id="other-user"),
        sample_memory("old", status="superseded"),
        sample_memory("other-project", scope="project", scope_ref="other-project"),
        sample_memory("other-thread", scope="thread", scope_ref="other-thread"),
        sample_memory("memory-1"),
        sample_memory("memory-1"),
        sample_memory("project-memory", scope="project", scope_ref="project-1"),
        sample_memory("thread-memory", scope="thread", scope_ref="thread-1"),
        sample_memory("over-limit"),
    ]
    context.build.return_value = ContextBuildResult(
        text="相关历史",
        memory_ids=("memory-1", "project-memory", "thread-memory"),
        token_estimate=5,
    )
    model = RecordingModel(responses=[AIMessage(content="回答")])
    result = await build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    ).ainvoke(make_state(settings, project_id="project-1"))
    expected = ["memory-1", "project-memory", "thread-memory"]
    assert [item["id"] for item in result["relevant_memories"]] == expected
    assert result["used_memory_ids"] == expected
    assert "MEMORY_RESULTS_REJECTED" in result["warnings"]
    assert len(context.build.await_args.kwargs["relevant_memories"]) == 3


async def test_missing_project_and_foreign_profile_cannot_inject_unrelated_data() -> None:
    settings = AgentSettings(_env_file=None)
    profile, memory, context = sample_ports()
    profile.get_profile.return_value = {"user_id": "other-user", "version": 1}
    memory.retrieve.return_value = [sample_memory("project-memory", scope="project", scope_ref="p")]
    model = RecordingModel(responses=[AIMessage(content="普通回答")])
    result = await build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    ).ainvoke(make_state(settings))
    assert result["user_profile"] is None
    assert result["profile_version"] is None
    assert result["relevant_memories"] == []
    assert result["personalization_context"] == ""
    context.build.assert_not_awaited()


@pytest.mark.parametrize(
    "invalid_context",
    [
        ContextBuildResult(text="too much", token_estimate=2000),
        ContextBuildResult(text="unknown reference", memory_ids=("unknown",), token_estimate=1),
        ContextBuildResult(text="nonempty", token_estimate=0),
    ],
)
async def test_invalid_context_is_not_injected(invalid_context: ContextBuildResult) -> None:
    settings = AgentSettings(_env_file=None)
    profile, memory, context = sample_ports()
    context.build.return_value = invalid_context
    model = RecordingModel(responses=[AIMessage(content="普通回答")])
    result = await build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context), settings=settings
    ).ainvoke(make_state(settings))
    assert result["personalization_context"] == ""
    assert result["used_memory_ids"] == []
    assert "CONTEXT_BUILD_FAILED" in result["warnings"]
    assert all(message.name != "personalization_reference" for message in model.calls[0])


async def test_absent_context_builder_keeps_plain_answer_without_injecting_full_profile() -> None:
    settings = AgentSettings(_env_file=None)
    profile, memory, _ = sample_ports()
    model = RecordingModel(responses=[AIMessage(content="普通回答")])
    result = await build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory), settings=settings
    ).ainvoke(make_state(settings))
    assert result["personalization_context"] == ""
    assert "CONTEXT_BUILDER_NOT_CONFIGURED" in result["warnings"]
    assert len(model.calls[0]) == 2


async def test_model_failure_propagates_instead_of_becoming_a_successful_answer() -> None:
    settings = AgentSettings(_env_file=None)

    class UnavailableModel(RecordingModel):
        def _generate(self, *args: Any, **kwargs: Any):
            raise RuntimeError("model unavailable")

    model = UnavailableModel(responses=[AIMessage(content="unused")])
    graph = build_agent_graph(AgentServices(FakeProvider(model)), settings=settings)
    with pytest.raises(RuntimeError, match="model unavailable"):
        await graph.ainvoke(make_state(settings))


@pytest.mark.parametrize(
    "response",
    [
        AIMessage(content=""),
        AIMessage(content="", tool_calls=[{"id": "x", "name": "unregistered", "args": {}}]),
    ],
)
async def test_empty_or_unhandled_tool_responses_cannot_finalize(response: AIMessage) -> None:
    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(FakeProvider(RecordingModel(responses=[response]))), settings=settings
    )
    with pytest.raises(ValueError, match="final text answer"):
        await graph.ainvoke(make_state(settings))


async def test_registered_tools_complete_a_round_trip() -> None:
    settings = AgentSettings(_env_file=None)

    @tool
    def add_numbers(a: int, b: int) -> int:
        """计算两个整数之和。 / Add two integers."""
        return a + b

    model = RecordingModel(
        responses=[
            AIMessage(
                id="tool-request",
                content="",
                tool_calls=[{"id": "sum-call", "name": "add_numbers", "args": {"a": 1, "b": 2}}],
            ),
            AIMessage(id="final-answer", content="结果是 3"),
        ]
    )
    result = await build_agent_graph(
        AgentServices(FakeProvider(model)), settings=settings, tools=[add_numbers]
    ).ainvoke(make_state(settings))
    assert model.bound_names == ["add_numbers"]
    assert result["response"] == "结果是 3"
    tool_result = model.calls[1][-1]
    assert isinstance(tool_result, ToolMessage)
    assert tool_result.tool_call_id == "sum-call" and tool_result.content == "3"


async def test_tool_failures_are_sanitized_before_returning_to_model() -> None:
    settings = AgentSettings(_env_file=None)

    @tool
    def failing_tool() -> str:
        """测试失败工具。 / Test a failed tool."""
        raise RuntimeError("private-test-tool-detail")

    model = RecordingModel(
        responses=[
            AIMessage(
                id="tool-request",
                content="",
                tool_calls=[{"id": "fail-call", "name": "failing_tool", "args": {}}],
            ),
            AIMessage(id="answer", content="工具失败，已说明原因。"),
        ]
    )
    result = await build_agent_graph(
        AgentServices(FakeProvider(model)), settings=settings, tools=[failing_tool]
    ).ainvoke(make_state(settings))
    assert result["response"]
    tool_result = model.calls[1][-1]
    assert tool_result.status == "error"
    assert "private-test-tool-detail" not in tool_result.content


async def test_insufficient_budget_stops_before_executing_tools() -> None:
    settings = AgentSettings(max_steps=6, _env_file=None)
    effects = []

    @tool
    def record_effect() -> str:
        """记录一次调用。 / Record an invocation."""
        effects.append("called")
        return "done"

    model = RecordingModel(
        responses=[
            AIMessage(
                content="", tool_calls=[{"id": "effect-call", "name": "record_effect", "args": {}}]
            )
        ]
    )
    graph = build_agent_graph(
        AgentServices(FakeProvider(model)), settings=settings, tools=[record_effect]
    )
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke(make_state(settings))
    assert effects == []


async def test_default_graph_config_enforces_step_cap_without_manual_config() -> None:
    settings = AgentSettings(max_steps=4, _env_file=None)
    graph = build_agent_graph(
        AgentServices(FakeProvider(RecordingModel(responses=[AIMessage(content="回答")]))),
        settings=settings,
    )
    with pytest.raises(GraphRecursionError):
        await graph.ainvoke(make_state(settings))


async def test_streaming_messages_propagate_through_agent_node() -> None:
    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(FakeProvider(FakeListChatModel(responses=["你好"]))), settings=settings
    )
    chunks = [
        chunk
        async for chunk, metadata in graph.astream(make_state(settings), stream_mode="messages")
        if metadata.get("langgraph_node") == "agent"
    ]
    assert "".join(chunk.text for chunk in chunks) == "你好"


async def test_new_checkpointed_run_clears_previous_personalization_when_disabled() -> None:
    settings = AgentSettings(_env_file=None)
    profile, memory, context = sample_ports()
    model = RecordingModel(
        responses=[
            AIMessage(id="first", content="首次回答"),
            AIMessage(id="second", content="本次普通回答"),
        ]
    )
    graph = build_agent_graph(
        AgentServices(FakeProvider(model), profile, memory, context),
        settings=settings,
        checkpointer=InMemorySaver(),
    )
    first = make_state(settings, run_id="run-1")
    await graph.ainvoke(first, build_graph_config(first, settings))
    second = make_state(settings, run_id="run-2", personalization_enabled=False)
    result = await graph.ainvoke(second, build_graph_config(second, settings))
    assert profile.get_profile.await_count == 1
    assert result["user_profile"] is None
    assert result["profile_version"] is None
    assert result["personalization_context"] == ""
    assert result["used_memory_ids"] == []
    assert all(message.name != "personalization_reference" for message in model.calls[1])


async def test_complete_graph_uses_real_provider_serialization_with_mock_http() -> None:
    requests = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "id": "chatcmpl-graph",
                "object": "chat.completion",
                "created": 1,
                "model": "test-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "完整链路回答"},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    llm_settings = LLMSettings(
        provider="deepseek", model="test-model", api_key="test-key", max_tokens=128, _env_file=None
    )
    settings = AgentSettings(_env_file=None)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        provider = OpenAICompatibleProvider(llm_settings, http_async_client=client)
        result = await build_agent_graph(AgentServices(provider), settings=settings).ainvoke(
            make_state(settings)
        )
    assert result["response"] == "完整链路回答"
    assert requests[0].url.path == "/chat/completions"
    body = json.loads(requests[0].content)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 128
    assert body["messages"][-1]["content"] == "这次请使用 Go"
    assert "max_steps" not in body


async def test_shared_compiled_graph_keeps_concurrent_run_snapshots_separate() -> None:
    async def read_profile(user_id: str):
        await asyncio.sleep(0)
        return {"user_id": user_id, "version": 1}

    async def build_context(**kwargs):
        return ContextBuildResult(text=kwargs["user_profile"]["user_id"], token_estimate=1)

    settings = AgentSettings(_env_file=None)
    graph = build_agent_graph(
        AgentServices(
            FakeProvider(FakeListChatModel(responses=["回答"])),
            profile_reader=SimpleNamespace(get_profile=read_profile),
            context_builder=SimpleNamespace(build=build_context),
        ),
        settings=settings,
    )
    states = [
        create_initial_state(
            user_id=f"user-{index}",
            thread_id=f"thread-{index}",
            user_input="test",
            settings=settings,
        )
        for index in (1, 2)
    ]
    results = await asyncio.gather(*(graph.ainvoke(state) for state in states))
    for index, result in enumerate(results, start=1):
        assert result["user_profile"]["user_id"] == f"user-{index}"
        assert result["personalization_context"] == f"user-{index}"
