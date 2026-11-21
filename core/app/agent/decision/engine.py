"""Model-driven decision engine with deterministic fallback.
 / 决策模型驱动、确定性回退的决策引擎。"""

import logging

from app.agent.decision.contracts import DecisionOutput, StepPlan
from app.agent.decision.fallback import fallback_plan, rules_plan
from app.agent.decision.guards import DecisionGuards
from app.agent.decision.model import DecisionModelClient, DecisionModelError
from app.agent.providers import ModelProvider
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
        provider: ModelProvider | None = None,
    ):
        """Bind settings, tools and the optional decision model. / 绑定配置、工具与可选决策模型。

        Args:
            settings: Decision-layer mode, endpoint and limits. / 决策层模式、端点与限制。
            registry: Tool metadata/selection registry. / 工具元数据与选择注册表。
            agent_settings: Runtime context defaults. / 运行时上下文默认值。
            model_client: Optional injected client for tests. / 测试可注入的客户端。
            provider: Optional selected-model adapter shared with the chat runner.
                中文：可选的、与聊天运行器共用的所选模型适配器。
        """
        self.settings = settings
        self.registry = registry
        self.agent_settings = agent_settings or AgentSettings()
        self.model = (
            model_client
            if model_client is not None
            else DecisionModelClient(settings, provider)
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

    @staticmethod
    def _fallback_reason(error: BaseException | None) -> tuple[str, str]:
        """Map a decision failure to one stable reason code and explanation.
        / 把决策失败映射为稳定原因码与说明。

        Args:
            error: Raised failure, or ``None`` for an empty/invalid result. / 抛出的异常；空或
                非法结果时为 ``None``。

        Returns:
            A reason code and a short rationale. / 原因码与简短说明。
        """
        code = str(getattr(error, "reason", "") if error is not None else "")
        if isinstance(error, TimeoutError) or code == "timeout":
            return (
                "FALLBACK_MODEL_UNAVAILABLE",
                "decision model timed out; chat model decides",
            )
        if code in {"invalid_json", "invalid_structure", "empty"}:
            return (
                "FALLBACK_INVALID_OUTPUT",
                f"decision output invalid ({code}); chat model decides",
            )
        if code:
            return (
                "FALLBACK_MODEL_UNAVAILABLE",
                f"decision model unavailable ({code}); chat model decides",
            )
        return (
            "FALLBACK_MODEL_UNAVAILABLE",
            "decision model unavailable; chat model decides",
        )

    def _fallback(
        self, state: dict, mode: str, error: BaseException | None = None
    ) -> StepPlan:
        """Build the autonomous fallback plan for one failure. / 为一次失败构建自治回退计划。

        Args:
            state: Current graph state. / 当前图状态。
            mode: ``chat`` or ``work``. / 聊天或工作模式。
            error: Failure that prevented a model plan. / 阻止模型计划的失败。

        Returns:
            A plan that restores chat-model autonomy. / 恢复聊天模型自主权的计划。
        """
        reason, rationale = self._fallback_reason(error)
        return fallback_plan(
            state,
            self.registry,
            self.settings,
            self.agent_settings,
            mode,
            reason=reason,
            rationale=rationale,
        )

    async def decide(self, state: dict, mode: str) -> StepPlan | None:
        """Return a guarded plan, or ``None`` when the layer is off.
        返回经护栏的计划；关闭时为 None。

        A configured decision model that times out, fails or returns an invalid structure
        produces an explicit ``fallback`` plan: the chat model keeps autonomy instead of the
        deterministic rules taking over. / 已配置的决策模型超时、失败或结构非法时生成明确的
        ``fallback`` 计划：聊天模型保持自主，而不是让确定性规则接管。

        Args:
            state: Current graph state. / 当前图状态。
            mode: ``chat`` or ``work``. / 聊天或工作模式。

        Returns:
            Model, rules or fallback plan with enforcement flag. / 带执行标记的模型、规则或
            回退计划。
        """
        if self.settings.mode == "off":
            return None
        plan: StepPlan | None = None
        if self.model is not None and self.model.configured:
            try:
                raw = await self.model.decide(
                    self.payload(state, mode), timeout=self.settings.timeout_seconds
                )
            except Exception as error:
                code = getattr(error, "reason", type(error).__name__)
                logger.warning(
                    "Decision model unavailable (%s); chat model decides", code, exc_info=False
                )
                plan = self._fallback(state, mode, error)
            else:
                try:
                    output = (
                        raw
                        if isinstance(raw, DecisionOutput)
                        else DecisionOutput.model_validate(raw)
                    )
                except (TypeError, ValueError) as error:
                    logger.warning(
                        "Decision model returned an invalid plan; chat model decides",
                        exc_info=False,
                    )
                    plan = self._fallback(
                        state,
                        mode,
                        DecisionModelError(
                            "invalid_structure",
                            "decision reply does not match the plan schema",
                        )
                        if not getattr(error, "reason", None)
                        else error,
                    )
                else:
                    plan = self.guards.apply(output, state, mode)
        if plan is None:
            # No decision model is configured: keep the deterministic pre-decision baseline.
            # 未配置决策模型：保持引入决策层之前的确定性基线。
            plan = rules_plan(state, self.registry, self.settings, self.agent_settings, mode)
        plan.enforced = self.settings.mode == "enforce"
        return plan
