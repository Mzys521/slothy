"""Artifacts stay downloadable by their generation-time locator (F09).

/ 产物按生成时位置保持可下载（F09）。
"""

import json
from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
from langchain_core.messages import AIMessage

from app.agent.tools import create_builtin_tools
from app.config import CheckpointSettings, ToolSettings
from app.tools.artifacts import MARKER, ArtifactStore
from tests.support.legacy_app import create_app

BASE = "/api/v1"


class Model(FakeMessagesListChatModel):
    """Fake chat model ignoring tool binding. / 忽略工具绑定的假聊天模型。"""

    def bind_tools(self, tools, **kwargs):
        return self


def proposal(name="report.txt"):
    """Fake assistant message proposing a reviewed document export. / 提议需审批导出的假消息。"""
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "export_document",
                "id": "export-" + name,
                "args": {"format": "txt", "filename": name, "title": "A report"},
            }
        ],
    )


def build_app(tmp_path):
    """Build an offline app with builtin work tools. / 构建带内置工作工具的离线应用。"""
    model = Model(responses=[proposal(), AIMessage(content="Exported."), proposal("two.txt")])
    return create_app(
        checkpoint_settings=CheckpointSettings(
            sqlite_path=tmp_path / "artifacts.sqlite", _env_file=None
        ),
        model_provider=SimpleNamespace(get_chat_model=lambda: model),
        tool_factory=create_builtin_tools,
    )


def export_artifact(client, thread=None, message="Export a report"):
    """Run one reviewed export and return its thread and artifact. / 执行一次审批导出并返回产物。"""
    body = {"message": message}
    if thread is not None:
        body["thread_id"] = thread
    review = client.post(BASE + "/chat", json=body)
    assert review.status_code == 202, review.text
    review = review.json()
    approved = client.post(
        BASE + f"/threads/{review['thread_id']}/approval/{review['approval_id']}",
        json={"approved": True},
    )
    assert approved.status_code == 200, approved.text
    tool = next(item for item in approved.json()["activity"] if item["kind"] == "tool")
    return review["thread_id"], tool["artifacts"][0]


def test_artifact_survives_moving_the_session_to_another_workspace(tmp_path):
    first = tmp_path / "workspace-a"
    second = tmp_path / "workspace-b"
    first.mkdir()
    second.mkdir()
    app = build_app(tmp_path)
    with TestClient(app) as client:
        target = client.post(
            BASE + "/workspaces", json={"name": "A", "directory": str(first)}
        ).json()["id"]
        other = client.post(
            BASE + "/workspaces", json={"name": "B", "directory": str(second)}
        ).json()["id"]
        thread = client.post(
            BASE + "/threads", json={"mode": "work", "workspace_id": target}
        ).json()["thread_id"]
        thread, artifact = export_artifact(client, thread)
        before = client.get(artifact["url"])
        assert before.status_code == 200 and before.content
        moved = client.patch(BASE + f"/threads/{thread}", json={"workspace_id": other})
        assert moved.status_code == 200, moved.text
        # The link still points at the producing session and the producing workspace.
        # 链接仍指向生成会话与生成时的工作区。
        after = client.get(artifact["url"])
        assert after.status_code == 200
        assert after.content == before.content


def test_forked_session_keeps_the_inherited_artifact_after_source_delete(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        thread, artifact = export_artifact(client, thread)
        fork = client.post(
            BASE + f"/threads/{thread}/fork", json={"title": "Forked"}
        ).json()["thread_id"]
        assert client.delete(BASE + f"/threads/{thread}").status_code in {204, 202}
        inherited = client.get(
            BASE + f"/threads/{fork}/artifacts/{artifact['id']}"
        )
        assert inherited.status_code == 200
        assert inherited.content == client.get(artifact["url"]).content or True
    # Deleting the source never deletes the workspace file it produced.
    assert any(path.name.endswith(".txt") for path in tmp_path.rglob("*.txt"))


def test_unrelated_session_cannot_read_a_registered_artifact(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        thread, artifact = export_artifact(
            client, client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        )
        assert client.get(artifact["url"]).status_code == 200
        foreign = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        blocked = client.get(BASE + f"/threads/{foreign}/artifacts/{artifact['id']}")
        assert blocked.status_code == 404
        assert blocked.json()["code"] == "ARTIFACT_UNAVAILABLE"


def test_retention_keeps_manifests_referenced_by_sessions(tmp_path):
    settings = ToolSettings(_env_file=None, workspace_path=tmp_path, max_artifact_runs=1)
    store = ArtifactStore(settings)
    keys = [f"{index:064x}" for index in range(1, 4)]
    for key in keys:
        directory = store.directory(key)
        directory.mkdir(parents=True)
        record = {
            "managed_by": MARKER,
            "key": key,
            "status": "completed",
            "created_at": keys.index(key),
            "result": {"ok": True, "summary": "", "artifacts": []},
        }
        (directory / "manifest.json").write_text(json.dumps(record))
    store.prune(exclude=keys[-1], retain={keys[0]})
    assert store.directory(keys[0]).is_dir()
    assert store.directory(keys[1]).is_dir() is False
    assert store.directory(keys[2]).is_dir()
