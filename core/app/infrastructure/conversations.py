"""Independent SQLite archive for turns, threads, attachments and account state.
/ 独立的 SQLite 会话归档，保存轮次、线程、附件与账号状态。"""

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
    """Own the conversation archive connection and its queries.

    Args:
        connection: Connection owned by open(). / 由 open() 持有的连接。
    """

    def __init__(self, connection: aiosqlite.Connection) -> None:
        """Bind the archive connection and create the learning wakeup event.

        Args:
            connection: Autocommit connection created by open().
                / 由 open() 创建、处于自动提交模式的连接。
        """
        self._connection = connection
        self.learning_wakeup = asyncio.Event()

    @classmethod
    @asynccontextmanager
    async def open(cls, settings: CheckpointSettings) -> AsyncIterator["SQLiteConversationStore"]:
        """Open the sessions database and apply schema plus migrations.

        The path is derived from the checkpoint settings to keep archives separate, and the
        checkpoint lock timeout is reused. WAL with synchronous FULL keeps commits durable.
        / 路径由快照配置派生以保持归档独立，并复用快照的锁等待。WAL 与 synchronous FULL
        保证提交持久。

        Args:
            settings: Checkpoint settings supplying the source path and lock wait.
                / 提供源路径与锁等待的快照配置。

        Returns:
            Async context manager yielding the opened store. / 产出已打开存储的异步上下文管理器。
        Raises:
            ValueError: When the derived file is the checkpoint file itself.
                / 派生文件与快照文件相同时抛出。
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
                CREATE TABLE IF NOT EXISTS command_events (
                    id INTEGER PRIMARY KEY, user_id TEXT, thread_id TEXT, name TEXT,
                    kind TEXT DEFAULT 'command',
                    recorded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
                CREATE TABLE IF NOT EXISTS reference_events (
                    id INTEGER PRIMARY KEY, user_id TEXT, selected INTEGER, included INTEGER,
                    ok INTEGER, recorded_at TEXT DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
                );
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
                ("explicit_references", "TEXT NOT NULL DEFAULT '[]'"),
                ("memory_trace", "TEXT NOT NULL DEFAULT '{}'"),
                ("profile_version", "INTEGER"),
                ("reasoning", "TEXT NOT NULL DEFAULT ''"),
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

    async def record_command(self, user: str, thread: str, name: str):
        """Record command lifecycle separately from tools and usage. / 命令审计独立于工具与用量。"""
        await self._connection.execute(
            "INSERT INTO command_events(user_id,thread_id,name) VALUES (?,?,?)",
            (user, thread, name),
        )

    async def record_reference(self, user: str, selected: int, included: int, ok: bool):
        """Count preflight outcomes without storing paths or content. / 仅记录引用预检计数。"""
        await self._connection.execute(
            "INSERT INTO reference_events(user_id,selected,included,ok) VALUES (?,?,?,?)",
            (user, selected, included, int(ok)),
        )

    async def composer_usage(self, user: str):
        """Local command counts and reference-validation outcomes. / 本地命令与引用校验指标。"""
        async with self._connection.execute(
            "SELECT name,count(*) FROM command_events WHERE user_id=? GROUP BY name ORDER BY name",
            (user,),
        ) as cursor:
            commands = [dict(name=name, calls=count) async for name, count in cursor]
        async with self._connection.execute(
            "SELECT count(*),coalesce(sum(ok),0),"
            "coalesce(sum(selected),0),coalesce(sum(included),0) "
            "FROM reference_events WHERE user_id=?",
            (user,),
        ) as cursor:
            attempts, successful, selected, included = await cursor.fetchone()
        return {
            "commands": commands,
            "references": {
                "attempts": attempts,
                "successful": successful,
                "failed": attempts - successful,
                "selected": selected,
                "included": included,
                "success_rate": successful / attempts if attempts else None,
            },
        }

    async def create_thread(
        self, user_id: str, thread_id: str, title: str, mode: str, workspace_id: str | None = None
    ) -> dict:
        """Insert a session row and return its public fields.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: New thread identifier. / 新线程标识。
            title: Initial title, empty until generated. / 初始标题，生成前为空。
            mode: Either chat or work. / chat 或 work。
            workspace_id: Optional logical workspace. / 可选逻辑工作区。

        Returns:
            The created thread fields. / 创建后的线程字段。
        """
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
        """Convert a positional thread row into a public mapping.

        Args:
            row: Row with thread_id, title, mode, archived, timestamps and optionals.
                / 含线程 ID、标题、模式、归档标记、时间戳与可选字段的行。

        Returns:
            Mapping with booleans normalized and unset optionals omitted.
            / 布尔值已归一化、未设置可选项被省略的映射。
        """
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
        """Load one thread owned by the user.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。

        Returns:
            Thread fields, or None when absent. / 线程字段；不存在时为 None。
        """
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
        """List one page of threads by archive state and workspace.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            archived: Whether to list archived or active threads. / 列出归档还是活跃线程。
            limit: Page size. / 每页数量。
            offset: Rows to skip. / 跳过的行数。
            workspace_id: Optional workspace filter; personal includes unset rows.
                / 可选工作区过滤；personal 包含未设置工作区的行。

        Returns:
            Thread mappings ordered by last update. / 按最近更新排序的线程映射。
        """
        async with self._connection.execute(
            """SELECT thread_id, title, mode, archived, created_at, updated_at, workspace_id, pinned
               FROM conversation_threads WHERE user_id=? AND archived=?
               AND (? IS NULL OR coalesce(workspace_id, 'personal')=?)
               ORDER BY updated_at DESC, thread_id DESC LIMIT ? OFFSET ?""",
            (user_id, archived, workspace_id, workspace_id, limit, offset),
        ) as cursor:
            return [self._thread_dict(row) async for row in cursor]

    async def update_thread(self, user_id: str, thread_id: str, values: dict) -> None:
        """Patch mutable thread fields after whitelist filtering.

        Only archived, title, workspace_id and pinned are written; unknown keys are ignored.
        / 仅写入 archived、title、workspace_id 与 pinned；未知键被忽略。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。
            values: Field values to update. / 要更新的字段值。
        """
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
        """Load one workspace, including the implicit personal one.

        Args:
            user: Owning user id. / 所属用户 ID。
            workspace_id: Workspace identifier. / 工作区标识。

        Returns:
            Workspace mapping, or None when unknown. / 工作区映射；未知时为 None。
        """
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
        """List workspaces, synthesizing personal when it is absent.

        Args:
            user: Owning user id. / 所属用户 ID。
            limit: Page size. / 每页数量。
            offset: Rows to skip. / 跳过的行数。

        Returns:
            Workspace mappings with personal first. / personal 排在最前的工作区映射。
        """
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
        """Create a workspace or rename an existing one.

        The directory is written on insert only; renaming keeps the original directory.
        / 目录仅在插入时写入；重命名会保留原有目录。

        Args:
            user: Owning user id. / 所属用户 ID。
            workspace_id: Workspace identifier. / 工作区标识。
            name: Display name. / 显示名称。
            directory: Optional absolute directory for new workspaces. / 新工作区的可选绝对目录。

        Returns:
            The stored workspace mapping. / 已保存的工作区映射。
        """
        await self._connection.execute(
            """INSERT INTO conversation_workspaces (user_id,id,name,directory) VALUES (?, ?, ?, ?)
               ON CONFLICT (user_id, id) DO UPDATE SET name=excluded.name""",
            (user, workspace_id, name, directory),
        )
        return await self.get_workspace(user, workspace_id)

    async def workspace_threads(self, user: str, workspace_id: str) -> list[str]:
        """List thread ids belonging to one workspace.

        Args:
            user: Owning user id. / 所属用户 ID。
            workspace_id: Workspace identifier. / 工作区标识。

        Returns:
            Thread ids in storage order. / 按存储顺序排列的线程 ID。
        """
        async with self._connection.execute(
            "SELECT thread_id FROM conversation_threads WHERE user_id=? AND workspace_id=?",
            (user, workspace_id),
        ) as cursor:
            return [row[0] async for row in cursor]

    async def delete_workspace(self, user: str, workspace_id: str):
        """Delete a workspace row.

        Args:
            user: Owning user id. / 所属用户 ID。
            workspace_id: Workspace identifier. / 工作区标识。
        """
        await self._connection.execute(
            "DELETE FROM conversation_workspaces WHERE user_id=? AND id=?", (user, workspace_id)
        )

    async def copy_turns(self, user_id: str, source_id: str, target_id: str) -> None:
        """Copy a thread's turns and referenced attachments for a fork.

        Only files referenced by copied turns are duplicated; forks keep original run ids
        so activity and metrics can be attributed without a new model call.
        / 仅复制被复制轮次引用的附件；派生线程保留原 run id，以便无需新的模型调用即可归属
        活动与指标。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            source_id: Thread to copy from. / 复制来源线程。
            target_id: Thread to copy into. / 复制目标线程。
        """
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
                   user_id, thread_id, run_id, user_input, response, reasoning,
                   attachment_ids, activity, metrics, used_memory_ids, profile_version,
                   explicit_references, memory_trace, recorded_at
               )
               SELECT user_id, ?, run_id, user_input, response, reasoning,
                      attachment_ids, activity, metrics, used_memory_ids,
                      profile_version, explicit_references, memory_trace, recorded_at
               FROM conversation_turns WHERE user_id=? AND thread_id=? ORDER BY id""",
            (target_id, user_id, source_id),
        ):
            pass

    async def clear_thread(self, user_id: str, thread_id: str) -> None:
        """Delete a thread's turns and attachments but keep the thread row.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread whose history is cleared. / 被清空历史的线程。
        """
        async with self._connection.execute(
            "DELETE FROM conversation_turns WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ):
            pass
        await self._connection.execute(
            "DELETE FROM conversation_files WHERE user_id=? AND thread_id=?", (user_id, thread_id)
        )

    async def delete_thread(self, user_id: str, thread_id: str) -> None:
        """Delete a thread, its turns and its attachments.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread to delete. / 要删除的线程。
        """
        await self.clear_thread(user_id, thread_id)
        async with self._connection.execute(
            "DELETE FROM conversation_threads WHERE user_id=? AND thread_id=?",
            (user_id, thread_id),
        ):
            pass

    async def get_account(self, user_id: str) -> dict:
        """Load the local account profile fields.

        Args:
            user_id: Account owner. / 账号所属用户。

        Returns:
            display_name, handle and avatar_version, or an empty mapping without a row.
            / display_name、handle 与 avatar_version；无记录时为空映射。
        """
        async with self._connection.execute(
            "SELECT display_name, handle, avatar_version FROM local_accounts WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        if row is None:
            return {}
        return dict(zip(("display_name", "handle", "avatar_version"), row, strict=True))

    async def update_account(self, user_id: str, display_name: str | None, handle: str | None):
        """Upsert display name and handle for the local account.

        Args:
            user_id: Account owner. / 账号所属用户。
            display_name: Display name, or None to clear. / 显示名称；None 表示清空。
            handle: Handle, or None to clear. / 用户名；None 表示清空。
        """
        await self._connection.execute(
            """INSERT INTO local_accounts(user_id, display_name, handle) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 display_name=excluded.display_name, handle=excluded.handle""",
            (user_id, display_name, handle),
        )

    async def get_avatar(self, user_id: str) -> bytes | None:
        """Load the stored avatar bytes.

        Args:
            user_id: Account owner. / 账号所属用户。

        Returns:
            PNG bytes, or None when no avatar is stored. / PNG 字节；未存储头像时为 None。
        """
        async with self._connection.execute(
            "SELECT avatar FROM local_accounts WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
        return row[0] if row else None

    async def set_avatar(self, user_id: str, image: bytes | None, version: str | None):
        """Upsert avatar bytes and version.

        Args:
            user_id: Account owner. / 账号所属用户。
            image: Validated PNG bytes, or None to remove. / 已校验的 PNG 字节；None 表示移除。
            version: Opaque avatar version string. / 不透明的头像版本字符串。
        """
        await self._connection.execute(
            """INSERT INTO local_accounts(user_id, avatar, avatar_version) VALUES (?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 avatar=excluded.avatar, avatar_version=excluded.avatar_version""",
            (user_id, image, version),
        )

    async def account_runs(self, user_id: str) -> AsyncIterator[tuple]:
        """Yield one activity row per distinct completed run.

        Forks reuse the original run id and activity, so duplicates are collapsed to the
        first row of each run id. / 派生线程复用原 run id 与活动，因此按 run id 去重为第一行。

        Args:
            user_id: Owning user id. / 所属用户 ID。

        Yields:
            (recorded_at, mode, metrics_json, activity_json) tuples. / 元组序列。
        """
        # English: Forks copy the original run ID and activity without a new model call.
        # 中文：派生线程复制原始 run ID 与活动，不执行新的模型调用。
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
        """Load the persisted memory configuration and revision.

        Args:
            user_id: Owning user id. / 所属用户 ID。

        Returns:
            (revision, configuration) or None when never saved. / 从未保存时为 None。
        """
        async with self._connection.execute(
            "SELECT revision, configuration_json FROM memory_configuration WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return (row[0], json.loads(row[1])) if row else None

    async def save_memory_configuration(
        self, user_id: str, configuration: dict, expected_revision: int
    ) -> int | None:
        """Write a memory configuration if the revision still matches.

        A single atomic optimistic statement prevents a stale editor from overwriting newer
        settings. / 单条原子乐观语句防止旧编辑器覆盖更新的设置。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            configuration: Configuration mapping to persist. / 要持久化的配置映射。
            expected_revision: Revision the caller read. / 调用方读到的修订号。

        Returns:
            The new revision, or None when the expectation was stale.
            / 新修订号；期望已过期时为 None。
        """
        # English: One atomic optimistic write; stale editors never overwrite newer settings.
        # 中文：单次原子乐观写入；过期编辑不会覆盖更新的设置。
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
        """Report whether personalization is enabled for the user.

        Users without a stored preference default to enabled.
        / 未存储偏好的用户默认为开启。

        Args:
            user_id: Owning user id. / 所属用户 ID。

        Returns:
            Effective global personalization gate. / 有效的全局个性化开关。
        """
        async with self._connection.execute(
            "SELECT personalization_enabled FROM conversation_preferences WHERE user_id=?",
            (user_id,),
        ) as cursor:
            row = await cursor.fetchone()
            return bool(row[0]) if row else True

    async def set_personalization(self, user_id: str, enabled: bool) -> None:
        """Persist the personalization gate and pause queued learning.

        Disabling clears pending learning states and wakes the worker; enabling also wakes it.
        / 关闭时会清除待处理的学习状态并唤醒工作器；开启时同样唤醒。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            enabled: New gate value. / 新的开关值。
        """
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
        """Archive one completed turn idempotently.

        The turn is written only after the run completes; learning stays queued only when the
        turn requested learning and the user gate is on. A duplicate run id is ignored.
        / 仅在运行完成后写入轮次；只有该轮请求学习且用户开关开启时才排队学习。重复 run id 被忽略。

        Args:
            turn: Completed turn extracted from the final graph snapshot.
                / 从最终图快照提取的已完成轮次。

        Raises:
            ValueError: When user, thread or run id is blank. / 用户、线程或 run id 为空时抛出。
        """
        user_id, thread_id, run_id = (
            turn.user_id.strip(),
            turn.thread_id.strip(),
            turn.run_id.strip(),
        )
        if not user_id or not thread_id or not run_id:
            raise ValueError("user_id, thread_id and run_id must not be blank")
        # English: Autocommit one statement so concurrent sessions share no open transaction.
        # 中文：单条语句自动提交，并发会话不共享未提交的事务。
        async with self._connection.execute(
            """
            INSERT INTO conversation_turns (
                user_id, thread_id, run_id, user_input, response, reasoning,
                learning_state, learning_project_id, attachment_ids, activity, metrics,
                used_memory_ids, profile_version, explicit_references, memory_trace, recorded_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CASE WHEN ?=1 AND coalesce((
                SELECT personalization_enabled FROM conversation_preferences WHERE user_id=?
            ),1)=1 THEN 1 ELSE 0 END, ?, ?, ?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            ON CONFLICT (user_id, thread_id, run_id) DO NOTHING
            """,
            (
                user_id,
                thread_id,
                run_id,
                turn.user_input,
                turn.response,
                turn.reasoning,
                int(turn.learn_memory),
                user_id,
                turn.project_id,
                json.dumps(turn.attachment_ids),
                json.dumps([entry.model_dump() for entry in turn.activity]),
                turn.metrics.model_dump_json() if turn.metrics else None,
                json.dumps(turn.used_memory_ids),
                turn.profile_version,
                json.dumps(turn.references, ensure_ascii=False),
                json.dumps(turn.memory_trace, ensure_ascii=False),
            ),
        ) as cursor:
            if cursor.rowcount and turn.learn_memory:
                self.learning_wakeup.set()

    async def learning_jobs(self, limit: int = 32) -> list[tuple]:
        """Load queued learning turns whose retry time has passed.

        Args:
            limit: Maximum jobs returned. / 返回任务数上限。

        Returns:
            Job tuples of id, user, thread, run, input, project and recorded_at.
            / 任务元组。
        """
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
        """Mark a queued turn as processed.

        The generation guard prevents an older worker from finishing a requeued turn.
        / 代数保护防止旧工作器完成已重新排队的轮次。

        Args:
            row_id: conversation_turns id. / 轮次行 ID。
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。
            run_id: Run identifier. / 运行标识。
            generation: Expected learning generation, or None to skip the guard.
                / 期望的学习代数；None 表示跳过保护。
        """
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
        """Reschedule a failed turn and record the failure.

        Counting attempts moves a turn to the dead state after five tries; retriable codes
        can be deferred without counting so they resume when the cause clears.
        / 计入次数会在五次后把轮次转为死信；可重试错误码可不计数推迟，待原因消除后恢复。

        Args:
            row_id: conversation_turns id. / 轮次行 ID。
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。
            run_id: Run identifier. / 运行标识。
            error: Stable failure code stored for diagnostics. / 用于诊断的稳定失败码。
            delay: Seconds before the next attempt. / 距下次尝试的秒数。
            count_attempt: Whether this failure counts toward the attempt cap.
                / 本次失败是否计入尝试上限。
        """
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
        """Report whether a turn is still queued for learning.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。
            run_id: Run identifier. / 运行标识。

        Returns:
            True while the turn waits in the learning queue. / 轮次仍在学习队列中时为 True。
        """
        async with self._connection.execute(
            """SELECT 1 FROM conversation_turns
               WHERE user_id=? AND thread_id=? AND run_id=? AND learning_state=1""",
            (user_id, thread_id, run_id),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def learning_status(self, user: str) -> dict:
        """Summarize learning queue health for one user.

        Args:
            user: Owning user id. / 所属用户 ID。

        Returns:
            Counts of total, pending, processed, failed and dead turns, the gate and last error.
            / 总数、待处理、已处理、失败与死信计数，门控值及最后错误。
        """
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
        """Requeue the most recent learned turns for one user.

        At most 500 rows are requeued, and the generation counter is bumped so in-flight
        workers cannot finish them. The queue is woken afterwards.
        / 最多重排 500 行，并递增代数使在途工作器无法完成它们；随后唤醒队列。

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Optional thread filter. / 可选线程过滤。

        Returns:
            Number of requeued rows. / 重排的行数。
        """
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
        """Load the learning state needed by the planner.

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread identifier. / 线程标识。
            run: Run identifier. / 运行标识。

        Returns:
            State, generation, metrics, attempts, text, project and timestamp, or None.
            / 状态、代数、指标、尝试次数、文本、项目与时间戳；不存在时为 None。
        """
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
        """Compute the wait before the next retryable job is due.

        Returns:
            Whole seconds to wait (at least one), or None when nothing is retrying.
            / 需要等待的整秒数（至少 1）；没有可重试任务时为 None。
        """
        async with self._connection.execute(
            "SELECT min(learning_retry_at)-CAST(strftime('%s','now') AS INTEGER) "
            "FROM conversation_turns WHERE learning_state=1 AND learning_error IS NOT NULL"
        ) as cursor:
            delay = (await cursor.fetchone())[0]
        return max(1, delay) if delay is not None else None

    async def wake_learning(self, user: str):
        """Clear retry delays for retriable learning failures.

        Only the model-unavailable, budget and state-changed codes are released; then the
        worker is woken. / 仅释放模型不可用、预算超限与状态变化三类错误码，随后唤醒工作器。

        Args:
            user: Owning user id. / 所属用户 ID。
        """
        await self._connection.execute(
            "UPDATE conversation_turns SET learning_retry_at=0 "
            "WHERE user_id=? AND learning_state=1 AND learning_error IN "
            "('MEMORY_MODEL_UNAVAILABLE','MEMORY_BUDGET_EXCEEDED','MEMORY_STATE_CHANGED')",
            (user,),
        )
        self.learning_wakeup.set()

    async def discard_thread_learning(self, user_id: str, thread_id: str):
        """Drop queued learning for one thread.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread whose queued turns are dropped. / 丢弃排队轮次的线程。
        """
        await self._connection.execute(
            """UPDATE conversation_turns SET learning_state=0
               WHERE user_id=? AND thread_id=? AND learning_state=1""",
            (user_id, thread_id),
        )

    async def has_turn(self, *, user_id: str, thread_id: str, run_id: str) -> bool:
        """Report whether a turn was already archived.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            thread_id: Thread identifier. / 线程标识。
            run_id: Run identifier. / 运行标识。

        Returns:
            True when the turn exists. / 轮次已存在时为 True。
        """
        async with self._connection.execute(
            "SELECT 1 FROM conversation_turns WHERE user_id=? AND thread_id=? AND run_id=?",
            (user_id.strip(), thread_id.strip(), run_id.strip()),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def list_turns(
        self,
        *,
        user_id: str,
        thread_id: str,
        after_id: int = 0,
        limit: int = 50,
        newest_first: bool = False,
    ) -> list[tuple[int, ConversationTurn]]:
        """Read one conversation in insertion order.

        Args:
            user_id: User authenticated by the caller. / 由调用方认证的用户。
            thread_id: Conversation within that user. / 该用户的会话。
            after_id: Last row id from the previous page; 0 for the first page.
                / 上一页末尾的行 ID；首页为 0。
            limit: Page size from 1 to 200 turns. / 每页 1 至 200 轮。

        Returns:
            Pairs of row id and hydrated ConversationTurn. / 行 ID 与水合后的轮次二元组。
        Raises:
            ValueError: When ids are blank or the page bounds are invalid.
                / ID 为空或分页边界非法时抛出。
        """
        user_id, thread_id = user_id.strip(), thread_id.strip()
        if not user_id or not thread_id:
            raise ValueError("user_id and thread_id must not be blank")
        if not 0 <= after_id <= 2**63 - 1 or not 1 <= limit <= 200:
            raise ValueError("Invalid conversation history page")
        async with self._connection.execute(
            f"""
            SELECT id, run_id, user_input, response, reasoning, attachment_ids, activity, metrics,
                   used_memory_ids, profile_version, explicit_references, memory_trace
            FROM conversation_turns
            WHERE user_id=? AND thread_id=? AND id>?
            ORDER BY id {"DESC" if newest_first else "ASC"} LIMIT ?
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
                        reasoning=reasoning,
                        attachment_ids=tuple(json.loads(files)),
                        activity=tuple(
                            RunActivity.model_validate(item) for item in json.loads(trace)
                        ),
                        metrics=RunMetrics.model_validate_json(metrics) if metrics else None,
                        used_memory_ids=tuple(json.loads(memory_ids)),
                        profile_version=profile_version,
                        references=tuple(json.loads(references)),
                        memory_trace=json.loads(memory_diagnostics),
                    ),
                )
                async for (
                    row_id,
                    run_id,
                    user_input,
                    response,
                    reasoning,
                    files,
                    trace,
                    metrics,
                    memory_ids,
                    profile_version,
                    references,
                    memory_diagnostics,
                ) in cursor
            ]

    async def search_threads(self, user: str, query: str, limit: int = 8) -> list[dict]:
        """Search owned session titles and archived text. / 搜索所属会话标题与归档文本。"""
        async with self._connection.execute(
            """SELECT t.thread_id, t.title, t.updated_at,
               (SELECT count(*) FROM conversation_turns c
                WHERE c.user_id=t.user_id AND c.thread_id=t.thread_id)
               FROM conversation_threads t WHERE t.user_id=? AND (
                 instr(lower(t.title),lower(?))>0 OR EXISTS (
                   SELECT 1 FROM conversation_turns c WHERE c.user_id=t.user_id
                   AND c.thread_id=t.thread_id AND instr(lower(c.user_input),lower(?))>0))
               ORDER BY t.updated_at DESC, t.thread_id LIMIT ?""",
            (user, query, query, limit),
        ) as cursor:
            return [
                dict(id=row[0], title=row[1], updated_at=row[2], message_count=row[3])
                async for row in cursor
            ]

    async def save_attachment(self, user, thread, file_id, filename, size, content):
        """Store extracted attachment text under a per-thread quota.

        The conditional insert enforces the 24 MiB thread quota atomically.
        / 条件插入以原子方式执行每线程 24 MiB 配额。

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread receiving the file. / 接收文件的线程。
            file_id: Generated attachment id. / 生成的附件 ID。
            filename: Original display filename. / 原始显示文件名。
            size: Original byte size. / 原始字节大小。
            content: Extracted UTF-8 text. / 提取出的 UTF-8 文本。

        Returns:
            True when the row was stored, False when the quota was exceeded.
            / 已存储为 True；超出配额为 False。
        """
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
        """List attachment metadata, optionally for one thread.

        Args:
            user: Owning user id. / 所属用户 ID。
            limit: Page size. / 每页数量。
            offset: Rows to skip. / 跳过的行数。
            thread_id: Optional thread filter. / 可选线程过滤。

        Returns:
            Attachment metadata ordered newest first. / 按从新到旧排列的附件元数据。
        """
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

    async def has_attachment(self, user: str, thread: str, identity: str) -> bool:
        """Check owned attachment metadata without reading its text. / 仅检查附件归属和存在性。"""
        async with self._connection.execute(
            "SELECT 1 FROM conversation_files WHERE user_id=? AND thread_id=? AND id=?",
            (user, thread, identity),
        ) as cursor:
            return await cursor.fetchone() is not None

    async def read_attachments(self, user: str, thread: str, ids: list[str]) -> list[dict]:
        """Read attachment text by id for one thread.

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread owning the files. / 文件所属线程。
            ids: Requested attachment ids. / 请求的附件 ID。

        Returns:
            Records in the requested order. / 按请求顺序排列的记录。
        Raises:
            LookupError: When any requested file is missing. / 任一请求文件缺失时抛出。
        """
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
