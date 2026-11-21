"""Persisted decision-model selection without credentials. / 决策模型选择持久化（不含凭据）。"""

import json
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class DecisionSelection:
    """Whether a decision was ever saved and which model it names. / 是否保存过决策及模型名。

    ``configured`` distinguishes a store that was never touched from an explicit ``null``:
    the former lets an independent backend use environment values, the latter means the user
    turned the decision model off. / ``configured`` 区分从未配置与显式关闭：前者允许独立后端
    使用环境变量，后者表示用户已关闭决策模型。
    """

    model: str | None
    configured: bool


class DecisionConfigStore:
    """Store only the selected catalog model ID in decision.json.
    / 仅在 decision.json 保存模型 ID。"""

    def __init__(self, path: Path):
        """Load the selection lazily. / 延迟加载选择。"""
        self.path = path
        self._selection: DecisionSelection | None = None

    def selection(self) -> DecisionSelection:
        """Return the saved selection and whether it was configured. / 返回保存的选择及是否配置过。

        Returns:
            A selection whose ``model`` is ``None`` for both "never configured" and "explicit
            off"; ``configured`` separates them. / ``model`` 对从未配置与显式关闭都为
            ``None``，``configured`` 区分两种情况。
        """
        if self._selection is None:
            model: str | None = None
            configured = False
            if self.path.exists():
                configured = True
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8")).get("model")
                except (ValueError, OSError):
                    value = None
                model = value if isinstance(value, str) and value.strip() else None
            self._selection = DecisionSelection(model=model, configured=configured)
        return self._selection

    def configured(self) -> bool:
        """Report whether a decision was saved, including an explicit ``null``. /
        是否保存过决策（包括显式 null）。"""
        return self.selection().configured

    def model(self) -> str | None:
        """Return the selected model ID, if any. / 返回已选决策模型 ID。

        ``None`` covers both "never configured" and "explicitly off"; call :meth:`selection`
        or :meth:`configured` when the difference matters. / ``None`` 同时表示从未配置与显式
        关闭；需要区分时调用 :meth:`selection` 或 :meth:`configured`。
        """
        return self.selection().model

    def save(self, model: str | None) -> None:
        """Persist the selected model ID or clear it. / 保存或清除决策模型 ID。"""
        selected = model.strip() if isinstance(model, str) and model.strip() else None
        self._selection = DecisionSelection(model=selected, configured=True)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"model": selected}, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.path)
