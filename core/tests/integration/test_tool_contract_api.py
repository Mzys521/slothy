from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


class Model(FakeMessagesListChatModel):
    def bind_tools(self, tools, **kwargs):
        return self


def test_review_metadata_metrics_archive_account_and_owned_artifact_download(tmp_path, monkeypatch):
    clock = {"now": 100.0}
    monkeypatch.setattr("app.agent.graph.review.time", lambda: clock["now"])
    proposal = AIMessage(
        content="",
        tool_calls=[
            {
                "name": "export_document",
                "id": "export",
                "args": {"format": "txt", "filename": "report.txt", "title": "A report"},
            }
        ],
    )
    model = Model(
        responses=[
            proposal,
            AIMessage(content="Exported."),
            proposal,
            AIMessage(content="Declined."),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )
    with TestClient(app) as client:
        runtime = client.get("/api/v1/runtime").json()
        assert len(runtime["chat_tools"]) == 8 and len(runtime["work_tools"]) == 10
        assert runtime["deprecated_tools"] == ["run_command", "write_document_python"]
        assert "export_document" in runtime["tool_groups"]["write"]
        review = client.post("/api/v1/chat", json={"message": "Export a report"}).json()
        assert review["calls"][0]["risk"] == "write"
        assert "report.txt" in review["calls"][0]["review_hint"]
        clock["now"] = 100.25
        approved = client.post(
            f"/api/v1/threads/{review['thread_id']}/approval/{review['approval_id']}",
            json={"approved": True},
        ).json()
        (metric,) = approved["metrics"]["tools"]
        assert metric["ok"] == 1 and metric["calls"] == 1 and metric["approval_wait_ms"] == 250
        assert metric["artifacts"] == 1 and metric["output_bytes"] > 0
        assert approved["metrics"]["usage_reported"] is False
        saved = client.get(f"/api/v1/threads/{review['thread_id']}/messages").json()["items"][0]
        assert saved["metrics"] == approved["metrics"]
        tool = next(item for item in approved["activity"] if item["kind"] == "tool")
        (artifact,) = tool["artifacts"]
        downloaded = client.get(artifact["url"])
        assert downloaded.status_code == 200 and b"A report" in downloaded.content
        foreign = client.post("/api/v1/threads", json={}).json()["thread_id"]
        assert (
            client.get(f"/api/v1/threads/{foreign}/artifacts/{artifact['id']}").status_code == 404
        )
        declined = client.post("/api/v1/chat", json={"message": "Another report"}).json()
        result = client.post(
            f"/api/v1/threads/{declined['thread_id']}/approval/{declined['approval_id']}",
            json={"approved": False},
        ).json()
        assert result["metrics"]["tools"][0]["rejected"] == 1
        entry = next(item for item in result["activity"] if item["kind"] == "tool")
        assert entry["error_code"] == "DENIED" and entry["status"] == "rejected"
        account = client.get("/api/v1/account/activity").json()
        (total,) = account["tools"]
        assert total["calls"] == 2 and total["ok"] == total["rejected"] == 1
        assert account["tool_calls"] == 1 and account["total_tokens"] is None
        assert account["tool_daily"][0]["tools"][0] == total


def test_invalid_write_arguments_return_error_without_requesting_approval(tmp_path):
    model = Model(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "workspace_write",
                        "args": {"action": "write_file"},
                        "id": "invalid",
                    }
                ],
            ),
            AIMessage(content="A valid path is required."),
        ]
    )
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "agent.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )
    with TestClient(app) as client:
        result = client.post("/api/v1/chat", json={"mode": "work", "message": "Write a file"})
        assert result.status_code == 200
        tool = next(item for item in result.json()["activity"] if item["kind"] == "tool")
        assert tool["error_code"] == "INVALID_ARGS"
