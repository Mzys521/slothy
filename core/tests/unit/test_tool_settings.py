"""工具配置的路径与限制。 / Workspace resolution and tool resource limits."""

from pathlib import Path

import pytest
from pydantic import ValidationError

from app.config import ToolSettings


def test_workspace_resolves_against_backend_instead_of_current_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.chdir(tmp_path)
    settings = ToolSettings(workspace_path="data/artifacts", _env_file=None)
    backend = Path(__file__).resolve().parents[2]
    assert settings.workspace_path == backend / "data" / "artifacts"


def test_tool_environment_and_existing_file_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("TOOLS_WORKSPACE_PATH", str(tmp_path / "artifacts"))
    monkeypatch.setenv("TOOLS_PYTHON_TIMEOUT_SECONDS", "12.5")
    settings = ToolSettings(_env_file=None)
    assert settings.workspace_path == tmp_path / "artifacts"
    assert settings.python_timeout_seconds == 12.5
    target = tmp_path / "file.txt"
    target.write_text("existing", encoding="utf-8")
    with pytest.raises(ValidationError, match="must be a directory"):
        ToolSettings(workspace_path=target, _env_file=None)


@pytest.mark.parametrize(
    "overrides",
    [
        {"workspace_path": " "},
        {"command_timeout_seconds": 0},
        {"command_timeout_seconds": 61},
        {"python_timeout_seconds": float("nan")},
        {"python_timeout_seconds": 121},
        {"max_output_chars": 255},
        {"max_code_chars": 100001},
        {"max_artifact_bytes": 0},
        {"max_artifact_files": 101},
    ],
)
def test_invalid_tool_budgets_are_rejected(overrides: dict):
    with pytest.raises(ValidationError):
        ToolSettings(**overrides, _env_file=None)
