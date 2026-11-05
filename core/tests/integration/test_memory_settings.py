"""Persistent planner configuration without implying an implemented planner runtime."""

from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.api import create_app
from app.config import CheckpointSettings, MemorySettings
from app.personalization.settings import MemoryConfiguration

PATH = "/api/v1/settings/memory"


def application(tmp_path):
    return create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(
            get_chat_model=lambda: FakeListChatModel(responses=["Ready"])
        ),
    )


def test_configuration_roundtrip_conflict_restart_and_legacy_gate(tmp_path):
    app = application(tmp_path)
    configuration = {
        "mode": "shadow",
        "model": " provider/model ",
        "batch_size": 12,
        "idle_seconds": 75,
        "consolidate_every": 25,
        "forget_interval_hours": 12,
        "active_limit": 800,
        "auto_confirm_score": 0.9,
        "observed_min_threads": 3,
        "episodic_ttl_days": 45,
        "fact_ttl_days": 200,
        "retention_days": 120,
        "daily_token_budget": 0,
    }
    with TestClient(app) as client:
        initial = client.get(PATH).json()
        assert initial["revision"] == 0
        assert initial["configuration"] == initial["defaults"]
        assert len(initial["configuration"]) == 13
        assert initial["runtime"] == {
            "engine": "rule_review",
            "planner_available": False,
            "personalization_enabled": True,
        }
        saved = client.patch(PATH, json={"expected_revision": 0, "configuration": configuration})
        assert saved.status_code == 200
        configuration["model"] = "provider/model"
        assert saved.json()["configuration"] == configuration
        assert saved.json()["revision"] == 1
        conflict = client.patch(
            PATH, json={"expected_revision": 0, "configuration": initial["defaults"]}
        )
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "VERSION_CONFLICT"
        assert conflict.json()["request_id"]
        assert client.get(PATH).json()["configuration"] == configuration
        # Preconfiguration must not silently change existing personalization or data.
        assert client.get("/api/v1/settings").json() == {"personalization_enabled": True}
        assert client.get("/api/v1/profile").json()["version"] == 0
        assert client.get("/api/v1/memories").json()["items"] == []
        client.patch("/api/v1/settings", json={"personalization_enabled": False})
        assert not client.get(PATH).json()["runtime"]["personalization_enabled"]
    with TestClient(application(tmp_path)) as client:
        current = client.get(PATH).json()
        assert current["configuration"] == configuration
        assert current["revision"] == 1
        assert not current["runtime"]["personalization_enabled"]
        reset = client.patch(
            PATH, json={"expected_revision": 1, "configuration": current["defaults"]}
        )
        assert reset.status_code == 200
        assert reset.json()["revision"] == 2
        assert reset.json()["configuration"] == initial["defaults"]


@pytest.mark.parametrize(
    "change",
    [
        {"batch_size": 0},
        {"batch_size": True},
        {"idle_seconds": 1.5},
        {"auto_confirm_score": 1.1},
        {"retention_days": -1},
        {"daily_token_budget": 10000001},
        {"mode": "unrecognized"},
        {"unexpected": "value"},
    ],
)
def test_invalid_configuration_is_not_written(tmp_path, change):
    with TestClient(application(tmp_path)) as client:
        initial = client.get(PATH).json()
        response = client.patch(
            PATH,
            json={
                "expected_revision": 0,
                "configuration": {**initial["configuration"], **change},
            },
        )
        assert response.status_code == 422
        assert response.json()["code"] == "VALIDATION_ERROR"
        assert client.get(PATH).json()["revision"] == 0


def test_environment_defaults_and_user_override(tmp_path, monkeypatch):
    monkeypatch.setenv("MEMORY_BATCH_SIZE", "13")
    monkeypatch.setenv("MEMORY_RETENTION_DAYS", "180")
    monkeypatch.setenv("MEMORY_MODEL", " configured/model ")
    assert MemorySettings(_env_file=None).batch_size == 13
    with TestClient(application(tmp_path)) as client:
        initial = client.get(PATH).json()
        assert initial["defaults"]["batch_size"] == 13
        assert initial["defaults"]["retention_days"] == 180
        assert initial["defaults"]["model"] == "configured/model"
        configuration = {**initial["configuration"], "batch_size": 9, "model": ""}
        assert (
            client.patch(
                PATH, json={"expected_revision": 0, "configuration": configuration}
            ).status_code
            == 200
        )
    monkeypatch.setenv("MEMORY_BATCH_SIZE", "15")
    with TestClient(application(tmp_path)) as client:
        current = client.get(PATH).json()
        assert current["configuration"]["batch_size"] == 9
        assert current["configuration"]["model"] is None
        assert current["defaults"]["batch_size"] == 15
        schema = client.get("/openapi.json").json()["components"]["schemas"]
        assert schema["MemoryConfiguration"]["additionalProperties"] is False
        assert set(schema["MemoryConfiguration"]["properties"]) == set(
            MemoryConfiguration.model_fields
        )
