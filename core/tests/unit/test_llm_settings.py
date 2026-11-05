"""Configuration contracts independent of any model API."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import AgentSettings, CheckpointSettings, LLMSettings


@pytest.mark.parametrize(
    ("provider", "expected_url"),
    [
        ("deepseek", "https://api.deepseek.com"),
        ("mimo", "https://api.xiaomimimo.com/v1"),
    ],
)
def test_named_providers_supply_endpoints(provider: str, expected_url: str) -> None:
    settings = LLMSettings(
        provider=provider, model="configured-model", api_key="test-key", _env_file=None
    )
    assert settings.resolved_base_url == expected_url


def test_custom_endpoint_overrides_preset() -> None:
    settings = LLMSettings(
        provider="mimo",
        model="custom-deployment",
        api_key="test-key",
        base_url="http://localhost:8000/custom/v1/",
        _env_file=None,
    )
    assert settings.resolved_base_url == "http://localhost:8000/custom/v1"


def test_environment_overrides_dotenv(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "LLM_PROVIDER=deepseek\nLLM_MODEL=file-model\nLLM_API_KEY=file-test-key\n"
        'LLM_EXTRA_BODY={"thinking":{"type":"disabled"}}\n'
        "UNRELATED_APP_SETTING=ignored\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LLM_MODEL", "env-model")
    monkeypatch.setenv("LLM_TIMEOUT_SECONDS", "15")
    settings = LLMSettings(_env_file=env_file)
    assert settings.model == "env-model"
    assert settings.timeout_seconds == 15
    assert settings.api_key.get_secret_value() == "file-test-key"
    assert settings.extra_body == {"thinking": {"type": "disabled"}}


@pytest.mark.parametrize(
    "overrides",
    [
        {"provider": "unsupported"},
        {"provider": "openai_compatible"},
        {"model": "   "},
        {"api_key": "   "},
        {"base_url": "not-a-url"},
        {"timeout_seconds": 0},
        {"timeout_seconds": float("inf")},
        {"max_retries": -1},
        {"temperature": 3},
        {"max_tokens": 0},
        {"extra_body": {"messages": []}},
        {"extra_body": {"max_tokens": 100}},
    ],
)
def test_invalid_configuration_fails_early(overrides: dict) -> None:
    values = {"provider": "deepseek", "model": "configured-model", "api_key": "test-key"}
    values.update(overrides)
    with pytest.raises(ValidationError):
        LLMSettings(**values, _env_file=None)


def test_credentials_are_required_and_not_exposed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "unrelated-openai-test-key")
    with pytest.raises(ValidationError, match="api_key"):
        LLMSettings(provider="deepseek", model="configured-model", _env_file=None)

    settings = LLMSettings(
        provider="deepseek", model="configured-model", api_key="private-test-key", _env_file=None
    )
    assert "private-test-key" not in repr(settings)
    assert "api_key" not in settings.model_dump()
    with pytest.raises(ValidationError) as error:
        LLMSettings(provider="deepseek", model="", api_key="private-test-key", _env_file=None)
    assert "private-test-key" not in str(error.value)


@pytest.mark.parametrize(
    ("provider", "prefix", "endpoint_variable"),
    [
        ("deepseek", "DEEPSEEK", "DEEPSEEK_API_URL"),
        ("mimo", "MIMO", "MIMO_API_BASE"),
        ("openai_compatible", "OPENAI", "OPENAI_API_BASE"),
    ],
)
def test_provider_selection_uses_one_complete_namespace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    provider: str,
    prefix: str,
    endpoint_variable: str,
) -> None:
    env_file = tmp_path / ".env"
    values = {}
    for vendor, endpoint_name in (
        ("DEEPSEEK", "DEEPSEEK_API_URL"),
        ("MIMO", "MIMO_API_BASE"),
        ("OPENAI", "OPENAI_API_BASE"),
    ):
        values.update(
            {
                f"{vendor}_API_KEY": f"{vendor}-test-key",
                f"{vendor}_MODEL": f"{vendor}-model",
                endpoint_name: f"https://{vendor.lower()}.example.test/v1",
            }
        )
    values["LLM_PROVIDER"] = "deepseek"
    env_file.write_text(
        "\n".join(f"{key}={value}" for key, value in values.items()), encoding="utf-8"
    )
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv(f"{prefix}_MODEL", "environment-model")

    settings = LLMSettings(_env_file=env_file)
    assert settings.provider == provider
    assert settings.model == "environment-model"
    assert settings.api_key.get_secret_value() == f"{prefix}-test-key"
    assert settings.resolved_base_url == values[endpoint_variable]


def test_explicit_provider_and_generic_overrides_win(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "mimo")
    monkeypatch.setenv("MIMO_API_KEY", "mimo-test-key")
    monkeypatch.setenv("MIMO_MODEL", "mimo-model")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-model")
    monkeypatch.setenv("LLM_MODEL", "generic-model")

    selected = LLMSettings(provider="deepseek", _env_file=None)
    assert selected.model == "generic-model"
    assert selected.api_key.get_secret_value() == "deepseek-test-key"
    explicit = LLMSettings(provider="deepseek", model="explicit-model", _env_file=None)
    assert explicit.model == "explicit-model"


def test_missing_selected_credentials_never_borrow_another_vendor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")
    monkeypatch.setenv("MIMO_MODEL", "mimo-model")
    with pytest.raises(ValidationError, match="api_key"):
        LLMSettings(provider="mimo", _env_file=None)


def test_root_and_backend_dotenv_precedence_is_independent_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root_env = tmp_path / ".env"
    backend_env = tmp_path / "core" / ".env"
    backend_env.parent.mkdir()
    root_env.write_text(
        "LLM_PROVIDER=mimo\nMIMO_API_KEY=root-test-key\nMIMO_MODEL=root-model\n",
        encoding="utf-8",
    )
    backend_env.write_text("MIMO_MODEL=backend-model\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path.parent)
    from_files = LLMSettings(_env_file=(root_env, backend_env))
    assert from_files.model == "backend-model"
    assert from_files.api_key.get_secret_value() == "root-test-key"
    monkeypatch.setenv("MIMO_MODEL", "environment-model")
    assert LLMSettings(_env_file=(root_env, backend_env)).model == "environment-model"


def test_agent_parameters_accept_environment_and_explicit_names(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_MAX_STEPS", "12")
    monkeypatch.setenv("PERSONALIZATION_ENABLED", "false")
    monkeypatch.setenv("MEMORY_TOP_K", "4")
    monkeypatch.setenv("MEMORY_CONTEXT_TOKEN_BUDGET", "600")
    settings = AgentSettings(_env_file=None)
    assert settings.max_steps == 12
    assert settings.personalization_enabled is False
    assert settings.memory_top_k == 4
    assert settings.memory_context_token_budget == 600
    assert (
        AgentSettings(personalization_enabled=True, _env_file=None).personalization_enabled is True
    )


@pytest.mark.parametrize(
    "invalid",
    [
        {"max_steps": 0},
        {"memory_top_k": 0},
        {"memory_context_token_budget": -1},
        {"context_read_timeout_seconds": 0},
    ],
)
def test_agent_budgets_must_be_positive(invalid: dict) -> None:
    with pytest.raises(ValidationError):
        AgentSettings(**invalid, _env_file=None)


def test_sqlite_settings_resolve_paths_independently_of_cwd(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    expected = Path(__file__).resolve().parents[2] / "data" / "checkpoints.sqlite"
    assert CheckpointSettings(_env_file=None).sqlite_path == expected
    monkeypatch.setenv("CHECKPOINT_SQLITE_PATH", str(tmp_path / "custom.sqlite"))
    monkeypatch.setenv("CHECKPOINT_BUSY_TIMEOUT_MS", "2500")
    settings = CheckpointSettings(_env_file=None)
    assert settings.sqlite_path == tmp_path / "custom.sqlite"
    assert settings.busy_timeout_ms == 2500


@pytest.mark.parametrize(
    "invalid",
    [
        {"sqlite_path": ""},
        {"sqlite_path": ":memory:"},
        {"sqlite_path": "sqlite:///x.db"},
        {"busy_timeout_ms": 0},
        {"history_max_turns": 0},
    ],
)
def test_persistence_and_history_parameters_reject_invalid_values(invalid: dict) -> None:
    with pytest.raises(ValueError):
        if "history_max_turns" in invalid:
            AgentSettings(**invalid, _env_file=None)
        else:
            CheckpointSettings(**invalid, _env_file=None)
