"""Artifacts stay downloadable by their generation-time locator (F09).

/ 产物按生成时位置保持可下载（F09）。
"""

import json
from pathlib import Path
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


def build_app(tmp_path, responses=None):
    """Build an offline app with builtin work tools. / 构建带内置工作工具的离线应用。"""
    if responses is None:
        responses = [proposal(), AIMessage(content="Exported."), proposal("two.txt")]
    model = Model(responses=responses)
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
        location = client.get(artifact["url"] + "/location").json()["path"]
        assert Path(location).is_relative_to(first.resolve())
        moved = client.patch(BASE + f"/threads/{thread}", json={"workspace_id": other})
        assert moved.status_code == 200, moved.text
        # The link still points at the producing session and the producing workspace.
        # 链接仍指向生成会话与生成时的工作区。
        after = client.get(artifact["url"])
        assert after.status_code == 200
        assert after.content == before.content
        assert client.get(artifact["url"] + "/location").json()["path"] == location


def test_forked_session_keeps_the_inherited_artifact_after_source_delete(tmp_path):
    app = build_app(tmp_path)
    with TestClient(app) as client:
        thread = client.post(BASE + "/threads", json={"mode": "work"}).json()["thread_id"]
        thread, artifact = export_artifact(client, thread)
        original = client.get(artifact["url"])
        location = client.get(artifact["url"] + "/location").json()["path"]
        fork = client.post(
            BASE + f"/threads/{thread}/fork", json={"title": "Forked"}
        ).json()["thread_id"]
        assert client.delete(BASE + f"/threads/{thread}").status_code in {204, 202}
        inherited = client.get(
            BASE + f"/threads/{fork}/artifacts/{artifact['id']}"
        )
        assert inherited.status_code == 200
        assert inherited.content == original.content
        inherited_location = client.get(
            BASE + f"/threads/{fork}/artifacts/{artifact['id']}/location"
        )
        assert inherited_location.status_code == 200, inherited_location.text
        assert inherited_location.json()["path"] == location
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
        location = BASE + f"/threads/{foreign}/artifacts/{artifact['id']}/location"
        assert client.get(location).status_code == 404


def test_written_file_location_is_restored_from_history_and_tracks_edits(tmp_path):
    directory = tmp_path / "workspace"
    directory.mkdir()
    file = directory / "changes.md"
    write = AIMessage(
        content="",
        tool_calls=[{
            "name": "workspace_write", "id": "write-changes",
            "args": {"action": "write_file", "path": file.name, "content": "First version"},
        }],
    )
    app = build_app(tmp_path, [write, AIMessage(content="Wrote the file.")])
    with TestClient(app) as client:
        workspace = client.post(
            BASE + "/workspaces", json={"name": "Documents", "directory": str(directory)}
        ).json()["id"]
        thread = client.post(BASE + "/threads", json={
            "mode": "work", "workspace_id": workspace
        }).json()["thread_id"]
        thread, artifact = export_artifact(client, thread, message="Write changes.md")
        completed = client.get(BASE + f"/threads/{thread}/messages").json()["items"][0]
        recorded = next(item for item in completed["activity"] if item["name"] == "workspace_write")
        assert recorded["status"] == "done" and recorded["artifacts"][0]["id"] == artifact["id"]
        location = artifact["url"] + "/location"
        assert client.get(location).json()["path"] == str(file.resolve())
        browser = client.get(location, headers={"Sec-Fetch-Site": "same-origin"})
        assert browser.status_code == 403 and "path" not in browser.json()

        file.write_text("Edited in an external editor", encoding="utf-8")
        assert client.get(location).json()["path"] == str(file.resolve())
        assert client.get(artifact["url"]).status_code == 404
        file.unlink()
        assert client.get(location).status_code == 404


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
