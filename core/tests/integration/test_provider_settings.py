"""Production configuration and a real SDK call against an isolated HTTP provider."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from fastapi.testclient import TestClient

from app.api import ChatInput, create_app
from app.config import CheckpointSettings
from app.providers import ProviderInput, ProviderStore


@pytest.fixture
def upstream():
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):
            assert self.headers.get("Authorization") == "Bearer private-config-key"
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"data": [{"id": "connected-model"}]}).encode())

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            requests.append({"body": body, "key": self.headers.get("Authorization")})
            response = {
                "id": "chatcmpl-configured",
                "object": "chat.completion",
                "created": 1,
                "model": "connected-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Connected."},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {"prompt_tokens": 11, "completion_tokens": 2, "total_tokens": 13},
            }
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode())

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}/v1", requests
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


def config(url="https://api.example.com/v1"):
    return {
        "id": "my-model",
        "name": "My provider",
        "template": None,
        "protocol": "openai",
        "base_url": url,
        "api_key": "private-config-key",
        "models": [{"id": "connected-model"}],
    }


def test_namespaced_model_identifier_fits_the_public_chat_contract():
    identifier = "p" * 48 + "/" + "m" * 128
    assert ChatInput(message="Hello", model=identifier).model == identifier


def test_first_run_configuration_routes_to_real_sdk_and_persists(tmp_path, monkeypatch, upstream):
    monkeypatch.setenv("SLOTHY_DESKTOP", "1")
    settings = CheckpointSettings(sqlite_path=tmp_path / "checkpoints.sqlite")
    url, requests = upstream
    with TestClient(create_app(checkpoint_settings=settings)) as client:
        base = "/api/v1/settings/providers"
        assert client.get(base).json()["setup_required"]
        assert client.get("/api/v1/models").json() == {"items": [], "default": None}
        assert client.get("/docs").status_code == 404
        assert client.get("/openapi.json").status_code == 404
        discovered = client.post(base + "/discover", json=config(url))
        assert discovered.json()["items"][0]["id"] == "connected-model"
        saved = client.post(base, json=config(url))
        assert saved.status_code == 201, saved.text
        assert saved.json()["has_api_key"] and "api_key" not in saved.json()
        assert "private-config-key" not in client.get(base).text
        assert not client.get(base).json()["setup_required"]
        catalog = client.get("/api/v1/models").json()
        assert catalog["default"] == "my-model/connected-model"
        assert catalog["items"][0]["display_name"] == "connected-model"
        reply = client.post("/api/v1/chat", json={"message": "Hello", "model": catalog["default"]})
        assert reply.status_code == 200, reply.text
        assert reply.json()["response"] == "Connected."
        assert requests[0]["key"] == "Bearer private-config-key"
        assert requests[0]["body"]["model"] == "connected-model"
        assert requests[0]["body"]["tools"]
        stored = (tmp_path / "providers.json").read_text(encoding="utf-8")
        assert "private-config-key" not in stored
    with TestClient(create_app(checkpoint_settings=settings)) as client:
        assert client.get(base).json()["items"][0]["has_api_key"]
        assert not client.get(base).json()["setup_required"]
        patch = {**config(url), "name": "Renamed"}
        del patch["api_key"]
        assert client.put(base + "/my-model", json=patch).json()["name"] == "Renamed"
        assert client.get(base).json()["items"][0]["has_api_key"]
        assert client.delete(base + "/my-model").status_code == 204
        assert client.get(base).json()["setup_required"]
        assert client.get("/api/v1/threads").json()[
            "items"
        ]  # Configuration deletion keeps user data.


def test_provider_validation_conflicts_and_empty_models(tmp_path):
    with TestClient(
        create_app(checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "api.sqlite"))
    ) as client:
        base = "/api/v1/settings/providers"
        no_key = config()
        del no_key["api_key"]
        assert client.post(base, json=no_key).status_code == 422
        assert client.post(base, json={**config(), "id": "INVALID ID"}).status_code == 422
        assert (
            client.post(
                base, json={**config(), "base_url": "https://secret:password@example.com"}
            ).status_code
            == 422
        )
        assert client.post(base, json={**config(), "models": []}).status_code == 201
        assert client.get(base).json()["setup_required"]
        assert client.post(base, json=config()).status_code == 409
        assert client.put(base + "/missing", json=config()).status_code == 404
        assert client.delete(base + "/missing").status_code == 404


def test_desktop_token_and_no_environment_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SLOTHY_DESKTOP", "1")
    monkeypatch.setenv("SLOTHY_API_TOKEN", "local-service-token")
    monkeypatch.setenv("LLM_API_KEY", "do-not-use-environment-key")
    monkeypatch.setenv("LLM_MODEL", "do-not-use-environment-model")
    app = create_app(checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "api.sqlite"))
    with TestClient(app) as client:
        response = client.get("/api/v1/settings/providers")
        assert response.status_code == 401 and response.json()["code"] == "UNAUTHORIZED"
        assert set(response.json()) == {"code", "message", "request_id"}
        client.headers["X-Slothy-Token"] = "local-service-token"
        assert client.get("/api/v1/settings/providers").json()["setup_required"]
        assert client.get("/api/v1/threads").json()["items"] == []
        assert client.get("/api/v1/memories").json()["items"] == []
        assert client.get("/api/v1/models").json()["items"] == []


def test_anthropic_configuration_builds_native_protocol(tmp_path):
    store = ProviderStore(tmp_path / "providers.json")
    store.save(ProviderInput(**{**config("https://api.anthropic.com"), "protocol": "anthropic"}))
    _item, connection = next(iter(store.catalog().values()))
    model = connection.build_provider(None).get_chat_model()
    assert model.__class__.__name__ == "ChatAnthropic"
    assert model.model == "connected-model"
    assert model.anthropic_api_key.get_secret_value() == "private-config-key"
