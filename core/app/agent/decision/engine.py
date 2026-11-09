"""Model-driven decision engine with deterministic fallback.
 / 决策模型驱动、确定性回退的决策引擎。"""

import logging

from app.agent.decision.contracts import StepPlan
from app.agent.decision.fallback import rules_plan
from app.agent.decision.guards import DecisionGuards
from app.agent.decision.model import DecisionModelClient
from app.agent.tools.registry import ToolRegistry
from app.config import AgentSettings, DecisionSettings

logger = logging.getLogger(__name__)


class DecisionEngine:
    """Produce one guarded StepPlan per run. / 每轮生成一个经护栏校验的计划。"""

    def __init__(
        self,
        settings: DecisionSettings,
        registry: ToolRegistry,
        agent_settings: AgentSettings | None = None,
        model_client: DecisionModelClient | None = None,
    ):
        """Bind settings, tools and the optional decision model. / 绑定配置、工具与可选决策模型。

        Args:
            settings: Decision-layer mode, endpoint and limits. / 决策层模式、端点与限制。
            registry: Tool metadata/selection registry. / 工具元数据与选择注册表。
            agent_settings: Runtime context defaults. / 运行时上下文默认值。
            model_client: Optional injected client for tests. / 测试可注入的客户端。
        """
        self.settings = settings
        self.registry = registry
        self.agent_settings = agent_settings or AgentSettings()
        self.model = (
            model_client
            if model_client is not None
            else DecisionModelClient(settings)
            if settings.mode != "off"
            else None
        )
        self.guards = DecisionGuards(registry, settings, self.agent_settings)

    def payload(self, state: dict, mode: str) -> dict:
        """Build canonical facts for the decision model. / 构造供决策模型使用的规范化事实。

        Args:
            state: Current graph state. / 当前图状态。
            mode: ``chat`` or ``work``. / 聊天或工作模式。

        Returns:
            JSON-serializable fact payload. / 可 JSON 序列化的事实载荷。
        """
        return {
            "run": {
                "mode": mode,
                "approval_policy": state.get("approval_policy", "ask"),
                "max_steps": self.agent_settings.max_steps,
            },
            "request": {
                "user_input": state.get("user_input", ""),
                "reference_count": len(state.get("references") or []),
                "attachment_count": len(state.get("attachment_ids") or []),
                "identity_question": bool(state.get("identity_question")),
            },
            "model": state.get("model_selection") or {},
            "tools": [
                {
                    "name": descriptor.name,
                    "risk": str(descriptor.risk),
                    "requires_approval": bool(
                        getattr(descriptor, "requires_approval", False)
                    ),
                }
                for descriptor in self.registry.descriptors(mode)
            ],
            "history": {
                "has_summary": bool(state.get("history_summary")),
                "compression_pending": bool(state.get("history_compression_pending")),
            },
        }

    async def decide(self, state: dict, mode: str) -> StepPlan | None:
        """Return a guarded plan, or ``None`` when the layer is off.
        返回经护栏的计划；关闭时为 None。

        Args:
            state: Current graph state. / 当前图状态。
            mode: ``chat`` or ``work``. / 聊天或工作模式。

        Returns:
            Model or rules plan with enforcement flag. / 带执行标记的模型或规则计划。
        """
        if self.settings.mode == "off":
            return None
        plan: StepPlan | None = None
        if self.model is not None and self.model.configured:
            try:
                raw = await self.model.decide(
                    self.payload(state, mode), timeout=self.settings.timeout_seconds
                )
            except Exception:
                logger.warning("Decision model unavailable; using rules", exc_info=False)
                raw = None
            if raw is not None:
                plan = self.guards.apply(raw, state, mode)
        if plan is None:
            plan = rules_plan(state, self.registry, self.settings, self.agent_settings, mode)
            if self.model is not None and self.model.configured:
                # The decision model is configured but unavailable; hand control back to the
                # chat model instead of letting deterministic rules drive the run.
                # 决策模型已配置但不可用：把决策权交回聊天模型，而不是让确定性规则接管。
                plan = plan.model_copy(
                    update={
                        "source": "llm",
                        "reasons": ["LLM_SELF_DRIVEN_FALLBACK"],
                        "rationale": "decision model unavailable; chat model decides",
                    }
                )
        plan.enforced = self.settings.mode == "enforce"
        return plan
