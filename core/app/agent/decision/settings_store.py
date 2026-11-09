"""Persisted decision-model selection without credentials. / 决策模型选择持久化（不含凭据）。"""

import json
from pathlib import Path


class DecisionConfigStore:
    """Store only the selected catalog model ID in decision.json.
    / 仅在 decision.json 保存模型 ID。"""

    def __init__(self, path: Path):
        """Load the selection lazily. / 延迟加载选择。"""
        self.path = path
        self._model: str | None = None
        self._loaded = False

    def model(self) -> str | None:
        """Return the selected model ID, if any. / 返回已选决策模型 ID。"""
        if not self._loaded:
            self._loaded = True
            if self.path.exists():
                try:
                    value = json.loads(self.path.read_text(encoding="utf-8")).get("model")
                except (ValueError, OSError):
                    value = None
                self._model = value if isinstance(value, str) and value.strip() else None
        return self._model

    def save(self, model: str | None) -> None:
        """Persist the selected model ID or clear it. / 保存或清除决策模型 ID。"""
        self._model = model.strip() if isinstance(model, str) and model.strip() else None
        self._loaded = True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(".tmp")
        temporary.write_text(
            json.dumps({"model": self._model}, ensure_ascii=False), encoding="utf-8"
        )
        temporary.replace(self.path)
