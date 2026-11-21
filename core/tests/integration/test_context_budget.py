"""The selected model's context window bounds every model call (F07).

/ 所选模型的上下文窗口约束每次模型调用（F07）。
"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from app import api
from app.agent.prompting.plan import (
    PromptBudget,
    compose,
    fit_prompt_budget,
    reference,
)
from app.config import CheckpointSettings, PromptSettings
from app.infrastructure.llm.catalog import ModelItem
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class RecordingModel(FakeMessagesListChatModel):
    """Fake model recording every provider input. / 记录每次提供商输入的假模型。"""

    inputs: list = []

    def bind_tools(self, tools, **kwargs):
        return self

    async def _agenerate(self, messages, *args, **kwargs):
        self.inputs = [*self.inputs, messages]
        return await super()._agenerate(messages, *args, **kwargs)


def build_app(tmp_path, monkeypatch, *, window, responses):
    """Build an app whose only catalog model exposes one context window.
    / 构建只暴露单一窗口的应用。"""
    model = RecordingModel(responses=responses)
    catalog = {
        "windowed": (
            ModelItem(
                id="windowed",
                provider="test",
                reasoning_levels=("none",),
                default_effort="none",
                context_window=window,
            ),
            "windowed",
        )
    }
    monkeypatch.setattr(api, "configured_models", lambda: catalog)
    monkeypatch.setattr(
        api,
        "OpenAICompatibleProvider",
        lambda name, *, effort: SimpleNamespace(get_chat_model=lambda: model),
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(
            sqlite_path=tmp_path / "budget.sqlite", _env_file=None
        ),
        tool_factory=lambda _settings: [],
    )
    return app, model


def plan_with_history(references=()):
    """Compose a plan with two history turns and optional references.
    / 组装含两轮历史与可选引用的计划。"""
    messages = [
        HumanMessage(content="OLD-ONE " + "a" * 4000, id="run-1:user"),
        AIMessage(content="OLD-REPLY " + "b" * 4000, id="run-1:ai"),
        HumanMessage(content="OLD-TWO " + "c" * 4000, id="run-2:user"),
        AIMessage(content="OLD-TWO-REPLY " + "d" * 4000, id="run-2:ai"),
        HumanMessage(content="CURRENT", id="run-3:user"),
    ]
    return compose(
        system="SYSTEM",
        settings=PromptSettings(_env_file=None),
        messages=messages,
        run_id="run-3",
        references=list(references),
    )


def test_fitting_drops_references_before_whole_turns_and_keeps_the_current_turn():
    references = [
        reference("skill_reference", {"skills": ["s" * 2000]}),
        reference("explicit_reference", {"reference_data": "r" * 2000}),
    ]
    plan = plan_with_history(references)
    budget = PromptBudget(context_window=3500, output_reserve=500)
    fit = fit_prompt_budget(plan, budget)

    assert fit.dropped_references == ("skill_reference", "explicit_reference")
    assert fit.removed_turns >= 1
    assert not fit.over_limit
    texts = [str(message.content) for message in fit.plan.messages]
    assert any("CURRENT" in text for text in texts)
    assert not any("OLD-ONE" in text for text in texts)
    assert fit.metadata(budget)["budget_output_reserve"] == 500
    assert fit.metadata(budget)["budget_trimmed"] is True


def test_tool_pairs_and_surviving_turns_stay_intact():
    messages = [
        HumanMessage(content="OLD " + "a" * 3000, id="run-1:user"),
        AIMessage(
            content="",
            tool_calls=[{"name": "calculate", "args": {"x": 1}, "id": "call-1"}],
            id="run-1:ai",
        ),
        ToolMessage(content="TOOL-RESULT " + "t" * 3000, tool_call_id="call-1"),
        HumanMessage(content="NEWER " + "b" * 100, id="run-2:user"),
        AIMessage(content="REPLY " + "c" * 100, id="run-2:ai"),
        HumanMessage(content="CURRENT " + "d" * 100, id="run-3:user"),
    ]
    plan = compose(
        system="SYSTEM",
        settings=PromptSettings(_env_file=None),
        messages=messages,
        run_id="run-3",
        references=[],
    )
    fit = fit_prompt_budget(plan, PromptBudget(context_window=1500, output_reserve=400))
    assert fit.removed_turns == 1
    remaining = [message for message in fit.plan.messages if not isinstance(message.type, str)]
    assert any("CURRENT" in str(message.content) for message in fit.plan.messages)
    pairs = [
        (message.type, getattr(message, "tool_call_id", None))
        for message in fit.plan.messages
        if isinstance(message, (AIMessage, ToolMessage))
    ]
    assert ("tool", "call-1") not in pairs
    assert remaining or fit.plan.messages


def test_dropping_the_profile_snapshot_keeps_layer_boundaries_consistent():
    messages = [
        HumanMessage(content="OLD " + "a" * 2000, id="run-1:user"),
        AIMessage(content="OLD-REPLY " + "b" * 2000, id="run-1:ai"),
        HumanMessage(content="CURRENT", id="run-2:user"),
    ]
    plan = compose(
        system="SYSTEM",
        settings=PromptSettings(_env_file=None),
        messages=messages,
        run_id="run-2",
        references=[],
        snapshot={"reference_data": "S" * 6000, "memory_ids": [], "version": 1, "revision": 1},
    )
    assert plan.l1_end == 1
    fit = fit_prompt_budget(plan, PromptBudget(context_window=1500, output_reserve=400))
    assert "profile_snapshot" in fit.dropped_references
    assert fit.plan.l1_end == 0
    assert fit.plan.messages[0].type == "system"
    assert not fit.over_limit


def test_minimal_prompt_over_the_window_is_reported():
    plan = compose(
        system="SYSTEM " + "s" * 8000,
        settings=PromptSettings(_env_file=None),
        messages=[HumanMessage(content="CURRENT " + "c" * 4000, id="run-1:user")],
        run_id="run-1",
        references=[],
    )
    fit = fit_prompt_budget(plan, PromptBudget(context_window=2000, output_reserve=500))
    assert fit.over_limit is True
    assert fit.dropped_references == () and fit.removed_turns == 0


def usage_reply(text):
    """Fake assistant reply carrying token usage so run metrics are produced. /
    携带 token 用量的假助手回复，使运行指标生成。"""
    return AIMessage(
        content=text,
        usage_metadata={"input_tokens": 10, "output_tokens": 2, "total_tokens": 12},
    )


def test_successful_run_records_the_budget_with_an_output_reserve(tmp_path, monkeypatch):
    app, model = build_app(
        tmp_path, monkeypatch, window=20_000, responses=[usage_reply("ok")]
    )
    with TestClient(app) as client:
        reply = client.post(BASE + "/chat", json={"message": "hi"})
        assert reply.status_code == 200, reply.text
        metrics = reply.json()["metrics"]
        assert metrics["context_window"] == 20_000
        assert metrics["budget_context_window"] == 20_000
        assert metrics["budget_window_source"] == "catalog"
        assert metrics["budget_output_reserve"] > 0
        # Tool schemas are reserved from the same window before conversation tokens.
        # 工具 schema 也在同一窗口内预留，之后才是对话 Token。
        assert metrics["budget_input_limit"] <= 20_000 - metrics["budget_output_reserve"]
        assert metrics["budget_input_estimated"] <= metrics["budget_input_limit"]


def test_long_history_is_trimmed_instead_of_failing(tmp_path, monkeypatch):
    responses = [usage_reply(f"reply-{index}") for index in range(5)]
    app, model = build_app(tmp_path, monkeypatch, window=20_000, responses=responses)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        for index in range(4):
            reply = client.post(
                BASE + "/chat",
                json={"thread_id": thread, "message": f"turn-{index} " + "长文本" * 10000},
            )
            assert reply.status_code == 200, reply.text
        metrics = reply.json()["metrics"]
        assert metrics["budget_trimmed"] is True
        assert metrics["budget_removed_turns"] >= 1
        last_input = json.dumps(model.inputs[-1], ensure_ascii=False, default=str)
        assert "turn-0" not in last_input
        assert "turn-3" in last_input


def test_oversized_request_reports_a_diagnosable_limit_and_allows_an_edited_turn(
    tmp_path, monkeypatch
):
    app, model = build_app(
        tmp_path,
        monkeypatch,
        window=6_000,
        responses=[usage_reply("small reply")],
    )
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "chat"}).json()["thread_id"]
        oversized = client.post(
            BASE + "/chat",
            json={"thread_id": thread, "message": "x" * 32_000},
        )
        assert oversized.status_code == 422, oversized.text
        body = oversized.json()
        assert body["code"] == "CONTEXT_LIMIT_EXCEEDED"
        assert "Context window: 6000" in body["message"]
        assert model.inputs == []
        # The failed run cannot be retried verbatim; an edited message starts a new turn.
        # 失败的运行无法原样重试；编辑后的消息可开启新轮次。
        edited = client.post(BASE + "/chat", json={"thread_id": thread, "message": "short"})
        assert edited.status_code == 200, edited.text
        assert edited.json()["response"] == "small reply"
