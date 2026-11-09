"""Runtime context cache invariants and revocation. / 运行上下文的前缀、撤销与边界。"""

import json

import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from app.agent.prompting.plan import canonical, compose, reference, stable_message
from app.agent.prompting.runtime_context import project_runtime_context
from app.agent.prompting.sections import PromptSection, assemble_sections
from app.config import PromptSettings
from app.infrastructure.llm.openai_compatible import _VendorChatOpenAI
from app.observability.journal import context_breakdown, estimate_tokens

SCOPE = {"user": "alice", "thread": "session", "project": "workspace"}


def projection(messages, previous=(), text="first", source="owned-file", **options):
    return project_runtime_context(
        messages=messages,
        references=[reference("explicit_reference", {"reference_data": text})] if text else [],
        sources={"explicit_reference": [source]} if text else {},
        previous=list(previous),
        scope=options.pop("scope", SCOPE),
        **options,
    )


def wire(messages, surface, run="r1"):
    return compose(
        system="trusted",
        settings=PromptSettings(_env_file=None),
        messages=messages,
        run_id=run,
        references=[],
        context_by_anchor=surface.by_anchor,
    )


def test_unchanged_context_reuses_prior_position_without_changing_turn_count():
    first = [HumanMessage("one", id="r1:user")]
    surface = projection(first)
    initial = wire(first, surface)
    second = [*first, AIMessage("answer", id="a1"), HumanMessage("two", id="r2:user")]
    reused = projection(second, json.loads(canonical(surface.events)))
    continued = wire(second, reused, "r2")
    assert reused.metadata["runtime_context_appended"] == 0
    assert [stable_message(m) for m in continued.messages[: len(initial.messages)]] == [
        stable_message(m) for m in initial.messages
    ]
    assert continued.metadata["l2_turns"] == 1
    assert sum(m.name == "explicit_reference" for m in continued.messages) == 1
    assert len(second) == 3


def test_changed_file_appends_after_tool_results_and_preserves_sent_prefix():
    messages = [HumanMessage("inspect", id="r1:user")]
    old = projection(messages)
    sent = wire(messages, old)
    messages += [
        AIMessage("", id="a", tool_calls=[{"id": "call", "name": "read", "args": {}}]),
        ToolMessage("file changed", tool_call_id="call", id="tool"),
    ]
    fresh = projection(messages, old.events, text="updated")
    current = wire(messages, fresh)
    assert [stable_message(m) for m in current.messages[: len(sent.messages)]] == [
        stable_message(m) for m in sent.messages
    ]
    assert current.messages[-1].name == "explicit_reference"
    assert "updated" in current.messages[-1].text
    assert len(fresh.events) == 2


@pytest.mark.parametrize(
    "source,scope,text",
    [
        ("other-file", SCOPE, "replacement"),
        ("owned-file", {**SCOPE, "thread": "different"}, "replacement"),
        ("owned-file", {**SCOPE, "user": "bob"}, "replacement"),
        ("owned-file", SCOPE, ""),
    ],
)
def test_revoked_source_scope_and_clear_never_replay_old_body(source, scope, text):
    messages = [HumanMessage("one", id="r1:user")]
    old = projection(messages, text="PRIVATE_OLD_BODY")
    messages += [AIMessage("answer", id="a"), HumanMessage("two", id="r2:user")]
    fresh = projection(messages, old.events, source=source, scope=scope, text=text)
    assert "PRIVATE_OLD_BODY" not in canonical(fresh.events)
    assert "PRIVATE_OLD_BODY" not in canonical(
        [stable_message(m) for m in wire(messages, fresh, "r2").messages]
    )
    if not text:
        assert fresh.events[-1]["sections"] == []
        assert "cleared" in wire(messages, fresh, "r2").messages[-1].text


def test_compaction_reinjects_latest_and_oversize_does_not_enter_checkpoint():
    old = projection([HumanMessage("old", id="r1:user")])
    messages = [HumanMessage("after compression", id="r2:user")]
    restored = projection(messages, old.events)
    assert len(restored.events) == 1 and restored.events[0]["anchor"] == "r2:user"
    oversized = projection(messages, text="full document " * 500, max_bytes=600)
    assert not oversized.events and oversized.metadata["runtime_context_overflow"]
    assert "full document" in wire(messages, oversized, "r2").messages[-1].text


def test_snapshot_capacity_is_bounded_and_latest_value_survives():
    messages, previous = [], []
    for n in range(30):
        messages.append(HumanMessage(str(n), id=f"r{n}:user"))
        current = projection(messages, previous, text=f"value {n}", max_snapshots=4, max_bytes=1800)
        previous = current.events
        assert len(previous) <= 4 and len(canonical(previous).encode()) <= 1800
    assert "value 29" in previous[-1]["sections"][0]["content"]


def test_memory_cannot_enter_runtime_log_and_static_sections_are_deterministic():
    with pytest.raises(ValueError, match="operational"):
        project_runtime_context(
            messages=[HumanMessage("x", id="r1:user")],
            references=[reference("personalization_reference", {"text": "memory"})],
            sources={},
            previous=[],
            scope=SCOPE,
        )
    sections = [PromptSection("tools", 200, "tools"), PromptSection("persona", 100, "persona")]
    assert (
        assemble_sections(sections)
        == assemble_sections(list(reversed(sections)))
        == "persona\n\ntools"
    )
    with pytest.raises(ValueError, match="unique"):
        assemble_sections([sections[0], sections[0]])


def test_deepseek_only_passes_tool_call_reasoning_without_mutating_history():
    model = _VendorChatOpenAI(
        model="deepseek-test",
        api_key="offline-test-key",
        vendor="deepseek",
        effort="high",
        extra_body={"thinking": {"type": "enabled"}},
    )
    messages = [
        HumanMessage("one"),
        AIMessage("final", additional_kwargs={"deepseek_reasoning_content": "FINAL_PRIVATE"}),
        HumanMessage("two"),
        AIMessage(
            "",
            tool_calls=[{"id": "call", "name": "read", "args": {}}],
            additional_kwargs={"deepseek_reasoning_content": "TOOL_REQUIRED"},
        ),
        ToolMessage("result", tool_call_id="call"),
    ]
    payload = model._get_request_payload(
        messages, tools=[{"type": "function", "function": {"name": "read"}}]
    )
    assert "reasoning_content" not in payload["messages"][1]
    assert payload["messages"][3]["reasoning_content"] == "TOOL_REQUIRED"
    assert messages[1].additional_kwargs["deepseek_reasoning_content"] == "FINAL_PRIVATE"


def test_context_accounting_distinguishes_real_user_from_trailing_reference():
    system = SystemMessage("system " * 20)
    user = HumanMessage("current user " * 10, id="r1:user")
    runtime = reference("attachment_reference", {"text": "document " * 30})
    memory = reference("personalization_reference", {"reference_data": "preference"})
    values = context_breakdown(
        "ignored duplicate system",
        [system, HumanMessage("old user"), AIMessage("old answer"), memory, user, runtime],
        "preference",
        [],
    )
    assert values["system"] == estimate_tokens(system.text)
    assert values["user"] == estimate_tokens(user.text)
    assert values["memory"] == estimate_tokens(memory.text)
    assert values["runtime"] == estimate_tokens(runtime.text)
    assert values["history"] == estimate_tokens("old user") + estimate_tokens("old answer")
