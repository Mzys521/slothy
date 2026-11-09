"""Operational context across checkpoints. / 图与检查点边界的运行上下文验收。"""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage
from langchain_core.tools import tool
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.types import Command
from pydantic import Field

from app.agent.graph import (
    AgentServices,
    build_agent_graph,
    build_graph_config,
    create_initial_state,
)
from app.agent.prompting.plan import stable_message
from app.config import AgentSettings, CheckpointSettings, PromptSettings
from tests.support.legacy_app import create_app


class Recorder(FakeMessagesListChatModel):
    calls: list[list[AnyMessage]] = Field(default_factory=list)

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, *args, **kwargs):
        self.calls.append(deepcopy(messages))
        result = super()._generate(messages, *args, **kwargs)
        for generation in result.generations:
            generation.message = generation.message.model_copy(deep=True, update={"id": None})
        return result


@pytest.mark.parametrize("review,revoke", [(False, False), (True, True)])
async def test_tool_round_keeps_prefix_but_approval_revocation_removes_private_body(review, revoke):
    content = {"text": "ORIGINAL_PRIVATE_CONTEXT", "visible": True}

    @tool
    def refresh_document() -> str:
        """Observe a changed fixture document. / 读取变化后的测试资料。"""
        content["text"] = "UPDATED_CONTEXT"
        return "refreshed"

    async def read(*_args):
        return [{"reference_id": "owned-file", "included": content["visible"]}], (
            content["text"] if content["visible"] else "Reference unavailable"
        )

    model = Recorder(
        responses=[
            AIMessage(
                "",
                tool_calls=[
                    {
                        "id": "call",
                        "name": "refresh_document",
                        "args": {},
                    }
                ],
            ),
            AIMessage("done"),
        ]
    )
    settings = AgentSettings(_env_file=None, personalization_enabled=False)
    graph = build_agent_graph(
        AgentServices(SimpleNamespace(get_chat_model=lambda: model), reference_reader=read),
        settings=settings,
        tools=[refresh_document],
        checkpointer=InMemorySaver(),
        require_tool_review=review,
    )
    state = create_initial_state(
        user_id="alice",
        thread_id="t",
        run_id="r",
        user_input="inspect",
        references=[{"kind": "file", "path": "document.txt"}],
        settings=settings,
    )
    config = build_graph_config(state, settings)
    result = await graph.ainvoke(state, config)
    if review:
        assert result.get("__interrupt__")
        content["visible"] = not revoke
        result = await graph.ainvoke(Command(resume={"approved": True}), config)
    assert result["response"] == "done"
    assert sum(isinstance(m, HumanMessage) for m in result["messages"]) == 1
    first, second = model.calls
    if revoke:
        assert not any("ORIGINAL_PRIVATE_CONTEXT" in m.text for m in second)
        assert result["used_reference_ids"] == []
    else:
        assert [stable_message(m) for m in second[: len(first)]] == [
            stable_message(m) for m in first
        ]
        assert "UPDATED_CONTEXT" in second[-1].text
        assert len(result["runtime_context_events"]) == 2


async def test_automatic_compaction_preserves_real_turn_count_and_reprojects_context():
    model = Recorder(responses=[AIMessage("ok")])
    settings = AgentSettings(_env_file=None, personalization_enabled=False)
    graph = build_agent_graph(
        AgentServices(
            SimpleNamespace(get_chat_model=lambda: model),
            skill_reader=lambda: [
                {"id": "guide", "instructions": "FIXED_SKILL_GUIDANCE"},
            ],
        ),
        settings=settings,
        checkpointer=InMemorySaver(),
        prompt_settings=PromptSettings(
            _env_file=None, history_soft_limit=2, history_hard_limit=3, history_compression_batch=2
        ),
    )
    for n in range(8):
        state = create_initial_state(
            user_id="alice",
            thread_id="t",
            run_id=f"r{n}",
            user_input=f"question {n}",
            settings=settings,
        )
        result = await graph.ainvoke(state, build_graph_config(state, settings))
        assert sum(m.name == "skill_reference" for m in model.calls[-1]) == 1
        assert len(result["runtime_context_events"]) == 1
    assert result["history_summary"]["version"] >= 2
    assert sum(isinstance(m, HumanMessage) for m in result["messages"]) <= 3


def test_attachment_surface_survives_sqlite_restart_without_entering_archive(tmp_path):
    model = Recorder(responses=[AIMessage("ok")])
    checkpoint = CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite")

    def application():
        return create_app(
            checkpoint_settings=checkpoint,
            model_provider=SimpleNamespace(get_chat_model=lambda: model),
        )

    with TestClient(application()) as client:
        thread = client.post("/api/v1/threads", json={}).json()["thread_id"]
        upload = client.post(
            f"/api/v1/threads/{thread}/attachments?filename=fixture.txt",
            content=b"ARCHIVED_ONLY_IN_PROMPT",
            headers={"Content-Type": "text/plain"},
        )
        assert upload.status_code == 201, upload.text
        attachment = upload.json()["id"]
        first = client.post(
            "/api/v1/chat",
            json={
                "thread_id": thread,
                "message": "inspect document",
                "attachment_ids": [attachment],
                "personalization_enabled": False,
            },
        )
        assert first.status_code == 200, first.text
    with TestClient(application()) as client:
        second = client.post(
            "/api/v1/chat",
            json={
                "thread_id": thread,
                "message": "inspect again",
                "attachment_ids": [attachment],
                "personalization_enabled": False,
            },
        )
        assert second.status_code == 200, second.text
        archive = client.get(f"/api/v1/threads/{thread}/messages")
        assert len(archive.json()["items"]) == 2
        assert "ARCHIVED_ONLY_IN_PROMPT" not in archive.text
    assert sum(m.name == "attachment_reference" for m in model.calls[-1]) == 1
    assert [stable_message(m) for m in model.calls[1][: len(model.calls[0])]] == [
        stable_message(m) for m in model.calls[0]
    ]
