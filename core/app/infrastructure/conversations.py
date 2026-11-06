"""独立 SQLite 会话归档。 / Independent SQLite conversation archive."""

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiosqlite

from app.agent.activity import RunActivity
from app.agent.metrics import RunMetrics
from app.config import CheckpointSettings
from app.conversations import ConversationTurn


class SQLiteConversationStore:
    """会话记录连接及查询。 / Conversation history connection and queries.

    Args:
        connection: 由 open 管理的连接。 / Connection owned by open.
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        self._connection = connection
        self.learning_wakeup = asyncio.Event()

    @classmethod
    @asynccontextmanager
    async def open(cls, settings: CheckpointSettings) -> AsyncIterator["SQLiteConversationStore"]:
        """settings：快照路径用于派生独立文件名，同时复用锁等待值。

        English: Derive a separate file from the checkpoint path and reuse its lock timeout.
        """
        path = settings.sqlite_path.with_suffix(".sessions.sqlite").resolve()
        if path == settings.sqlite_path or (
            path.exists() and settings.sqlite_path.exists() and path.samefile(settings.sqlite_path)
        ):
            raise ValueError("Conversation and checkpoint databases must be separate")
        path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(
            path, timeout=settings.busy_timeout_ms / 1000, isolation_level=None
        ) as connection:
            async with connection.executescript("""
                PRAGMA journal_mode=WAL;
                PRAGMA synchronous=FULL;
                CREATE TABLE IF NOT EXISTS conversation_turns (
                    id INTEGER PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    user_input TEXT NOT NULL,
                    response TEXT NOT NULL,
                    UNIQUE (user_id, thread_id, run_id)
                );
                CREATE INDEX IF NOT EXISTS conversation_turns_history
                    ON conversation_turns (user_id, thread_id, id);
                CREATE TABLE IF NOT EXISTS conversation_threads (
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    title TEXT NOT NULL DEFAULT '',
                    mode TEXT NOT NULL DEFAULT 'chat' CHECK (mode IN ('chat', 'work')),
                    archived INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now')),
                    PRIMARY KEY (user_id, thread_id)
                );
                INSERT OR IGNORE INTO conversation_threads (user_id, thread_id, title)
                SELECT t.user_id, t.thread_id, substr(t.user_input, 1, 80)
                FROM conversation_turns AS t
                WHERE t.id IN (
                    SELECT min(id) FROM conversation_turns GROUP BY user_id, thread_id
                );
                CREATE TRIGGER IF NOT EXISTS conversation_turn_thread
                AFTER INSERT ON conversation_turns
                BEGIN
                    INSERT INTO conversation_threads (user_id, thread_id, title)
                    VALUES (NEW.user_id, NEW.thread_id, substr(NEW.user_input, 1, 80))
                    ON CONFLICT (user_id, thread_id) DO UPDATE SET
                        title = CASE WHEN title = '' THEN excluded.title ELSE title END,
                        updated_at = excluded.updated_at;
                END;
                CREATE TABLE IF NOT EXISTS conversation_preferences (
                    user_id TEXT PRIMARY KEY,
                    personalization_enabled INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS memory_configuration (
                    user_id TEXT PRIMARY KEY,
                    revision INTEGER NOT NULL,
                    configuration_json TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS local_accounts (
                    user_id TEXT PRIMARY KEY,
                    display_name TEXT,
                    handle TEXT,
                    avatar BLOB,
                    avatar_version TEXT
                );
                CREATE TABLE IF NOT EXISTS conversation_files (
                    id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    thread_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    content TEXT NOT NULL,
                    PRIMARY KEY(user_id, thread_id, id)
                );
                CREATE INDEX IF NOT EXISTS conversation_files_thread
                    ON conversation_files(user_id, thread_id);
                CREATE TABLE IF NOT EXISTS conversation_workspaces (
                    user_id TEXT NOT NULL,
                    id TEXT NOT NULL,
                    name TEXT NOT NULL COLLATE NOCASE,
                    PRIMARY KEY(user_id, id),
                    UNIQUE(user_id, name)
                );
                """):
                pass
            async with connection.execute("PRAGMA table_info(conversation_turns)") as cursor:
                columns = {row[1] async for row in cursor}
            if "learning_state" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN learning_state INTEGER DEFAULT 0"
                )
            if "learning_project_id" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN learning_project_id TEXT"
                )
            if "learning_retry_at" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN learning_retry_at INTEGER DEFAULT 0"
                )
            if "attachment_ids" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN attachment_ids TEXT DEFAULT '[]'"
                )
            if "activity" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN activity TEXT NOT NULL DEFAULT '[]'"
                )
            if "metrics" not in columns:
                await connection.execute("ALTER TABLE conversation_turns ADD COLUMN metrics TEXT")
            if "recorded_at" not in columns:
                await connection.execute(
                    "ALTER TABLE conversation_turns ADD COLUMN recorded_at TEXT"
                )
            for name, declaration in (
                ("used_memory_ids", "TEXT NOT NULL DEFAULT '[]'"),
                ("profile_version", "INTEGER"),
                ("learning_attempts", "INTEGER NOT NULL DEFAULT 0"),
                ("learning_error", "TEXT"),
                ("learning_generation", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in columns:
                    await connection.execute(
                        f"ALTER TABLE conversation_turns ADD COLUMN {name} {declaration}"
                    )
            async with connection.execute("PRAGMA table_info(conversation_workspaces)") as cursor:
                workspace_columns = {row[1] async for row in cursor}
            if "directory" not in workspace_columns:
                await connection.execute(
                    "ALTER TABLE conversation_workspaces ADD COLUMN directory TEXT"
                )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS learning_queue ON conversation_turns(id) "
                "WHERE learning_state = 1"
            )
            async with connection.execute("PRAGMA table_info(conversation_threads)") as cursor:
                thread_columns = {row[1] async for row in cursor}
            if "workspace_id" not in thread_columns:
                await connection.execute(
                    "ALTER TABLE conversation_threads ADD COLUMN workspace_id TEXT"
                )
            if "pinned" not in thread_columns:
                await connection.execute(
                    "ALTER TABLE conversation_threads ADD COLUMN pinned INTEGER"
                )
            yield cls(connection)

    async def create_thread(
        self, user_id: str, thread_id: str, title: str, mode: str, workspace_id: str | None = None
    ) -> dict:
        async with self._connection.execute(
            """INSERT INTO conversation_threads (user_id, thread_id, title, mode, workspace_id)
               VALUES (?, ?, ?, ?, ?)
               RETURNING thread_id, title, mode, archived, created_at, updated_at,
                         workspace_id, pinned""",
            (user_id, thread_id, title, mode, workspace_id),
        ) as cursor:
            return self._thread_dict(await cursor.fetchone())

    @staticmethod
    def _thread_dict(row: tuple) -> dict:
        thread_id, title, mode, archived, created_at, updated_at, workspace_id, pinned = row
        result = dict(
            thread_id=thread_id,
            title=title,
            mode=mode,
            archived=bool(archived),
            created_at=created_at,
            updated_at=updated_at,
        )
        if workspace_id is not None:
            result["workspace_id"] = workspace_id
        if pinned is not None:
            result["pinned"] = bool(pinned)
        return result

    async def get_thread(self, user_id: str, thread_id: str) -> dict | None:
        async with self._connection.execute(
            """SELECT thread_id, title, mode, archived, created_at, updated_at, workspace_id, pinned
               FROM conversation_threads WHERE user_id=? AND thread_id=?""",
            (user_id, thread_id),
        ) as cursor:
            row = await cursor.fetchone()
            return self._thread_dict(row) if row else None

    async def list_threads(
        self,
        user_id: str,
        archived: bool,
        limit: int,
        offset: int,
        workspace_id: str | None = None,
    ) -> list[dict]:
        async with self._connection.execute(
            """SELECT thread_id, title, mode, archived, created_at, updated_at, workspace_id, pinned
               FROM conversation_threads WHERE user_id=? AND archived=?
               AND (? IS NULL OR coalesce(workspace_id, 'personal')=?)
               ORDER BY updated_at DESC, thread_id DESC LIMIT ? OFFSET ?""",
            (user_id, archived, workspace_id, workspace_id, limit, offset),
        ) as cursor:
            return [self._thread_dict(row) async for row in cursor]

    async def update_thread(self, user_id: str, thread_id: str, values: dict) -> None:
        fields = [
            field for field in ("archived", "title", "workspace_id", "pinned") if field in values
        ]
        assignments = ", ".join(field + "=?" for field in fields)
        async with self._connection.execute(
            f"UPDATE conversation_threads SET {assignments} WHERE user_id=? AND thread_id=?",
            (*(values[field] for field in fields), user_id, thread_id),
        ):
            pass

    async def get_workspace(self, user: str, workspace_id: str) -> dict | None:
        async with self._connection.execute(
            "SELECT id, name, directory FROM conversation_workspaces WHERE user_id=? AND id=?",
            (user, workspace_id),
        ) as cursor:
            row = await cursor.fetchone()
        if row:
            return {"id": row[0], "name": row[1], "directory": row[2]}
        return (
            {"id": "personal", "name": "", "directory": None}
            if workspace_id == "personal"
            else None
        )

    async def list_workspaces(self, user: str, limit: int, offset: int) -> list[dict]:
        async with self._connection.execute(
            """SELECT id, name, directory FROM (
                 SELECT id, name, directory FROM conversation_workspaces WHERE user_id=?
                 UNION ALL SELECT 'personal', '', NULL WHERE NOT EXISTS (
                   SELECT 1 FROM conversation_workspaces WHERE user_id=? AND id='personal'
                 )
               ) ORDER BY id<>'personal', name, id LIMIT ? OFFSET ?""",
            (user, user, limit, offset),
        ) as cursor:
            return [{"id": row[0], "name": row[1], "directory": row[2]} async for row in cursor]

    async def save_workspace(
        self, user: str, workspace_id: str, name: str, directory: str | None = None
    ) -> dict:
        await self._connection.execute(
            """INSERT INTO conversation_workspaces (user_id,id,name,directory) VALUES (?, ?, ?, ?)
               ON CONFLICT (user_id, id) DO UPDATE SET name=excluded.name""",
            (user, workspace_id, name, directory),
        )
        return await self.get_workspace(user, workspace_id)

    async def workspace_threads(self, user: str, workspace_id: str) -> list[str]:
        async with self._connection.execute(
            "SELECT thread_id FROM conversation_threads WHERE user_id=? AND workspace_id=?",
            (user, workspace_id),
        ) as cursor:
            return [row[0] async for row in cursor]

    async def delete_workspace(self, user: str, workspace_id: str):
        await self._connection.execute(
            "DELETE FROM conversation_workspaces WHERE user_id=? AND id=?", (user, workspace_id)
        )

    async def copy_turns(self, user_id: str, source_id: str, target_id: str) -> None:
        await self._connection.execute(
            """INSERT INTO conversation_files (id, user_id, thread_id, filename, byte_size, content)
               SELECT f.id, f.user_id, ?, f.filename, f.byte_size, f.content
               FROM conversation_files f WHERE f.user_id=? AND f.thread_id=? AND EXISTS (
                 SELECT 1 FROM conversation_turns t, json_each(t.attachment_ids) a
                 WHERE t.user_id=f.user_id AND t.thread_id=f.thread_id AND a.value=f.id
               )""",
            (target_id, user_id, source_id),
        )
        async with self._connection.execute(
            """INSERT INTO conversation_turns (
                   user_id, thread_id, run_id, user_input, response,
                   attachment_ids, activity, metrics, used_memory_ids, profile_version, recorded_at
               )
               SELECT user_id, ?, run_id, user_input, response,
                      attachment_ids, activity, metrics, used_memory_ids,
                      profile_version, recorded_at
               FROM conversation_turns WHERE user_id=? AND thread_id=? ORDER BY id""",
            (target_id, user_id, source_id),
        ):
            pass

    async def clear_thread(self, user_id: str, thread_id: str) -> None:
        async with self._connection.execute(
            "DELETE FROM conversation_turns WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ):
            pass
        await self._connection.execute(
            "DELETE FROM conversation_files WHERE user_id=? AND thread_id=?", (user_id, thread_id)
        )

    async def delete_thread(self, user_id: str, thread_id: str) -> None:
        await self.clear_thread(user_id, thread_id)
        async with self._connection.execute(
            "DELETE FROM conversation_threads WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ):
            pass

    async def get_account(self, user_id: str) -> dict:
        async with self._connection.execute(
            "SELECT display_name, handle, avatar_version FROM local_accounts WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return {}
        return dict(zip(("display_name", "handle", "avatar_version"), row, strict=True))

    async def update_account(self, user_id: str, display_name: str | None, handle: str | None):
        await self._connection.execute(
            """INSERT INTO local_accounts(user_id, display_name, handle) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 display_name=excluded.display_name, handle=excluded.handle""",
            (user_id, display_name, handle),
        )

    async def get_avatar(self, user_id: str) -> bytes | None:
        async with self._connection.execute(
            "SELECT avatar FROM local_accounts WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_avatar(self, user_id: str, image: bytes | None, version: str | None):
        await self._connection.execute(
            """INSERT INTO local_accounts(user_id, avatar, avatar_version) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 avatar=excluded.avatar, avatar_version=excluded.avatar_version""",
            (user_id, image, version),
        )

    async def account_runs(self, user_id: str) -> AsyncIterator[tuple]:
        # Forks copy the original run ID and activity without performing a new model call.
        async with self._connection.execute(
            """SELECT t.recorded_at, s.mode, t.metrics, t.activity
               FROM conversation_turns t JOIN conversation_threads s
               ON s.user_id=t.user_id AND s.thread_id=t.thread_id
               WHERE t.id IN (
                 SELECT min(id) FROM conversation_turns WHERE user_id=? GROUP BY run_id
               ) ORDER BY t.id""",
            (user_id,),
        ) as cursor:
            async for row in cursor:
                yield row

    async def memory_configuration(self, user_id: str) -> tuple[int, dict] | None:
        async with self._connection.execute(
            "SELECT revision, configuration_json FROM memory_configuration WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return (row[0], json.loads(row[1])) if row else None

    async def save_memory_configuration(
        self, user_id: str, configuration: dict, expected_revision: int
    ) -> int | None:
        # One atomic optimistic write; stale editors never overwrite newer settings.
        async with self._connection.execute(
            """INSERT INTO memory_configuration (user_id, revision, configuration_json)
               SELECT ?, ?+1, ? WHERE ?=COALESCE(
                   (SELECT revision FROM memory_configuration WHERE user_id=?), 0)
               ON CONFLICT(user_id) DO UPDATE SET revision=excluded.revision,
                   configuration_json=excluded.configuration_json
               RETURNING revision""",
            (
                user_id,
                expected_revision,
                json.dumps(configuration),
                expected_revision,
                user_id,
            ),
        ) as cursor:
            row = await cursor.fetchone()
            return row[0] if row else None

    async def personalization_enabled(self, user_id: str) -> bool:
        async with self._connection.execute(
            "SELECT personalization_enabled FROM conversation_preferences WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else True

    async def set_personalization(self, user_id: str, enabled: bool) -> None:
        async with self._connection.execute(
            """INSERT INTO conversation_preferences VALUES (?, ?)
               ON CONFLICT (user_id) DO UPDATE
               SET personalization_enabled = excluded.personalization_enabled""",
            (user_id, enabled),
        ):
            pass
        if not enabled:
            await self._connection.execute(
                "UPDATE conversation_turns SET learning_state=0 "
                "WHERE user_id=? AND learning_state=1",
                (user_id,),
            )
        self.learning_wakeup.set()

    async def save_turn(self, turn: ConversationTurn) -> None:
        """turn：从完成快照提取的轮次。 / A turn from a completed snapshot."""
        user_id, thread_id, run_id = (
            turn.user_id.strip(),
            turn.thread_id.strip(),
            turn.run_id.strip(),
        )
        if not user_id or not thread_id or not run_id:
            raise ValueError("user_id, thread_id and run_id must not be blank")
        # 中文：单条语句自动提交，并发会话不共享未提交的事务。
        # English: Autocommit one statement so concurrent sessions share no open transaction.
        async with self._connection.execute(
            """
            INSERT INTO conversation_turns (
                user_id, thread_id, run_id, user_input, response,
                learning_state, learning_project_id, attachment_ids, activity, metrics,
                used_memory_ids, profile_version, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, CASE WHEN ?=1 AND coalesce((
                SELECT personalization_enabled FROM conversation_preferences WHERE user_id=?
            ),1)=1 THEN 1 ELSE 0 END, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT (user_id, thread_id, run_id) DO NOTHING
            """,
            (
                user_id,
                thread_id,
                run_id,
                turn.user_input,
                turn.response,
                int(turn.learn_memory),
                user_id,
                turn.project_id,
                json.dumps(turn.attachment_ids),
                json.dumps([entry.model_dump() for entry in turn.activity]),
                turn.metrics.model_dump_json() if turn.metrics else None,
                json.dumps(turn.used_memory_ids),
                turn.profile_version,
            ),
        ) as cursor:
            if cursor.rowcount and turn.learn_memory:
                self.learning_wakeup.set()

    async def learning_jobs(self, limit: int = 32) -> list[tuple]:
        async with self._connection.execute(
            """SELECT id, user_id, thread_id, run_id, user_input, learning_project_id, recorded_at
               FROM conversation_turns WHERE learning_state=1
                 AND learning_retry_at <= CAST(strftime('%s', 'now') AS INTEGER)
               ORDER BY id LIMIT ?""",
            (limit,),
        ) as cursor:
            return await cursor.fetchall()

    async def finish_learning(
        self, row_id: int, user_id: str, thread_id: str, run_id: str, generation: int | None = None
    ):
        await self._connection.execute(
            """UPDATE conversation_turns SET learning_state=2, learning_error=NULL
               WHERE id=? AND user_id=? AND thread_id=? AND run_id=? AND learning_state=1
                 AND (? IS NULL OR learning_generation=?)""",
            (row_id, user_id, thread_id, run_id, generation, generation),
        )

    async def defer_learning(
        self,
        row_id: int,
        user_id: str,
        thread_id: str,
        run_id: str,
        error: str = "EXTRACTION_FAILED",
        delay: int = 30,
        count_attempt: bool = True,
    ):
        await self._connection.execute(
            """UPDATE conversation_turns
               SET learning_retry_at=CAST(strftime('%s', 'now') AS INTEGER)+?,
                   learning_state=CASE WHEN learning_attempts+? >= 5 THEN 3 ELSE 1 END,
                   learning_attempts=learning_attempts+?, learning_error=?
               WHERE id=? AND user_id=? AND thread_id=? AND run_id=? AND learning_state=1""",
            (
                delay,
                int(count_attempt),
                int(count_attempt),
                error,
                row_id,
                user_id,
                thread_id,
                run_id,
            ),
        )

    async def learning_pending(self, user_id: str, thread_id: str, run_id: str) -> bool:
        async with self._connection.execute(
            """SELECT 1 FROM conversation_turns
               WHERE user_id=? AND thread_id=? AND run_id=? AND learning_state=1""",
            (user_id, thread_id, run_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def learning_status(self, user: str) -> dict:
        async with self._connection.execute(
            """SELECT count(*), coalesce(sum(learning_state=1),0),
                      coalesce(sum(learning_state=2),0),
                      coalesce(sum(learning_state IN (1,3) AND learning_error IS NOT NULL),0),
                      coalesce(sum(learning_state=3),0),
                      (SELECT learning_error FROM conversation_turns
                       WHERE user_id=? AND learning_state IN (1,3) AND learning_error IS NOT NULL
                       ORDER BY id DESC LIMIT 1)
               FROM conversation_turns WHERE user_id=?""",
            (user, user),
        ) as cursor:
            total, pending, processed, failed, dead, last_error = await cursor.fetchone()
        return {
            "total_turns": total,
            "pending": pending,
            "processed": processed,
            "failed": failed,
            "enabled": await self.personalization_enabled(user),
            "dead": dead,
            "last_error": last_error,
        }

    async def requeue_learning(self, user: str, thread: str | None = None) -> int:
        async with self._connection.execute(
            """UPDATE conversation_turns SET learning_state=1, learning_retry_at=0,
                      learning_attempts=0, learning_error=NULL,
                      learning_generation=learning_generation+1
               WHERE id IN (SELECT id FROM conversation_turns WHERE user_id=?
                 AND learning_state IN (1,2,3) AND (? IS NULL OR thread_id=?)
                 ORDER BY id DESC LIMIT 500)""",
            (user, thread, thread),
        ) as cursor:
            self.learning_wakeup.set()
            return cursor.rowcount

    async def learning_metadata(self, user: str, thread: str, run: str) -> dict | None:
        async with self._connection.execute(
            "SELECT learning_state,learning_generation,metrics,learning_attempts, "
            "user_input,learning_project_id,recorded_at "
            "FROM conversation_turns WHERE user_id=? AND thread_id=? AND run_id=?",
            (user, thread, run),
        ) as cursor:
            row = await cursor.fetchone()
        return (
            {
                "state": row[0],
                "generation": row[1],
                "metrics": json.loads(row[2]) if row[2] else {},
                "attempts": row[3],
                "text": row[4],
                "project_id": row[5],
                "recorded_at": row[6],
            }
            if row
            else None
        )

    async def learning_retry_delay(self) -> float | None:
        async with self._connection.execute(
            "SELECT min(learning_retry_at)-CAST(strftime('%s','now') AS INTEGER) "
            "FROM conversation_turns WHERE learning_state=1 AND learning_error IS NOT NULL"
        ) as cursor:
            delay = (await cursor.fetchone())[0]
        return max(1, delay) if delay is not None else None

    async def wake_learning(self, user: str):
        await self._connection.execute(
            "UPDATE conversation_turns SET learning_retry_at=0 "
            "WHERE user_id=? AND learning_state=1 AND learning_error IN "
            "('MEMORY_MODEL_UNAVAILABLE','MEMORY_BUDGET_EXCEEDED','MEMORY_STATE_CHANGED')",
            (user,),
        )
        self.learning_wakeup.set()

    async def discard_thread_learning(self, user_id: str, thread_id: str):
        await self._connection.execute(
            """UPDATE conversation_turns SET learning_state=0
               WHERE user_id=? AND thread_id=? AND learning_state=1""",
            (user_id, thread_id),
        )

    async def has_turn(self, *, user_id: str, thread_id: str, run_id: str) -> bool:
        """user_id/thread_id/run_id：用户、会话和执行标识。 / User, conversation and run IDs."""
        async with self._connection.execute(
            "SELECT 1 FROM conversation_turns WHERE user_id=? AND thread_id=? AND run_id=?",
            (user_id.strip(), thread_id.strip(), run_id.strip()),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def list_turns(
        self, *, user_id: str, thread_id: str, after_id: int = 0, limit: int = 50
    ) -> list[tuple[int, ConversationTurn]]:
        """按写入顺序读取会话记录。 / Read one conversation in insertion order.

        Args:
            user_id: 由调用方认证的用户。 / User authenticated by the caller.
            thread_id: 该用户的会话。 / Conversation within that user.
            after_id: 上一页末尾的记录 ID，首页为 0。 / Last row ID from the previous page.
            limit: 每页 1 至 200 轮。 / Page size from 1 to 200 turns.
        """
        user_id, thread_id = user_id.strip(), thread_id.strip()
        if not user_id or not thread_id:
            raise ValueError("user_id and thread_id must not be blank")
        if not 0 <= after_id <= 2**63 - 1 or not 1 <= limit <= 200:
            raise ValueError("Invalid conversation history page")
        async with self._connection.execute(
            """
            SELECT id, run_id, user_input, response, attachment_ids, activity, metrics,
                   used_memory_ids, profile_version
            FROM conversation_turns
            WHERE user_id=? AND thread_id=? AND id>?
            ORDER BY id LIMIT ?
            """,
            (user_id, thread_id, after_id, limit),
        ) as cursor:
            return [
                (
                    row_id,
                    ConversationTurn(
                        user_id,
                        thread_id,
                        run_id,
                        user_input,
                        response,
                        attachment_ids=tuple(json.loads(files)),
                        activity=tuple(
                            RunActivity.model_validate(item) for item in json.loads(trace)
                        ),
                        metrics=RunMetrics.model_validate_json(metrics) if metrics else None,
                        used_memory_ids=tuple(json.loads(memory_ids)),
                        profile_version=profile_version,
                    ),
                )
                async for (
                    row_id,
                    run_id,
                    user_input,
                    response,
                    files,
                    trace,
                    metrics,
                    memory_ids,
                    profile_version,
                ) in cursor
            ]

    async def save_attachment(self, user, thread, file_id, filename, size, content):
        async with self._connection.execute(
            """INSERT INTO conversation_files
               SELECT ?, ?, ?, ?, ?, ? WHERE (
                 SELECT coalesce(sum(byte_size), 0) FROM conversation_files
                 WHERE user_id=? AND thread_id=?
               ) + ? <= ?""",
            (file_id, user, thread, filename, size, content, user, thread, size, 24 * 1024 * 1024),
        ) as cursor:
            return cursor.rowcount == 1

    async def list_attachments(
        self, user: str, limit: int, offset: int, thread_id: str | None = None
    ) -> list[dict]:
        async with self._connection.execute(
            """SELECT id, thread_id, filename, byte_size FROM conversation_files
               WHERE user_id=? AND (? IS NULL OR thread_id=?)
               ORDER BY rowid DESC LIMIT ? OFFSET ?""",
            (user, thread_id, thread_id, limit, offset),
        ) as cursor:
            return [
                dict(zip(("id", "thread_id", "filename", "byte_size"), row, strict=True))
                async for row in cursor
            ]

    async def read_attachments(self, user: str, thread: str, ids: list[str]) -> list[dict]:
        if not ids:
            return []
        placeholders = ",".join("?" for _ in ids)
        async with self._connection.execute(
            f"""SELECT id, filename, byte_size, content FROM conversation_files
                WHERE user_id=? AND thread_id=? AND id IN ({placeholders})""",
            (user, thread, *ids),
        ) as cursor:
            records = {
                row[0]: dict(zip(("id", "filename", "byte_size", "content"), row, strict=True))
                async for row in cursor
            }
        if len(records) != len(ids):
            raise LookupError("One or more files are unavailable in this conversation.")
        return [records[file_id] for file_id in ids]
