"""Large attachment upload and paged preview tests. / 大文件上传与分页读取测试。"""

from types import SimpleNamespace

from fastapi.testclient import TestClient
from langchain_core.language_models.fake_chat_models import FakeListChatModel

from app.api import LOCAL_USER
from app.config import CheckpointSettings
from tests.support.legacy_app import create_app


def test_upload_above_old_2mib_limit_is_stored_and_paged(tmp_path):
    """A >2 MiB text file is accepted, preview-bounded and paged.
    / 大文件可上传且分页读取。"""
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=tmp_path / "large.sqlite"),
        model_provider=SimpleNamespace(get_chat_model=lambda: FakeListChatModel(responses=["ok"])),
    )
    payload = ("BEGIN" + "x" * 3_000_000 + "END").encode()
    with TestClient(app) as client:
        thread = client.post("/api/v1/threads", json={"mode": "chat"}).json()["thread_id"]
        upload = client.post(
            f"/api/v1/threads/{thread}/attachments",
            params={"filename": "large.txt"},
            content=payload,
        )
        assert upload.status_code == 201, upload.text
        file_id = upload.json()["id"]
        assert upload.json()["byte_size"] > 2 * 1024 * 1024

        preview = client.portal.call(
            app.state.conversations.read_attachment_previews,
            LOCAL_USER,
            thread,
            [file_id],
            60_000,
        )[0]
        assert len(preview["content"]) == 60_000
        assert preview["total_chars"] > 3_000_000

        first = client.portal.call(
            app.state.conversations.read_attachment_page,
            LOCAL_USER,
            thread,
            file_id,
            0,
            8_000,
        )
        assert first["text"].startswith("BEGIN")
        assert first["next_offset"] == 8_000
        last = client.portal.call(
            app.state.conversations.read_attachment_page,
            LOCAL_USER,
            thread,
            file_id,
            first["total_chars"] - 3,
            8_000,
        )
        assert last["text"].endswith("END")
