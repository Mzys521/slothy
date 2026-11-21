"""F10 execution alignment: availability, plan constraints and shadow parity.
 / F10 执行对齐：可用性、计划约束与影子模式一致性。"""

from collections.abc import Sequence
from copy import deepcopy
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

from fastapi.testclient import TestClient
from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.tools import BaseTool, tool
from pydantic import Field, SecretStr

from app import api
from app.agent.decision.contracts import DecisionOutput
from app.agent.decision.model import DecisionModelClient
from app.agent.decision.resolution import resolve_decision_settings
from app.agent.decision.settings_store import DecisionSelection
from app.agent.graph import (
    AgentServices,
    ContextBuildResult,
    build_agent_graph,
    create_initial_state,
)
from app.agent.prompting.plan import stable_message
from app.config import AgentSettings, CheckpointSettings, DecisionSettings
from app.infrastructure.llm.catalog import ModelItem
from app.providers import ProviderConnection, ProviderInput, ProviderModel
from tests.support.legacy_app import create_app


class RecordingModel(FakeMessagesListChatModel):
    """Fake chat model recording each provider-visible input. / 记录每次提供商输入的假聊天模型。"""

    calls: list[list[BaseMessage]] = Field(default_factory=list)
    bound_names: list[str] = Field(default_factory=list)

    def bind_tools(self, tools: Sequence[BaseTool], **kwargs: Any) -> BaseChatModel:
        """Record the bound names and return this model. / 记录绑定名称并返回自身。"""
        self.bound_names = [selected.name for selected in tools]
        return self

    def _generate(self, messages: list[BaseMessage], *args: Any, **kwargs: Any):
        """Record one provider input before answering. / 回答前记录一次提供商输入。"""
        self.calls.append(deepcopy(messages))
        return super()._generate(messages, *args, **kwargs)


class FixedDecision:
    """Decision client returning one fixed plan. / 返回固定计划的决策客户端。"""

    configured = True

    def __init__(self, output: DecisionOutput):
        self.output = output

    async def decide(self, payload, *, timeout):
        """Return the fixed plan. / 返回固定计划。"""
        return self.output


def fake_provider(model: BaseChatModel):
    """Wrap a chat model in the minimal provider protocol. / 用最小 Provider 协议包装聊天模型。"""
    return SimpleNamespace(get_chat_model=lambda: model)


def sample_memory(identifier: str = "memory-1") -> dict:
    """Return one valid memory candidate. / 返回一个有效记忆候选。"""
    return {
        "id": identifier,
        "user_id": "user-1",
        "status": "active",
        "scope": "global",
        "scope_ref": None,
        "content": "历史偏好 Go",
    }


def sample_ports():
    """Build read ports shared by the shadow and off graphs. / 构建影子与关闭图共用的读取端口。"""
    profile = SimpleNamespace(
        get_profile=AsyncMock(
            return_value={"user_id": "user-1", "version": 1, "preferences": {"language": "Go"}}
        )
    )
    memory = SimpleNamespace(
        retrieve=AsyncMock(
            return_value=[
                {
                    "id": "memory-1",
                    "user_id": "user-1",
                    "status": "active",
                    "scope": "global",
                    "scope_ref": None,
                    "content": "历史偏好 Go",
                }
            ]
        )
    )
    context = SimpleNamespace(
        build=AsyncMock(
            return_value=ContextBuildResult(
                text="历史偏好 Go，仅作参考。", memory_ids=("memory-1",), token_estimate=5
            )
        )
    )
    return profile, memory, context


def make_state(settings: AgentSettings, *, run_id: str = "run-1"):
    """Create one deterministic run state. / 创建一个确定性的运行状态。"""
    return create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="hello",
        settings=settings,
        run_id=run_id,
    )


async def test_enforced_plan_trims_memory_and_reference_budgets():
    """Enforced context limits reach retrieval and reference resolution.
    / 已执行的上下文限制到达记忆检索与引用解析。"""
    seen_budgets: list[int | None] = []

    async def read_references(user, thread, references, *, budget=None):
        seen_budgets.append(budget)
        included = budget is None or budget > 0
        return (
            [
                {
                    "reference_id": "file::notes.txt",
                    "included": included,
                    "status": "ok",
                }
            ],
            "REFERENCE_BODY" if included else "",
        )

    profile = SimpleNamespace(get_profile=AsyncMock(return_value=None))
    memory = SimpleNamespace(retrieve=AsyncMock(return_value=[sample_memory()]))
    context = SimpleNamespace(
        build=AsyncMock(
            return_value=ContextBuildResult(
                text="历史偏好", memory_ids=("memory-1",), token_estimate=4
            )
        )
    )
    model = RecordingModel(responses=[AIMessage(content="answer")])
    settings = AgentSettings(_env_file=None, memory_top_k=4)
    graph = build_agent_graph(
        AgentServices(
            fake_provider(model),
            profile,
            memory,
            context,
            reference_reader=read_references,
        ),
        settings=settings,
        decision_settings=DecisionSettings(_env_file=None, mode="enforce"),
        decision_model_client=FixedDecision(
            DecisionOutput(
                route="model",
                tool_policy={"allowed": None},
                context_plan={"memory_top_k": 0, "reference_budget": 0},
            )
        ),
    )
    state = create_initial_state(
        user_id="user-1",
        thread_id="thread-1",
        user_input="hello",
        settings=settings,
        run_id="run-1",
        references=[{"kind": "file", "path": "notes.txt"}],
    )
    result = await graph.ainvoke(state)
    assert seen_budgets == [0]
    assert memory.retrieve.await_count == 0
    assert context.build.await_count == 0
    assert result["used_memory_ids"] == []
    assert result["personalization_context"] == ""
    assert result["used_reference_ids"] == []
    assert all(
        "REFERENCE_BODY" not in str(stable_message(message))
        for message in model.calls[0]
    )
    assert result["response"] == "answer"


async def test_zero_quota_and_empty_allowlist_block_even_a_safe_tool():
    """An enforced empty tool policy prevents synthetic tool side effects.
    / 已执行的空工具策略阻止合成工具副作用。"""
    effects: list[str] = []

    @tool
    def safe_probe() -> str:
        """A safe synthetic probe. / 安全合成探针。"""
        effects.append("executed")
        return "ok"

    model = RecordingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[{"id": "probe-1", "name": "safe_probe", "args": {}}],
            ),
            AIMessage(content="done"),
        ]
    )
    settings = AgentSettings(_env_file=None, personalization_enabled=False, max_steps=10)
    graph = build_agent_graph(
        AgentServices(fake_provider(model)),
        settings=settings,
        tools=[safe_probe],
        decision_settings=DecisionSettings(_env_file=None, mode="enforce"),
        decision_model_client=FixedDecision(
            DecisionOutput(
                route="tool",
                tool_policy={"allowed": [], "max_calls": 0},
                context_plan={"memory_top_k": 0, "reference_budget": 0},
            )
        ),
    )
    result = await graph.ainvoke(make_state(settings))
    assert effects == []
    tool_message = next(
        message for message in result["messages"] if isinstance(message, ToolMessage)
    )
    assert tool_message.tool_call_id == "probe-1"
    assert tool_message.status == "error"
    assert "TOOL_NOT_ALLOWED" in tool_message.content
    assert result["response"] == "done"
    assert result["decision_enforced"] is True
    assert result["decision_policy_version"] >= 2


async def test_shadow_mode_input_matches_off_mode_but_records_the_plan():
    """Shadow restrictions stay metric-only; provider input equals the off mode.
    / 影子模式的限制仅作为指标；提供商输入与关闭模式一致。"""
    settings = AgentSettings(_env_file=None, memory_top_k=4)
    off_profile, off_memory, off_context = sample_ports()
    shadow_profile, shadow_memory, shadow_context = sample_ports()
    off_model = RecordingModel(responses=[AIMessage(content="answer")])
    shadow_model = RecordingModel(responses=[AIMessage(content="answer")])
    off_graph = build_agent_graph(
        AgentServices(
            fake_provider(off_model),
            off_profile,
            off_memory,
            off_context,
            skill_reader=lambda: [{"id": "guide", "instructions": "FIXED_GUIDE"}],
        ),
        settings=settings,
    )
    shadow_graph = build_agent_graph(
        AgentServices(
            fake_provider(shadow_model),
            shadow_profile,
            shadow_memory,
            shadow_context,
            skill_reader=lambda: [{"id": "guide", "instructions": "FIXED_GUIDE"}],
        ),
        settings=settings,
        decision_settings=DecisionSettings(_env_file=None, mode="shadow"),
        decision_model_client=FixedDecision(
            DecisionOutput(
                route="model",
                tool_policy={"allowed": [], "max_calls": 0},
                context_plan={"memory_top_k": 0, "reference_budget": 100},
                reasons=["MODEL_DEFAULT"],
            )
        ),
    )
    off_result = await off_graph.ainvoke(make_state(settings))
    shadow_result = await shadow_graph.ainvoke(make_state(settings))

    assert [stable_message(m) for m in shadow_model.calls[0]] == [
        stable_message(m) for m in off_model.calls[0]
    ]
    assert shadow_result["decision"] is not None
    assert shadow_result["decision_source"] == "model"
    assert shadow_result["decision_enforced"] is False
    assert shadow_result["decision_reasons"]
    assert off_result["decision"] is None
    assert off_memory.retrieve.await_args.kwargs["limit"] == 4
    assert shadow_memory.retrieve.await_args.kwargs["limit"] == 4
    assert [
        event["scope"] for event in shadow_result["runtime_context_events"]
    ] == [event["scope"] for event in off_result["runtime_context_events"]]


def test_native_anthropic_provider_resolves_availability(monkeypatch):
    """A native Anthropic connection resolves through its protocol adapter.
    / 原生 Anthropic 连接通过其协议适配器解析为可用。"""
    created: dict[str, Any] = {}

    class FakeAnthropicChat:
        def __init__(self, **kwargs):
            created.update(kwargs)

        async def ainvoke(self, messages, config=None):
            return AIMessage(content='{"route":"model","reasons":["MODEL_DEFAULT"]}')

    monkeypatch.setattr("app.providers.ChatAnthropic", FakeAnthropicChat)
    connection = ProviderConnection(
        ProviderInput(
            id="anthropic",
            name="Anthropic",
            protocol="anthropic",
            base_url="https://api.anthropic.test",
            models=[ProviderModel(id="claude-test")],
        ),
        SecretStr("test-key"),
        "claude-test",
    )
    provider = connection.build_provider(None)
    assert provider.prompt_cache_protocol == "anthropic"
    assert created["model_name"] == "claude-test"

    resolution = resolve_decision_settings(
        DecisionSelection(model="anthropic/claude-test", configured=True),
        desktop=False,
        environment=DecisionSettings(_env_file=None, mode="enforce"),
        provider_for=lambda model_id: provider if model_id == "anthropic/claude-test" else None,
    )
    assert resolution.available is True
    assert resolution.settings.mode == "enforce"
    assert resolution.provider is provider


async def test_native_anthropic_decision_client_uses_the_injected_adapter(monkeypatch):
    """The decision client executes through the native protocol adapter.
    / 决策客户端通过原生协议适配器执行。"""
    class FakeAnthropicChat:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        async def ainvoke(self, messages, config=None):
            return AIMessage(content='{"route":"model","rationale":"anthropic"}')

    monkeypatch.setattr("app.providers.ChatAnthropic", FakeAnthropicChat)
    connection = ProviderConnection(
        ProviderInput(
            id="anthropic",
            name="Anthropic",
            protocol="anthropic",
            base_url="https://api.anthropic.test",
            models=[ProviderModel(id="claude-test")],
        ),
        SecretStr("test-key"),
        "claude-test",
    )
    provider = connection.build_provider(None)
    client = DecisionModelClient(
        DecisionSettings(_env_file=None, mode="shadow"), provider
    )
    output = await client.decide({"request": {"user_input": "hi"}}, timeout=2)
    assert output is not None
    assert output.rationale == "anthropic"


def test_settings_api_reports_native_anthropic_availability(tmp_path, monkeypatch):
    """GET/PUT settings report the same native adapter the runner will use.
    / GET/PUT 设置报告与运行器相同的原生适配器可用性。"""
    class FakeAnthropicChat:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

        def bind_tools(self, tools, **kwargs):
            return self

        async def ainvoke(self, messages, config=None):
            return AIMessage(content="ok")

    monkeypatch.setattr("app.providers.ChatAnthropic", FakeAnthropicChat)
    connection = ProviderConnection(
        ProviderInput(
            id="anthropic",
            name="Anthropic",
            protocol="anthropic",
            base_url="https://api.anthropic.test",
            models=[ProviderModel(id="claude-test")],
        ),
        SecretStr("test-key"),
        "claude-test",
    )

    class FakeProviderStore:
        def __init__(self, path):
            self.path = path

        def catalog(self):
            return {
                "anthropic/claude-test": (
                    ModelItem(
                        id="anthropic/claude-test",
                        provider="anthropic",
                        default_effort=None,
                    ),
                    connection,
                )
            }

        def list(self):
            return []

        def search_key(self):
            return None

    monkeypatch.setattr(api, "ProviderStore", FakeProviderStore)
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "anthropic.sqlite"),
        work_tools=(),
    )
    with TestClient(app) as client:
        initial = client.get("/api/v1/settings/decision").json()
        assert initial["available"] is False
        assert initial["mode"] == "off"
        saved = client.put(
            "/api/v1/settings/decision", json={"model": "anthropic/claude-test"}
        )
        assert saved.status_code == 200, saved.text
        payload = saved.json()
        assert payload["model"] == "anthropic/claude-test"
        assert payload["available"] is True
        assert payload["mode"] == "enforce"
        assert payload["source"] == "catalog"
        current = client.get("/api/v1/settings/decision").json()
        assert current == payload


def test_independent_backend_environment_reaches_shadow_and_enforce(tmp_path, monkeypatch):
    """Environment-only decisions work without a saved selection and explicit off wins.
    / 仅环境配置无需保存选择即可工作，显式关闭优先。"""
    monkeypatch.setenv("DECISION_MODE", "shadow")
    monkeypatch.setenv("DASHSCOPE__MODEL", "decision-env-model")
    monkeypatch.setenv("DASHSCOPE__API_KEY", "decision-env-key")
    monkeypatch.setenv("DASHSCOPE__MODEL_URL", "https://decision.example.test/v1")
    model = RecordingModel(responses=[AIMessage(content="ok")])
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "env.sqlite"),
        model_provider=fake_provider(model),
        work_tools=(),
    )
    with TestClient(app) as client:
        shadow = client.get("/api/v1/settings/decision").json()
        assert shadow["mode"] == "shadow"
        assert shadow["available"] is True
        assert shadow["source"] == "environment"
        assert shadow["reason"] is None

        resolution = app.state.resolved_decision_settings()
        assert resolution.settings.model == "decision-env-model"
        assert resolution.provider is not None

        monkeypatch.setenv("DECISION_MODE", "enforce")
        enforced = client.get("/api/v1/settings/decision").json()
        assert enforced["mode"] == "enforce"
        assert enforced["available"] is True

        cleared = client.put("/api/v1/settings/decision", json={"model": None})
        assert cleared.status_code == 200, cleared.text
        explicit = cleared.json()
        assert explicit["mode"] == "off"
        assert explicit["available"] is False
        assert explicit["source"] == "explicit-off"
        assert explicit["reason"] == "DECISION_DISABLED"
