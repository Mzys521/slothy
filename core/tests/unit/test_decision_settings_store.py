"""Decision model selection persistence tests. / 决策模型选择持久化测试。"""

from app.agent.decision.settings_store import DecisionConfigStore


def test_decision_selection_round_trip(tmp_path):
    """The selected model survives a reload and can be cleared. / 选择可重载与清除。"""
    path = tmp_path / "decision.json"
    store = DecisionConfigStore(path)
    assert store.model() is None
    store.save("deepseek/deepseek-flash")
    assert DecisionConfigStore(path).model() == "deepseek/deepseek-flash"
    store.save(None)
    assert DecisionConfigStore(path).model() is None
