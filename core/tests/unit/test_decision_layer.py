"""Decision-layer contracts, guards and engine tests. / 决策层契约、护栏与引擎测试。"""

import pytest
from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from app.agent.decision.contracts import DecisionOutput
from app.agent.decision.engine import DecisionEngine
from app.agent.decision.fallback import rules_plan
from app.agent.decision.guards import DecisionGuards
from app.agent.decision.model import (
    DecisionModelClient,
    DecisionModelError,
    _parse_json,
)
from app.agent.decision.resolution import resolve_decision_settings
from app.agent.decision.settings_store import DecisionSelection
from app.agent.tools.gateway import ToolGateway
from app.agent.tools.registry import ToolRegistry
from app.config import DecisionSettings


def test_decision_settings_default_to_off(monkeypatch):
    """The layer is opt-in so existing behavior and tests never call an endpoint.
    / 决策层默认关闭，现有行为与测试不会调用外部端点。"""
    monkeypatch.setenv("DECISION_MODE", "off")
    settings = DecisionSettings(_env_file=None)
    assert settings.mode == "off"
    assert settings.timeout_seconds > 0


def test_environment_resolution_reaches_shadow_and_enforce():
    """Environment credentials resolve a callable adapter in both active modes.
    / 环境凭据在两种启用模式下解析出可调用适配器。"""
    for mode in ("shadow", "enforce"):
        environment = DecisionSettings(
            _env_file=None,
            mode=mode,
            model="decision-model",
            api_key="decision-key",
            base_url="https://decision.example.test/v1",
        )
        resolution = resolve_decision_settings(
            DecisionSelection(model=None, configured=False),
            desktop=False,
            environment=environment,
            provider_for=lambda _model: None,
        )
        assert resolution.mode == mode
        assert resolution.available is True
        assert resolution.source == "environment"
        assert resolution.provider is not None


def test_desktop_resolution_ignores_environment_and_needs_an_explicit_choice():
    """Desktop never adopts environment values as an implicit decision model.
    / 桌面版不会把环境值当作隐式决策模型。"""
    environment = DecisionSettings(
        _env_file=None,
        mode="enforce",
        model="decision-model",
        api_key="decision-key",
        base_url="https://decision.example.test/v1",
    )
    unconfigured = resolve_decision_settings(
        DecisionSelection(model=None, configured=False),
        desktop=True,
        environment=environment,
        provider_for=lambda _model: None,
    )
    assert unconfigured.available is False
    assert unconfigured.mode == "off"
    explicit = resolve_decision_settings(
        DecisionSelection(model=None, configured=True),
        desktop=True,
        environment=environment,
        provider_for=lambda _model: None,
    )
    assert explicit.available is False
    assert explicit.source == "explicit-off"


def test_guards_clamp_unknown_tools_and_unbacked_identity():
    """Unknown tools are dropped and an unbacked identity route becomes model.
    / 未知工具被丢弃；无身份意图的身份路由降级为模型。"""
    settings = DecisionSettings(_env_file=None)
    guards = DecisionGuards(ToolRegistry(), settings)
    raw = DecisionOutput(
        route="identity",
        tool_policy={"allowed": ["does-not-exist"], "preferred": ["does-not-exist"]},
        reasons=["IDENTITY_INTENT"],
    )
    plan = guards.apply(raw, {"user_input": "普通问题"}, "chat")
    assert plan.route == "model"
    assert plan.identity_intent is None
    assert plan.tool_policy.allowed == []
    assert "GUARDED" in plan.reasons


def test_guards_accept_real_identity_question():
    """A direct self-identity question keeps the identity route.
    / 直接本人身份问句保留身份路由。"""
    plan = DecisionGuards(ToolRegistry(), DecisionSettings(_env_file=None)).apply(
        DecisionOutput(route="identity", reasons=["IDENTITY_INTENT"]),
        {"user_input": "我叫什么名字？"},
        "chat",
    )
    assert plan.route == "identity"
    assert plan.identity_intent == "name"


def test_guards_keep_empty_allowlist_distinct_from_no_restriction():
    """``[]`` forbids every tool while ``None`` imposes no restriction.
    / ``[]`` 禁止所有工具，``None`` 不施加限制。"""
    guards = DecisionGuards(ToolRegistry(), DecisionSettings(_env_file=None))
    empty = guards.apply(
        DecisionOutput(route="model", tool_policy={"allowed": []}), {"user_input": "hello"}
    )
    unrestricted = guards.apply(
        DecisionOutput(route="model", tool_policy={"allowed": None}), {"user_input": "hello"}
    )
    assert empty.tool_policy.allowed == []
    assert unrestricted.tool_policy.allowed is None


def test_rules_plan_keeps_chat_autonomy():
    """The deterministic baseline imposes no tool quota or allowlist.
    / 确定性基线不施加工具额度或允许列表。"""
    plan = rules_plan(
        {"user_input": "普通问题", "approval_policy": "ask"},
        ToolRegistry(),
        DecisionSettings(_env_file=None),
        mode="chat",
    )
    assert plan.source == "rules"
    assert plan.tool_policy.allowed is None
    assert plan.tool_policy.max_calls is None


def test_rules_fallback_matches_current_behavior():
    """Fallback keeps identity shortcut and full context defaults.
    / 回退保留身份捷径与完整上下文默认值。"""
    plan = rules_plan(
        {"user_input": "我叫什么名字？", "approval_policy": "ask"},
        ToolRegistry(),
        DecisionSettings(_env_file=None),
        mode="chat",
    )
    assert plan.route == "identity"
    assert plan.source == "rules"
    assert plan.context_plan.memory_top_k >= 1


class _FakeModel:
    """Configured decision model returning one fixed output. / 返回固定输出的假决策模型。"""

    configured = True

    def __init__(self, output: DecisionOutput):
        self.output = output

    async def decide(self, payload, *, timeout):
        """Return the fixed output. / 返回固定输出。"""
        return self.output


class _FailingModel:
    """Configured decision model raising one failure. / 抛出指定失败的假决策模型。"""

    configured = True

    def __init__(self, error: Exception | dict):
        self.error = error

    async def decide(self, payload, *, timeout):
        """Raise the configured failure. / 抛出配置的失败。"""
        if isinstance(self.error, Exception):
            raise self.error
        return self.error


class _ReplyModel:
    """Minimal chat model returning one fixed text reply. / 返回固定文本的最小聊天模型。"""

    def __init__(self, reply: str):
        self.reply = reply

    async def ainvoke(self, messages, config=None):
        """Return one assistant message. / 返回一条助手消息。"""
        return AIMessage(content=self.reply)


class _ProtocolProvider:
    """Protocol fake adapter exposing one chat model. / 暴露聊天模型的协议假适配器。"""

    def __init__(self, model):
        self.model = model

    def get_chat_model(self):
        """Return the shared fake chat model. / 返回共享假聊天模型。"""
        return self.model


async def test_shadow_engine_records_model_plan_without_enforcement():
    """Shadow mode stores a model plan but does not enforce it.
    / 影子模式记录模型计划但不执行。"""
    settings = DecisionSettings(_env_file=None, mode="shadow")
    engine = DecisionEngine(
        settings,
        ToolRegistry(),
        model_client=_FakeModel(DecisionOutput(route="model", reasons=["MODEL_DEFAULT"])),
    )
    plan = await engine.decide({"user_input": "hello", "approval_policy": "ask"}, "chat")
    assert plan is not None
    assert plan.source == "model"
    assert plan.enforced is False


async def test_enforce_engine_marks_plan_enforced():
    """Enforce mode marks the plan as applied. / 强制模式标记计划已生效。"""
    settings = DecisionSettings(_env_file=None, mode="enforce")
    engine = DecisionEngine(
        settings,
        ToolRegistry(),
        model_client=_FakeModel(DecisionOutput(route="model")),
    )
    plan = await engine.decide({"user_input": "hello", "approval_policy": "ask"}, "chat")
    assert plan is not None
    assert plan.enforced is True


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        (TimeoutError("slow decision"), "FALLBACK_MODEL_UNAVAILABLE"),
        (ValueError("not valid"), "FALLBACK_MODEL_UNAVAILABLE"),
        ({"route": "not-a-route"}, "FALLBACK_INVALID_OUTPUT"),
    ],
)
async def test_decision_failures_return_explainable_autonomous_fallback(
    failure, expected_reason
):
    """Timeout and invalid structures fall back to an autonomous chat model.
    / 超时与非法结构回退为聊天模型自主决策。"""
    settings = DecisionSettings(_env_file=None, mode="enforce")
    engine = DecisionEngine(
        settings,
        ToolRegistry(),
        model_client=_FailingModel(failure),
    )
    plan = await engine.decide({"user_input": "hello", "approval_policy": "ask"}, "chat")
    assert plan is not None
    assert plan.source == "fallback"
    assert plan.route == "model"
    assert plan.enforced is True
    assert plan.tool_policy.allowed is None
    assert plan.tool_policy.max_calls is None
    assert expected_reason in plan.reasons
    assert "chat model decides" in plan.rationale


async def test_decision_client_reuses_protocol_adapter_and_parses_shared_contract():
    """The client calls the injected provider adapter and parses its reply.
    / 客户端调用注入的适配器并解析其回复。"""
    client = DecisionModelClient(
        DecisionSettings(_env_file=None, mode="shadow"),
        _ProtocolProvider(
            _ReplyModel(
                '{"route":"tool","tool_policy":{"allowed":[],"max_calls":0},'
                '"context_plan":{"memory_top_k":0,"reference_budget":0}}'
            )
        ),
    )
    output = await client.decide({}, timeout=1)
    assert output is not None
    assert output.route == "tool"
    assert output.tool_policy.allowed == []
    assert output.tool_policy.max_calls == 0
    assert output.context_plan.memory_top_k == 0


async def test_decision_client_rejects_invalid_structure():
    """Invalid JSON and schema violations are distinguishable failures.
    / 非法 JSON 与 schema 违规是可区分的失败。"""
    client = DecisionModelClient(
        DecisionSettings(_env_file=None, mode="shadow"),
        _ProtocolProvider(_ReplyModel("not a plan")),
    )
    with pytest.raises(DecisionModelError) as broken:
        await client.decide({}, timeout=1)
    assert broken.value.reason == "invalid_json"

    structurally_wrong = DecisionModelClient(
        DecisionSettings(_env_file=None, mode="shadow"),
        _ProtocolProvider(_ReplyModel('{"route":"unknown"}')),
    )
    with pytest.raises(DecisionModelError) as mismatch:
        await structurally_wrong.decide({}, timeout=1)
    assert mismatch.value.reason == "invalid_structure"


async def test_disabled_engine_returns_none():
    """Off mode performs no decision work. / 关闭模式不做决策工作。"""
    engine = DecisionEngine(DecisionSettings(_env_file=None, mode="off"), ToolRegistry())
    assert await engine.decide({"user_input": "hello"}, "chat") is None


def test_parse_json_accepts_fenced_reply():
    """Fenced JSON replies are accepted. / 支持被代码围栏包裹的 JSON 回复。"""
    assert _parse_json('```json\n{"route":"model"}\n```') == {"route": "model"}


@tool
def safe_probe() -> str:
    """A safe synthetic probe for gateway tests. / 网关测试用安全合成探针。"""
    return "ok"


def _gateway() -> tuple[ToolGateway, ToolRegistry, dict]:
    """Build a small gateway and an enforced-plan state. / 构建小型网关与已执行计划状态。"""
    registry = ToolRegistry.from_tools([safe_probe])
    gateway = ToolGateway(registry)
    state = {
        "messages": [HumanMessage(content="hello")],
        "tool_contract_version": 2,
        "decision_policy_version": 2,
        "decision_enforced": True,
        "decision": {"tool_policy": {"allowed": [], "max_calls": 8}},
    }
    return gateway, registry, state


def _call() -> dict:
    """Return one safe tool call. / 返回一个安全工具调用。"""
    return {"id": "probe-1", "name": "safe_probe", "args": {}}


def test_gateway_rejects_calls_outside_the_enforced_plan():
    """An empty allowlist rejects even a safe tool. / 空允许列表连安全工具也拒绝。"""
    gateway, _, state = _gateway()
    result = gateway.preflight(_call(), state)
    assert result is not None and result.error is not None
    assert result.error.code == "TOOL_NOT_ALLOWED"


def test_gateway_enforces_zero_decision_quota():
    """A zero call quota exhausts the decision budget before execution.
    / 零调用额度在执行前耗尽决策预算。"""
    gateway, _, state = _gateway()
    state["decision"] = {"tool_policy": {"allowed": ["safe_probe"], "max_calls": 0}}
    result = gateway.budget_error(_call(), state)
    assert result is not None and result.error is not None
    assert result.error.code == "BUDGET_EXCEEDED"


def test_old_checkpoints_and_shadow_plans_do_not_restrict_tools():
    """Checkpoints without the policy version and shadow plans stay advisory.
    / 无策略版本的检查点与影子计划仍只是建议。"""
    gateway, _, state = _gateway()
    assert gateway.preflight(_call(), {**state, "decision_policy_version": 1}) is None
    assert gateway.preflight(_call(), {**state, "decision_enforced": False}) is None
