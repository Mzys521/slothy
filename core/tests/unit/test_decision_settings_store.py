"""Decision model selection persistence tests. / 决策模型选择持久化测试。"""

from app.agent.decision.settings_store import DecisionConfigStore, DecisionSelection


def test_decision_selection_round_trip(tmp_path):
    """The selected model survives a reload and can be cleared. / 选择可重载与清除。"""
    path = tmp_path / "decision.json"
    store = DecisionConfigStore(path)
    assert store.model() is None
    store.save("deepseek/deepseek-flash")
    assert DecisionConfigStore(path).model() == "deepseek/deepseek-flash"
    store.save(None)
    assert DecisionConfigStore(path).model() is None


def test_store_distinguishes_never_configured_from_explicit_off(tmp_path):
    """A missing file is unconfigured; a saved null is an explicit off switch.
    / 缺失文件表示未配置；已保存的 null 表示显式关闭。"""
    path = tmp_path / "decision.json"
    unconfigured = DecisionConfigStore(path)
    assert unconfigured.selection() == DecisionSelection(model=None, configured=False)
    assert unconfigured.configured() is False

    unconfigured.save(None)
    explicit = DecisionConfigStore(path)
    assert explicit.selection() == DecisionSelection(model=None, configured=True)
    assert explicit.configured() is True
    assert explicit.model() is None
