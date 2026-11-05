"""运行状态初始化约束。 / Contracts for per-run state initialization."""

import pytest

from app.agent.graph import build_graph_config, create_initial_state
from app.config import AgentSettings


@pytest.mark.parametrize(
    ("global_enabled", "requested", "expected"),
    [(True, None, True), (True, False, False), (True, True, True), (False, True, False)],
)
def test_personalization_combines_global_and_request_gates(
    global_enabled: bool,
    requested: bool | None,
    expected: bool,
) -> None:
    state = create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="请用 Go 回答",
        settings=AgentSettings(personalization_enabled=global_enabled, _env_file=None),
        personalization_enabled=requested,
    )
    assert state["personalization_enabled"] is expected
    assert state["user_profile"] is None
    assert state["relevant_memories"] == []


def test_new_runs_have_independent_working_data_and_stable_retry_message_ids() -> None:
    settings = AgentSettings(_env_file=None)
    first = create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="  保留格式\n",
        settings=settings,
        run_id="run-1",
    )
    second = create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="  保留格式\n",
        settings=settings,
        run_id="run-1",
    )
    assert first["messages"][0].id == second["messages"][0].id
    assert first["messages"][0].content == "  保留格式\n"
    first["messages"].clear()
    first["warnings"].append("memory unavailable")
    first["relevant_memories"].append({"id": "memory-1"})
    assert len(second["messages"]) == 1
    assert second["warnings"] == []
    assert second["relevant_memories"] == []
    assert "remaining_steps" not in second
    assert "max_steps" not in second
    assert second["project_id"] is None


@pytest.mark.parametrize(
    "invalid",
    [{"user_id": " "}, {"thread_id": ""}, {"user_input": "\n"}, {"run_id": ""}],
)
def test_blank_run_inputs_are_rejected(invalid: dict) -> None:
    values = {"user_id": "user-1", "thread_id": "thread-1", "user_input": "你好"}
    values.update(invalid)
    with pytest.raises(ValueError):
        create_initial_state(**values, settings=AgentSettings(_env_file=None))


def test_checkpoint_keys_are_user_scoped_and_avoid_delimiter_collisions() -> None:
    settings = AgentSettings(_env_file=None)
    configs = [
        build_graph_config({"user_id": user, "thread_id": thread}, settings)
        for user, thread in (("a:b", "c"), ("a", "b:c"), ("another", "c"))
    ]
    assert len({config["configurable"]["thread_id"] for config in configs}) == 3
    assert configs[0]["metadata"] == {"user_id": "a:b", "thread_id": "c"}
