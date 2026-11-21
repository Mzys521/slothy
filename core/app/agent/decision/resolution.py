"""Resolve the decision model to the adapter the chat runner actually uses.
 / 将决策模型解析为聊天运行器实际使用的适配器。

One resolver feeds availability in the settings API and the executable engine, so a model
shown as available is the same protocol adapter a run will call. / 同一个解析函数同时供设置 API
的可用性判断与可执行引擎使用，因此显示可用的模型就是运行实际会调用的协议适配器。
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from app.agent.decision.model import environment_provider
from app.agent.decision.settings_store import DecisionSelection
from app.agent.providers import ModelProvider
from app.config import DecisionSettings

# Where the effective settings came from. / 有效配置的来源。
ResolutionSource = Literal["off", "explicit-off", "environment", "catalog"]


@dataclass(frozen=True)
class DecisionResolution:
    """Effective decision settings plus the provider that will execute them.
    / 有效决策配置及其执行适配器。

    Attributes:
        settings: Mode, model identifier and limits. / 模式、模型标识与限制。
        provider: Adapter reused from the selected catalog model or the environment. /
            复用自所选目录模型或环境的适配器。
        selected_model: Catalog model identifier, or ``None`` for environment/off. /
            目录模型标识；环境或关闭时为 ``None``。
        source: Which configuration source produced this resolution. / 产生该解析的配置来源。
        reason: Stable explanation when no decision model can run. / 无法运行决策模型时的稳定原因。
    """

    settings: DecisionSettings
    provider: ModelProvider | None = None
    selected_model: str | None = None
    source: ResolutionSource = "off"
    reason: str | None = None

    @property
    def available(self) -> bool:
        """Report whether a run may call the decision model. / 运行是否可调用决策模型。"""
        return self.settings.mode != "off" and self.provider is not None

    @property
    def mode(self) -> str:
        """Return the effective mode for API reporting. / 返回供 API 展示的有效模式。"""
        return self.settings.mode


def disabled_resolution(
    reason: str = "DECISION_DISABLED", source: ResolutionSource = "off"
) -> DecisionResolution:
    """Build a resolution that keeps the chat model autonomous. / 构建保持聊天模型自主的解析。

    Args:
        reason: Stable reason code. / 稳定原因码。
        source: Configuration source that disabled the layer. / 关闭决策层的配置来源。

    Returns:
        A disabled resolution without a provider. / 不含适配器的关闭解析。
    """
    return DecisionResolution(
        settings=DecisionSettings(_env_file=None, mode="off"),
        provider=None,
        selected_model=None,
        source=source,
        reason=reason,
    )


def resolve_decision_settings(
    selection: DecisionSelection,
    *,
    desktop: bool,
    environment: DecisionSettings,
    provider_for: Callable[[str], ModelProvider | None],
) -> DecisionResolution:
    """Resolve the store selection, desktop isolation and environment fallbacks.
    / 解析存储选择、桌面隔离与环境回退。

    Desktop keeps its isolated ``decision.json`` source: never configured means off. An
    independent backend with no saved decision loads ``DecisionSettings()`` environment
    values, while an explicitly saved ``null`` stays off. A selected catalog model uses the
    same provider factory as ``runner_for()``. / 桌面沿用隔离的 ``decision.json`` 来源：从未
    配置即关闭。独立后端没有保存决策时加载 ``DecisionSettings()`` 环境值；显式保存的
    ``null`` 保持关闭。选中的目录模型使用与 ``runner_for()`` 相同的适配器工厂。

    Args:
        selection: Saved model selection and whether it was configured. / 保存的模型选择及
            是否配置过。
        desktop: Whether this process is the isolated desktop shell. / 是否为隔离的桌面进程。
        environment: Validated environment defaults. / 已校验的环境默认值。
        provider_for: Factory returning the selected model's existing adapter, or ``None``
            when it is unavailable. / 返回所选模型既有适配器（不可用时为 None）的工厂。

    Returns:
        The effective settings and provider. / 有效配置与适配器。
    """
    if desktop:
        if not selection.configured:
            return disabled_resolution("DECISION_UNCONFIGURED", "off")
        if selection.model is None:
            return disabled_resolution("DECISION_DISABLED", "explicit-off")
        return _catalog(selection.model, provider_for)
    if not selection.configured:
        return _environment(environment)
    if selection.model is None:
        return disabled_resolution("DECISION_DISABLED", "explicit-off")
    # A saved catalog selection enables enforcement; ``DECISION_MODE`` applies only when the
    # environment itself is the configuration source. / 已保存的目录选择启用执行；环境模式只在
    # 环境本身作为配置来源时生效。
    return _catalog(selection.model, provider_for)


def _catalog(
    model: str,
    provider_for: Callable[[str], ModelProvider | None],
) -> DecisionResolution:
    """Resolve one selected catalog model to its existing adapter. / 把所选目录模型解析为适配器。

    A saved catalog selection enables ``enforce``; the settings API contract says a chosen
    model drives routing. ``DECISION_MODE`` applies only when the environment is the
    configuration source. / 已保存的目录选择启用 ``enforce``；设置 API 约定所选模型驱动路由。
    ``DECISION_MODE`` 只在环境本身作为配置来源时生效。

    Args:
        model: Catalog model identifier. / 目录模型标识。
        provider_for: Adapter factory. / 适配器工厂。

    Returns:
        The catalog resolution; unavailable models degrade to off with a reason. /
        目录解析；模型不可用时降级为关闭并记录原因。
    """
    try:
        provider = provider_for(model)
    except Exception:
        # Availability reporting must never fail the settings API. / 可用性报告不能让设置 API 失败。
        provider = None
    if provider is None:
        return DecisionResolution(
            settings=DecisionSettings(_env_file=None, mode="off", model=model),
            provider=None,
            selected_model=model,
            source="catalog",
            reason="DECISION_MODEL_UNAVAILABLE",
        )
    return DecisionResolution(
        settings=DecisionSettings(_env_file=None, mode="enforce", model=model),
        provider=provider,
        selected_model=model,
        source="catalog",
    )


def _environment(environment: DecisionSettings) -> DecisionResolution:
    """Resolve environment-configured decisions for an independent backend.
    / 为独立后端解析环境配置的决策模型。

    Args:
        environment: Validated environment defaults. / 已校验的环境默认值。

    Returns:
        The environment resolution. / 环境解析。
    """
    if environment.mode == "off":
        return DecisionResolution(
            settings=environment,
            provider=None,
            selected_model=None,
            source="environment",
            reason="DECISION_DISABLED",
        )
    provider = environment_provider(environment)
    if provider is None:
        return DecisionResolution(
            settings=environment,
            provider=None,
            selected_model=None,
            source="environment",
            reason="DECISION_CREDENTIALS_MISSING",
        )
    return DecisionResolution(
        settings=environment,
        provider=provider,
        selected_model=None,
        source="environment",
    )
