"""Protect existing business databases at startup and expose an explicit recovery path.

Covers F01: a missing side marker, a damaged database, a version rollback, a future version, a
verified backup and two consecutive startups must never lose archived turns, checkpoints or
memories. / 覆盖 F01：标记缺失、数据库损坏、版本回退、未来版本、已验证备份与连续两次启动都不得
丢失归档轮次、检查点或记忆。
"""

import asyncio
import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from langchain_core.runnables import RunnableLambda

from app.api import create_app
from app.config import CheckpointSettings
from app.conversations import ConversationTurn
from app.infrastructure.checkpoints import SQLiteCheckpointStore
from app.infrastructure.conversations import SQLiteConversationStore
from app.infrastructure.storage_version import (
    STORAGE_VERSION,
    StorageRecoveryRequired,
    adopt_storage,
    inspect_storage,
    prepare_storage,
    reset_storage,
)
from app.memory.store import MemoryStore

BASE = "/api/v1"


async def create_v3_data(path: Path) -> None:
    """Create one archived turn, one checkpoint and one memory.
    / 创建一轮归档、一个检查点与一条记忆。"""
    settings = CheckpointSettings(sqlite_path=path)
    async with (
        SQLiteCheckpointStore(settings) as checkpointer,
        SQLiteConversationStore.open(settings) as conversations,
        MemoryStore.open(settings.sqlite_path.with_suffix(".workspace.sqlite"), 5) as memories,
    ):
        assert checkpointer is not None
        await conversations.create_thread("local-user", "thread-1", "Hello", "chat")
        await conversations.save_turn(
            ConversationTurn(
                "local-user",
                "thread-1",
                "run-1",
                "你好，记住我",
                "已记住，你好",
                reasoning="thinking",
            )
        )
        await conversations.save_attachment(
            "local-user", "thread-1", "file-1", "notes.txt", 12, "hello world"
        )
        await memories.connection.execute(
            """INSERT INTO workspace_memories (
                 id, user_id, title, content, type, status, scope, scope_ref, source, importance,
                 memory_key, dimension, value_json, predicate, subject, protected
               ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "mem-1",
                "local-user",
                "姓名",
                "林舟",
                "identity",
                "active",
                "global",
                None,
                "manual",
                0.9,
                "user.name",
                "identity",
                '"林舟"',
                "related",
                "user",
                1,
            ),
        )
    connection = sqlite3.connect(path)
    try:
        connection.execute(
            """INSERT INTO checkpoints
               (thread_id, checkpoint_ns, checkpoint_id, parent_checkpoint_id, type,
                checkpoint, metadata)
               VALUES (?, '', ?, NULL, 'json', ?, '{}')""",
            ('["local-user","thread-1"]', "checkpoint-1", b'{"v":1}'),
        )
        connection.commit()
    finally:
        connection.close()


def table_rows(path: Path, table: str) -> list[tuple]:
    """Return rows of one table through a read-only connection. / 只读读取一张表的全部行。"""
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    try:
        return list(connection.execute(f'SELECT * FROM "{table}"'))
    finally:
        connection.close()


def test_missing_marker_adopts_verified_v3_data(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    asyncio.run(create_v3_data(path))
    turns = table_rows(path.with_suffix(".sessions.sqlite"), "conversation_turns")
    memories = table_rows(path.with_suffix(".workspace.sqlite"), "workspace_memories")
    checkpoints = table_rows(path, "checkpoints")
    assert turns and memories and checkpoints
    assert inspect_storage(path).status == "current"

    metadata = prepare_storage(path)

    assert metadata["version"] == STORAGE_VERSION
    assert metadata["adopted"] is True
    assert path.exists() and path.with_suffix(".sessions.sqlite").exists()
    assert table_rows(path.with_suffix(".sessions.sqlite"), "conversation_turns") == turns
    assert table_rows(path.with_suffix(".workspace.sqlite"), "workspace_memories") == memories
    assert table_rows(path, "checkpoints") == checkpoints
    for name in (
        "checkpoints.sqlite",
        "checkpoints.sessions.sqlite",
        "checkpoints.workspace.sqlite",
    ):
        connection = sqlite3.connect(f"file:{(tmp_path / name).as_posix()}?mode=ro", uri=True)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == STORAGE_VERSION
        finally:
            connection.close()


def test_two_startups_keep_the_same_epoch_and_rows(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    asyncio.run(create_v3_data(path))
    first = prepare_storage(path)
    rows = table_rows(path.with_suffix(".sessions.sqlite"), "conversation_turns")
    second = prepare_storage(path)

    assert second == first
    assert second["epoch"] == first["epoch"]
    assert table_rows(path.with_suffix(".sessions.sqlite"), "conversation_turns") == rows


def test_empty_directory_initializes_and_retries(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    first = prepare_storage(path)
    second = prepare_storage(path)

    assert first["version"] == STORAGE_VERSION and second == first
    assert first["reset"] is False
    assert not path.exists()


def test_damaged_database_requires_recovery_and_keeps_bytes(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    (tmp_path / "providers.json").write_text("provider-config")
    for name in ("checkpoints.sqlite", "checkpoints.sessions.sqlite"):
        (tmp_path / name).write_bytes(b"broken-database")
    with pytest.raises(StorageRecoveryRequired) as error:
        prepare_storage(path)
    state = error.value.state
    assert state.status == "corrupt" and state.recoverable
    assert (tmp_path / "checkpoints.sqlite").read_bytes() == b"broken-database"
    assert not path.with_suffix(".storage.json").exists()
    with pytest.raises(StorageRecoveryRequired):
        adopt_storage(path)
    assert (tmp_path / "checkpoints.sqlite").read_bytes() == b"broken-database"
    assert (tmp_path / "providers.json").read_text() == "provider-config"


def test_future_and_old_markers_require_recovery_without_deleting(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    path.write_bytes(b"legacy-data")
    marker = path.with_suffix(".storage.json")
    marker.write_text(json.dumps({"version": STORAGE_VERSION + 1, "epoch": "future"}))
    with pytest.raises(StorageRecoveryRequired) as future:
        prepare_storage(path)
    assert future.value.state.status == "future"
    assert path.read_bytes() == b"legacy-data"

    marker.write_text(json.dumps({"version": 2, "epoch": "old"}))
    with pytest.raises(StorageRecoveryRequired) as legacy:
        prepare_storage(path)
    assert legacy.value.state.status == "legacy"
    assert path.read_bytes() == b"legacy-data"
    assert json.loads(marker.read_text())["version"] == 2


def create_legacy_data(path: Path) -> None:
    """Create pre-V3 business databases with the old columns only. / 创建只有旧列的 V3 前业务库。"""
    sessions = path.with_suffix(".sessions.sqlite")
    workspace = path.with_suffix(".workspace.sqlite")
    connection = sqlite3.connect(path)
    connection.executescript(
        """CREATE TABLE checkpoints (
             thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
             checkpoint_id TEXT NOT NULL, parent_checkpoint_id TEXT, type TEXT,
             checkpoint BLOB, metadata BLOB,
             PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id));
           CREATE TABLE writes (
             thread_id TEXT NOT NULL, checkpoint_ns TEXT NOT NULL DEFAULT '',
             checkpoint_id TEXT NOT NULL, task_id TEXT NOT NULL, task_path TEXT NOT NULL DEFAULT '',
             idx INTEGER NOT NULL, channel TEXT NOT NULL, type TEXT, value BLOB,
             PRIMARY KEY (thread_id, checkpoint_ns, checkpoint_id, task_id, idx));
           INSERT INTO checkpoints (thread_id, checkpoint_id, type, checkpoint, metadata)
             VALUES ('["local-user","thread-1"]', 'old-checkpoint', 'json', '{"v":1}', '{}');"""
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(sessions)
    connection.executescript(
        """CREATE TABLE conversation_turns (
             id INTEGER PRIMARY KEY, user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
             run_id TEXT NOT NULL, user_input TEXT NOT NULL, response TEXT NOT NULL,
             UNIQUE (user_id, thread_id, run_id));
           CREATE TABLE conversation_threads (
             user_id TEXT NOT NULL, thread_id TEXT NOT NULL, title TEXT NOT NULL DEFAULT '',
             mode TEXT NOT NULL DEFAULT 'chat', archived INTEGER NOT NULL DEFAULT 0,
             created_at TEXT NOT NULL DEFAULT '2026-11-01T00:00:00Z',
             updated_at TEXT NOT NULL DEFAULT '2026-11-01T00:00:00Z',
             PRIMARY KEY (user_id, thread_id));
           INSERT INTO conversation_threads (user_id, thread_id, title)
             VALUES ('local-user', 'thread-1', '旧会话');
           INSERT INTO conversation_turns (user_id, thread_id, run_id, user_input, response)
             VALUES ('local-user', 'thread-1', 'run-old', '以前的提问', '以前的回答');"""
    )
    connection.commit()
    connection.close()
    connection = sqlite3.connect(workspace)
    connection.executescript(
        """CREATE TABLE workspace_memories (
             id TEXT PRIMARY KEY, user_id TEXT NOT NULL, title TEXT NOT NULL,
             content TEXT NOT NULL, type TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'active',
             scope TEXT NOT NULL, scope_ref TEXT, source TEXT, importance REAL NOT NULL,
             created_at TEXT NOT NULL DEFAULT '2026-11-01T00:00:00Z',
             updated_at TEXT NOT NULL DEFAULT '2026-11-01T00:00:00Z');
           CREATE TABLE memory_claims (
             id TEXT PRIMARY KEY, user_id TEXT NOT NULL, scope TEXT NOT NULL, scope_ref TEXT,
             profile_key TEXT NOT NULL, dimension TEXT NOT NULL, value TEXT NOT NULL,
             title TEXT NOT NULL, status TEXT NOT NULL, memory_id TEXT, updated_at TEXT);
           CREATE TABLE memory_evidence (
             claim_id TEXT NOT NULL, thread_id TEXT NOT NULL, run_id TEXT NOT NULL,
             quote TEXT NOT NULL, source TEXT NOT NULL, recorded_at TEXT,
             PRIMARY KEY (claim_id, thread_id, run_id));
           CREATE TABLE workspace_profiles (
             user_id TEXT NOT NULL, version INTEGER NOT NULL, profile_json TEXT NOT NULL,
             change_summary TEXT NOT NULL, created_at TEXT NOT NULL,
             PRIMARY KEY (user_id, version));
           INSERT INTO workspace_memories (id, user_id, title, content, type, scope, importance)
             VALUES ('old-mem', 'local-user', '旧记忆', '用户喜欢深色主题', 'preference',
                     'global', 0.7);"""
    )
    connection.commit()
    connection.close()


def test_legacy_data_is_upgraded_in_place_after_an_explicit_choice(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    create_legacy_data(path)
    assert inspect_storage(path).status == "legacy"
    with pytest.raises(StorageRecoveryRequired):
        prepare_storage(path)

    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=path),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(lambda value: None)),
        memory_processing="rules",
    )
    with TestClient(app) as client:
        status = client.get(BASE + "/storage/recovery").json()
        assert status["required"] is True and status["adoptable"] is False
        adopted = client.post(BASE + "/storage/recovery/adopt")
        assert adopted.status_code == 200, adopted.text
        assert adopted.json()["restart_required"] is True
        backup = Path(adopted.json()["backup"])
        assert "旧会话".encode() in (backup / "checkpoints.sessions.sqlite").read_bytes()

    sessions = path.with_suffix(".sessions.sqlite")
    turns = table_rows(sessions, "conversation_turns")
    assert any(row[3:5] == ("run-old", "以前的提问") for row in turns)
    columns = {
        row[1]
        for row in sqlite3.connect(sessions).execute("PRAGMA table_info(conversation_turns)")
    }
    assert {"explicit_references", "memory_trace", "learning_generation"} <= columns
    memories = table_rows(path.with_suffix(".workspace.sqlite"), "workspace_memories")
    assert any(row[0] == "old-mem" for row in memories)
    metadata = prepare_storage(path)
    assert metadata["version"] == STORAGE_VERSION and metadata["adopted"] is True


def test_verified_backup_then_reset_keeps_a_recoverable_copy(tmp_path):
    path = tmp_path / "checkpoints.sqlite"
    for name in ("checkpoints.sqlite", "checkpoints.sessions.sqlite"):
        (tmp_path / name).write_bytes(b"damaged")
    (tmp_path / "workspaces").mkdir()
    (tmp_path / "workspaces" / "important.py").write_text("print('keep')")

    metadata = reset_storage(path)

    assert metadata["version"] == STORAGE_VERSION and metadata["reset"] is True
    backup = Path(metadata["backup"])
    assert (backup / "checkpoints.sqlite").read_bytes() == b"damaged"
    assert json.loads((backup / "manifest.json").read_text())["version"] == STORAGE_VERSION
    assert not path.exists() and not path.with_suffix(".sessions.sqlite").exists()
    assert (tmp_path / "workspaces" / "important.py").exists()
    assert prepare_storage(path) == metadata


def test_api_serves_recovery_only_until_the_user_decides(tmp_path):
    path = tmp_path / "app.sqlite"
    for name in ("app.sqlite", "app.sessions.sqlite"):
        (tmp_path / name).write_bytes(b"damaged")
    app = create_app(
        checkpoint_settings=CheckpointSettings(sqlite_path=path),
        model_provider=SimpleNamespace(get_chat_model=lambda: RunnableLambda(lambda value: None)),
        memory_processing="rules",
    )
    with TestClient(app) as client:
        assert client.get(BASE + "/health").status_code == 200
        blocked = client.get(BASE + "/threads")
        assert blocked.status_code == 503
        assert blocked.json()["code"] == "STORAGE_RECOVERY_REQUIRED"
        assert blocked.json()["storage"]["status"] == "corrupt"
        status = client.get(BASE + "/storage/recovery").json()
        assert status["required"] is True and status["adoptable"] is False
        assert client.post(BASE + "/storage/recovery/adopt").status_code == 422
        assert (tmp_path / "app.sqlite").read_bytes() == b"damaged"
        reset = client.post(BASE + "/storage/recovery/reset")
        assert reset.status_code == 200, reset.text
        assert reset.json()["restart_required"] is True
        assert Path(reset.json()["backup"], "app.sqlite").read_bytes() == b"damaged"
        assert client.get(BASE + "/storage/recovery").json()["required"] is False
