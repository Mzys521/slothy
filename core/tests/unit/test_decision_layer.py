"""Decision-layer contracts, guards and engine tests. / 决策层契约、护栏与引擎测试。"""

from app.agent.decision.contracts import DecisionOutput
from app.agent.decision.engine import DecisionEngine
from app.agent.decision.fallback import rules_plan
from app.agent.decision.guards import DecisionGuards
from app.agent.decision.model import _parse_json
from app.agent.tools.registry import ToolRegistry
from app.config import DecisionSettings


def test_decision_settings_default_to_off(monkeypatch):
    """The layer is opt-in so existing behavior and tests never call an endpoint.
    / 决策层默认关闭，现有行为与测试不会调用外部端点。"""
    monkeypatch.setenv("DECISION_MODE", "off")
    settings = DecisionSettings(_env_file=None)
    assert settings.mode == "off"
    assert settings.timeout_seconds > 0


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


async def test_disabled_engine_returns_none():
    """Off mode performs no decision work. / 关闭模式不做决策工作。"""
    engine = DecisionEngine(DecisionSettings(_env_file=None, mode="off"), ToolRegistry())
    assert await engine.decide({"user_input": "hello"}, "chat") is None


def test_parse_json_accepts_fenced_reply():
    """Fenced JSON replies are accepted. / 支持被代码围栏包裹的 JSON 回复。"""
    assert _parse_json('```json\n{"route":"model"}\n```') == {"route": "model"}
