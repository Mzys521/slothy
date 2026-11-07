"""SQLite-backed user profile, memory claims and retrieval state.
/ SQLite 支持的用户画像、记忆断言与检索状态。"""

import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import aiosqlite

from app.agent.graph.services import Snapshot
from app.personalization.context import ContextBuildResult

NOW = "strftime('%Y-%m-%dT%H:%M:%fZ', 'now')"
PROFILE_HISTORY_LIMIT = 200
EPISODE_QUERY_NOISE = re.compile(
    r"你还?记得|记得|帮我|请问|上一次|上次|上回|上周|昨天|今天|前天|之前|以前|最近|当时|"
    r"我们|我的|这次|完成了?|做了|什么|哪个|怎么|怎样|如何|告诉我|这个项目|本项目|工作区|"
    r"(?:回忆|回顾)(?:一下)?|\b(?:a|an|the|i|we|do|did|does|you|my|our|of|in|on|to|"
    r"was|were|is|are|be|and|what|when|where|how|about|for|me|it|this|that|last|time|remember|"
    r"previous|yesterday|today|recently)\b",
    re.I,
)


def memory_terms(query: str, limit: int) -> list[str]:
    """Split a query into searchable words and Han character bigrams.

    Args:
        query: Raw user query. / 原始用户查询。
        limit: Maximum distinct terms returned. / 返回的去重词项上限。

    Returns:
        Deduplicated terms in first-seen order. / 按首次出现顺序去重的词项。
    """
    # English: Match words and adjacent Han characters without requiring spaces.
    # 中文：匹配词与相邻汉字二元组，兼容无空格的中文查询。
    words = re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", query.lower())
    terms = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.extend(word[index : index + 2] for index in range(len(word) - 1))
        elif len(word) >= 2:
            terms.append(word)
    return list(dict.fromkeys(terms))[:limit]


class WorkspaceStore:
    """Own the workspace schema and user-scoped memory/profile access.

    The connection is created and migrated by open; every method must filter by user.
    / 连接由 open 创建并迁移；每个方法都必须按用户过滤。
    """

    def __init__(self, connection: aiosqlite.Connection):
        """Bind an already-open workspace connection.

        Args:
            connection: Connection whose row factory yields mappings.
                / 行工厂返回映射的连接。
        """
        self.connection = connection

    async def learning_usage(self, user: str):
        """Yield recorded daily learning usage rows for one user.

        Args:
            user: Owning user id. / 所属用户 ID。

        Yields:
            (created_at, total_tokens) pairs in chronological order. / 按时间顺序的用量二元组。
        """
        async with self.connection.execute(
            "SELECT created_at,total_tokens FROM memory_learning_usage "
            "WHERE user_id=? ORDER BY created_at,id",
            (user,),
        ) as rows:
            async for row in rows:
                yield tuple(row)

    @classmethod
    @asynccontextmanager
    async def open(cls, path: Path, timeout: float) -> AsyncIterator["WorkspaceStore"]:
        """Open the database, create tables, triggers and run migrations.

        The schema enables WAL and foreign keys; additive migrations keep older databases
        readable without rewriting existing rows.
        / 结构启用 WAL 与外键；增量迁移让旧库可读且不重写既有行。

        Args:
            path: Database file path. / 数据库文件路径。
            timeout: SQLite lock wait in seconds. / SQLite 锁等待秒数。

        Returns:
            Async context manager yielding the connected store.
            / 产出已连接存储的异步上下文管理器。
        """
        async with aiosqlite.connect(path, timeout=timeout, isolation_level=None) as connection:
            connection.row_factory = aiosqlite.Row
            async with connection.executescript(f"""
                PRAGMA journal_mode=WAL;
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS workspace_memories (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    content TEXT NOT NULL,
                    type TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    scope TEXT NOT NULL,
                    scope_ref TEXT,
                    source TEXT,
                    importance REAL NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({NOW}),
                    updated_at TEXT NOT NULL DEFAULT ({NOW})
                );
                CREATE INDEX IF NOT EXISTS workspace_memory_filter
                    ON workspace_memories (user_id, status, scope, scope_ref, created_at);
                CREATE INDEX IF NOT EXISTS workspace_graph_order
                    ON workspace_memories (user_id, status, importance DESC, id);
                CREATE TABLE IF NOT EXISTS workspace_memory_links (
                    source_id TEXT NOT NULL REFERENCES workspace_memories(id) ON DELETE CASCADE,
                    target_id TEXT NOT NULL REFERENCES workspace_memories(id) ON DELETE CASCADE,
                    relation TEXT NOT NULL,
                    relevance REAL NOT NULL,
                    PRIMARY KEY (source_id, target_id)
                );
                CREATE TABLE IF NOT EXISTS workspace_memory_feedback (
                    id TEXT PRIMARY KEY,
                    memory_id TEXT NOT NULL REFERENCES workspace_memories(id) ON DELETE CASCADE,
                    feedback_type TEXT NOT NULL,
                    feedback_content TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({NOW})
                );
                CREATE TABLE IF NOT EXISTS workspace_profiles (
                    user_id TEXT NOT NULL,
                    version INTEGER NOT NULL,
                    profile_json TEXT NOT NULL,
                    change_summary TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT ({NOW}),
                    PRIMARY KEY (user_id, version)
                );
                CREATE TABLE IF NOT EXISTS memory_claims (
                    id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_ref TEXT,
                    profile_key TEXT NOT NULL,
                    dimension TEXT NOT NULL,
                    value TEXT NOT NULL,
                    title TEXT NOT NULL,
                    status TEXT NOT NULL,
                    memory_id TEXT REFERENCES workspace_memories(id) ON DELETE SET NULL,
                    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
                );
                CREATE INDEX IF NOT EXISTS memory_claims_user_status
                    ON memory_claims(user_id, status, scope, scope_ref, profile_key);
                CREATE INDEX IF NOT EXISTS memory_claims_memory ON memory_claims(memory_id);
                CREATE TABLE IF NOT EXISTS memory_evidence (
                    claim_id TEXT NOT NULL REFERENCES memory_claims(id) ON DELETE CASCADE,
                    thread_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    quote TEXT NOT NULL,
                    source TEXT NOT NULL,
                    recorded_at TEXT,
                    PRIMARY KEY (claim_id, thread_id, run_id)
                );
                CREATE INDEX IF NOT EXISTS memory_evidence_turn
                    ON memory_evidence(thread_id, run_id);
                CREATE TABLE IF NOT EXISTS memory_learning_turns (
                    user_id TEXT NOT NULL, thread_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    generation INTEGER NOT NULL, outcome TEXT NOT NULL,
                    PRIMARY KEY(user_id,thread_id,run_id,generation)
                );
                CREATE TABLE IF NOT EXISTS memory_learning_exclusions (
                    user_id TEXT NOT NULL, thread_id TEXT NOT NULL, run_id TEXT NOT NULL,
                    PRIMARY KEY(user_id,thread_id,run_id)
                );
                CREATE TABLE IF NOT EXISTS memory_learning_usage (
                    id TEXT PRIMARY KEY, user_id TEXT NOT NULL, day TEXT NOT NULL,
                    model TEXT, reserved_tokens INTEGER NOT NULL,
                    input_tokens INTEGER, output_tokens INTEGER, total_tokens INTEGER,
                    state TEXT NOT NULL DEFAULT 'reserved', error TEXT,
                    created_at TEXT NOT NULL DEFAULT ({NOW})
                );
                CREATE INDEX IF NOT EXISTS memory_usage_day ON memory_learning_usage(user_id,day);
                CREATE TABLE IF NOT EXISTS memory_meta (
                    user_id TEXT PRIMARY KEY, memory_index_version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS retrieval_cache (
                    cache_key TEXT PRIMARY KEY, user_id TEXT NOT NULL, payload TEXT NOT NULL,
                    created_at REAL NOT NULL, expires_at REAL NOT NULL
                );
                CREATE INDEX IF NOT EXISTS retrieval_cache_expiry ON retrieval_cache(expires_at);
                CREATE INDEX IF NOT EXISTS retrieval_cache_owner ON retrieval_cache(user_id);
                CREATE TABLE IF NOT EXISTS prompt_profile_snapshots (
                    user_id TEXT NOT NULL, thread_id TEXT NOT NULL,
                    snapshot_json TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1,
                    PRIMARY KEY(user_id, thread_id)
                );
                CREATE TRIGGER IF NOT EXISTS memory_revision_insert
                AFTER INSERT ON workspace_memories
                BEGIN
                    INSERT INTO memory_meta VALUES(NEW.user_id, 1)
                    ON CONFLICT(user_id) DO UPDATE SET memory_index_version=memory_index_version+1;
                END;
                CREATE TRIGGER IF NOT EXISTS memory_revision_update
                AFTER UPDATE ON workspace_memories
                BEGIN
                    INSERT INTO memory_meta VALUES(NEW.user_id, 1)
                    ON CONFLICT(user_id) DO UPDATE SET memory_index_version=memory_index_version+1;
                END;
                CREATE TRIGGER IF NOT EXISTS memory_revision_delete
                AFTER DELETE ON workspace_memories
                BEGIN
                    INSERT INTO memory_meta VALUES(OLD.user_id, 1)
                    ON CONFLICT(user_id) DO UPDATE SET memory_index_version=memory_index_version+1;
                    DELETE FROM retrieval_cache WHERE user_id=OLD.user_id;
                END;
                """):
                pass
            async with connection.execute("PRAGMA table_info(workspace_profiles)") as rows:
                columns = {row[1] async for row in rows}
            if "attributes_json" not in columns:
                await connection.execute(
                    "ALTER TABLE workspace_profiles ADD COLUMN attributes_json TEXT DEFAULT '[]'"
                )
            if "removed_attributes" not in columns:
                await connection.execute(
                    "ALTER TABLE workspace_profiles ADD COLUMN "
                    "removed_attributes INTEGER NOT NULL DEFAULT 0"
                )
            if "redacted_attributes" not in columns:
                await connection.execute(
                    "ALTER TABLE workspace_profiles ADD COLUMN "
                    "redacted_attributes INTEGER NOT NULL DEFAULT 0"
                )
            async with connection.execute("PRAGMA table_info(workspace_memory_links)") as rows:
                link_columns = {row[1] async for row in rows}
            for name, declaration in (
                ("directed", "INTEGER NOT NULL DEFAULT 0"),
                ("origin", "TEXT NOT NULL DEFAULT 'manual'"),
                ("suppressed", "INTEGER NOT NULL DEFAULT 0"),
            ):
                if name not in link_columns:
                    await connection.execute(
                        f"ALTER TABLE workspace_memory_links ADD COLUMN {name} {declaration}"
                    )
            await connection.execute(
                "CREATE INDEX IF NOT EXISTS workspace_links_target "
                "ON workspace_memory_links(target_id)"
            )
            async with connection.execute("PRAGMA table_info(memory_evidence)") as rows:
                columns = {row[1] async for row in rows}
            if "recorded_at" not in columns:
                await connection.execute("ALTER TABLE memory_evidence ADD COLUMN recorded_at TEXT")
            async with connection.execute("PRAGMA table_info(memory_claims)") as rows:
                claim_columns = {row[1] async for row in rows}
            for name, declaration in (
                ("confidence", "REAL"),
                ("importance", "REAL"),
                ("learned_by", "TEXT"),
                ("sensitivity", "TEXT"),
                ("last_source_at", "TEXT"),
            ):
                if name not in claim_columns:
                    await connection.execute(
                        f"ALTER TABLE memory_claims ADD COLUMN {name} {declaration}"
                    )
            # English: Retrieval also reads claim keys and evidence dates. Triggers cover writers.
            # 中文：检索还会读取断言键与证据日期；这些触发器覆盖所有写入方。
            for table in ("memory_claims", "memory_evidence"):
                for event in ("INSERT", "UPDATE", "DELETE"):
                    ref = "OLD" if event == "DELETE" else "NEW"
                    owner = (
                        f"SELECT {ref}.user_id AS user_id"
                        if table == "memory_claims"
                        else f"SELECT user_id FROM memory_claims WHERE id={ref}.claim_id"
                    )
                    async with connection.executescript(f"""
                        CREATE TRIGGER IF NOT EXISTS cache_revision_{table}_{event.lower()}
                        AFTER {event} ON {table}
                        BEGIN
                            INSERT INTO memory_meta(user_id,memory_index_version)
                            SELECT user_id,1 FROM ({owner}) WHERE true
                            ON CONFLICT(user_id) DO UPDATE SET
                                memory_index_version=memory_index_version+1;
                        END;
                    """):
                        pass
            yield cls(connection)

    async def list_memories(
        self,
        user_id: str,
        *,
        status: str = "active",
        memory_type: str | None = None,
        scope: str | None = None,
        limit: int = 50,
        cursor: str | None = None,
    ) -> list[Snapshot]:
        """List a user's memories filtered by status, type and scope.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            status: Memory status filter, active by default. / 记忆状态过滤，默认 active。
            memory_type: Optional memory type filter. / 可选记忆类型过滤。
            scope: Optional scope filter. / 可选作用域过滤。
            limit: Maximum rows returned. / 返回行数上限。
            cursor: Last id from the previous page, or None. / 上一页最后一个 ID 或 None。

        Returns:
            Memory snapshots ordered by id. / 按 ID 排序的记忆快照。
        """
        async with self.connection.execute(
            """SELECT * FROM workspace_memories
               WHERE user_id=? AND status=? AND (? IS NULL OR type=?)
                 AND (? IS NULL OR scope=?) AND (? IS NULL OR id>?)
               ORDER BY id LIMIT ?""",
            (user_id, status, memory_type, memory_type, scope, scope, cursor, cursor, limit),
        ) as rows:
            return [dict(row) async for row in rows]

    async def get_memory(self, user_id: str, memory_id: str) -> Snapshot | None:
        """Load one memory owned by the user.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。

        Returns:
            The memory snapshot, or None when absent or not owned.
            / 记忆快照；不存在或不属于该用户时为 None。
        """
        async with self.connection.execute(
            "SELECT * FROM workspace_memories WHERE user_id=? AND id=?",
            (user_id, memory_id),
        ) as rows:
            row = await rows.fetchone()
            return dict(row) if row else None

    async def create_memory(self, user_id: str, values: dict) -> Snapshot:
        """Insert a memory with a generated id.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            values: Title, content, type, scope, scope_ref, source and importance.
                / 标题、内容、类型、作用域、作用域引用、来源与重要性。

        Returns:
            The inserted memory snapshot. / 插入后的记忆快照。
        """
        async with self.connection.execute(
            """INSERT INTO workspace_memories
               (id,user_id,title,content,type,scope,scope_ref,source,importance)
               VALUES (:id,:user_id,:title,:content,:type,:scope,:scope_ref,:source,:importance)
               RETURNING *""",
            {**values, "id": uuid4().hex, "user_id": user_id},
        ) as rows:
            return dict(await rows.fetchone())

    async def update_memory(self, user_id: str, memory_id: str, values: dict) -> Snapshot | None:
        """Update mutable memory fields for one owned memory.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。
            values: Subset of title, content, source, importance and status.
                / title、content、source、importance 与 status 的子集。

        Returns:
            The updated snapshot, or None when the memory does not exist.
            / 更新后的快照；记忆不存在时为 None。
        Raises:
            ValueError: When values contain unsupported fields. / 含不支持的字段时抛出。
        """
        if not values:
            return await self.get_memory(user_id, memory_id)
        columns = {"title", "content", "source", "importance", "status", "protected"}
        if not values.keys() <= columns:
            raise ValueError("Unsupported memory fields")
        assignments = ", ".join(f"{column}=?" for column in values)
        async with self.connection.execute(
            f"""UPDATE workspace_memories SET {assignments}, updated_at={NOW}
                WHERE user_id=? AND id=? RETURNING *""",
            (*values.values(), user_id, memory_id),
        ) as rows:
            row = await rows.fetchone()
            return dict(row) if row else None

    async def delete_memory(self, user_id: str, memory_id: str) -> bool:
        """Delete one owned memory.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            memory_id: Memory identifier. / 记忆标识。

        Returns:
            True when exactly one row was deleted. / 恰好删除一行时为 True。
        """
        async with self.connection.execute(
            "DELETE FROM workspace_memories WHERE user_id=? AND id=?", (user_id, memory_id)
        ) as cursor:
            return cursor.rowcount == 1

    async def add_feedback(self, user_id: str, memory_id: str, values: dict) -> Snapshot | None:
        """Attach user feedback to an existing owned memory.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            memory_id: Memory receiving the feedback. / 接收反馈的记忆。
            values: feedback_type and feedback_content. / feedback_type 与 feedback_content。

        Returns:
            The feedback row, or None when the memory is not owned.
            / 反馈行；记忆不属于该用户时为 None。
        """
        async with self.connection.execute(
            """INSERT INTO workspace_memory_feedback
               (id,memory_id,feedback_type,feedback_content)
               SELECT ?, id, ?, ? FROM workspace_memories WHERE user_id=? AND id=?
               RETURNING *""",
            (uuid4().hex, values["feedback_type"], values["feedback_content"], user_id, memory_id),
        ) as rows:
            row = await rows.fetchone()
            return dict(row) if row else None

    async def set_link(
        self,
        user_id: str,
        source: str,
        target: str,
        relation: str,
        relevance: float,
        directed: bool = False,
    ) -> Snapshot | None:
        """Create or refresh an edge between two active owned memories.

        Undirected edges normalize endpoint order so each pair has a single row.
        / 无向边会归一化端点顺序，使每对记忆只有一行。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            source: Source memory id. / 源记忆 ID。
            target: Target memory id. / 目标记忆 ID。
            relation: Relation label, e.g. shared_conversation. / 关系标签，如 shared_conversation。
            relevance: Relevance score from 0 to 1. / 0 到 1 的相关度。
            directed: Whether to keep the source/target direction. / 是否保留方向。

        Returns:
            The link row, or None when either endpoint is not active and owned.
            / 边行；任一端点非活跃或不属于该用户时为 None。
        """
        if not directed:
            source, target = sorted((source, target))
        async with self.connection.execute(
            """INSERT INTO workspace_memory_links
               (source_id,target_id,relation,relevance,directed,origin,suppressed)
               SELECT a.id, b.id, ?, ?, ?, 'manual', 0 FROM workspace_memories a
               JOIN workspace_memories b ON a.user_id=b.user_id
               WHERE a.user_id=? AND a.id=? AND b.id=? AND a.status='active' AND b.status='active'
               ON CONFLICT(source_id,target_id) DO UPDATE SET
                 relation=excluded.relation, relevance=excluded.relevance,
                 directed=excluded.directed, origin='manual', suppressed=0
               RETURNING *""",
            (relation, relevance, directed, user_id, source, target),
        ) as rows:
            row = await rows.fetchone()
            return dict(row) if row else None

    async def delete_link(
        self, user_id: str, source: str, target: str, directed: bool = False
    ) -> bool:
        """Suppress an edge instead of deleting its history.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            source: Source memory id. / 源记忆 ID。
            target: Target memory id. / 目标记忆 ID。
            directed: Whether the edge is directed. / 边是否有向。

        Returns:
            True when exactly one visible edge was suppressed. / 恰好抑制一条可见边时为 True。
        """
        if not directed:
            source, target = sorted((source, target))
        async with self.connection.execute(
            """UPDATE workspace_memory_links SET suppressed=1
               WHERE source_id=? AND target_id=? AND directed=? AND suppressed=0
                 AND source_id IN (SELECT id FROM workspace_memories WHERE user_id=?)
                 AND target_id IN (SELECT id FROM workspace_memories WHERE user_id=?)""",
            (source, target, directed, user_id, user_id),
        ) as cursor:
            return cursor.rowcount == 1

    async def graph(self, user_id: str, limit: int, focus: str | None = None) -> dict:
        """Return a bounded memory graph for visualization.

        A focus memory and its direct neighbors are ordered first, then active memories by
        importance; truncated/total tell the caller whether more nodes exist.
        / 焦点记忆及其直接邻居排在最前，其余活跃记忆按重要性排序；truncated/total 告知是否还有
        更多节点。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            limit: Maximum nodes returned. / 返回节点上限。
            focus: Optional memory id to prioritize. / 可选优先记忆 ID。

        Returns:
            Mapping with nodes, visible edges, total active count, truncation flag and limit.
            / 含节点、可见边、活跃总数、截断标记与上限的映射。
        """
        order = ""
        arguments = [user_id]
        if focus:
            order = """CASE WHEN id=? THEN 0 WHEN id IN (
                SELECT target_id FROM workspace_memory_links WHERE source_id=? AND suppressed=0
                UNION SELECT source_id FROM workspace_memory_links
                WHERE target_id=? AND suppressed=0
            ) THEN 1 ELSE 2 END,"""
            arguments.extend([focus, focus, focus])
        arguments.extend([limit, user_id])
        async with self.connection.execute(
            f"""WITH nodes AS (
                   SELECT id, title, type, importance FROM workspace_memories
                   WHERE user_id=? AND status='active'
                   ORDER BY {order} importance DESC, id LIMIT ?
               )
               SELECT
                   (SELECT json_group_array(json_object(
                       'id',id,'label',title,'type',type,'importance',importance)) FROM nodes),
                   (SELECT json_group_array(json_object(
                       'source',source_id,'target',target_id,'relation',relation,
                       'relevance',relevance,
                       'directed',json(CASE WHEN directed THEN 'true' ELSE 'false' END),
                       'origin',origin))
                    FROM workspace_memory_links
                    WHERE suppressed=0 AND source_id IN (SELECT id FROM nodes)
                      AND target_id IN (SELECT id FROM nodes)),
                   (SELECT count(*) FROM workspace_memories WHERE user_id=? AND status='active')""",
            arguments,
        ) as rows:
            nodes, edges, total = await rows.fetchone()
            nodes = json.loads(nodes)
            return {
                "nodes": nodes,
                "edges": json.loads(edges),
                "total": total,
                "truncated": total > len(nodes),
                "limit": limit,
            }

    async def retrieve(
        self, *, user_id: str, query: str, thread_id: str, project_id: str | None, limit: int
    ) -> list[Snapshot]:
        """Retrieve scoped user memories matching query terms.

        Global memories, the current thread's memories and the current project's memories
        are eligible; confirmed claim keys are joined and episodic results carry evidence
        dates.
        / 全局记忆、当前线程记忆与当前项目记忆可被检索；会连接已确认断言键，情景记忆结果带有
        证据日期。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            query: Raw user query driving term extraction. / 驱动词项提取的原始用户查询。
            thread_id: Current thread scope. / 当前线程作用域。
            project_id: Current project scope, or None. / 当前项目作用域或 None。
            limit: Maximum rows returned. / 返回行数上限。

        Returns:
            Matching memory snapshots ranked by importance and recency.
            / 按重要性与时间排序的匹配记忆快照。
        """
        terms = memory_terms(query, 16)
        if not terms:
            return []
        episode_terms = memory_terms(EPISODE_QUERY_NOISE.sub(" ", query), 24)
        contains = "instr(lower(m.title || ' ' || m.content), ?) > 0"
        matches = " OR ".join(contains for _ in terms)
        episode_matches = " OR ".join(contains for _ in episode_terms) or "0"
        async with self.connection.execute(
            f"""SELECT m.*, c.profile_key, CASE WHEN m.type='episodic' THEN (
                    SELECT MIN(e.recorded_at) FROM memory_evidence e WHERE e.claim_id=c.id
                ) END AS reported_at
                FROM workspace_memories m
                LEFT JOIN memory_claims c ON c.memory_id=m.id AND c.status='confirmed'
                WHERE m.user_id=? AND m.status='active'
                  AND (m.scope='global' OR (m.scope='thread' AND m.scope_ref=?)
                       OR (m.scope='project' AND m.scope_ref=?))
                  AND ((m.type<>'episodic' AND ({matches}))
                       OR (m.type='episodic' AND ({episode_matches})))
                ORDER BY m.importance DESC, CASE WHEN m.type='episodic'
                    THEN coalesce(reported_at, m.created_at) ELSE m.updated_at END DESC,
                    m.id LIMIT ?""",
            (user_id, thread_id, project_id, *terms, *episode_terms, limit),
        ) as rows:
            return [dict(row) async for row in rows]

    async def get_profile(self, user_id: str, version: int | None = None) -> Snapshot | None:
        """Read a profile version with its projected attributes.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            version: Requested version, or None for the newest. / 指定版本；None 表示最新版本。

        Returns:
            Profile snapshot, or None when the user has no profile.
            / 画像快照；用户没有画像时为 None。
        """
        async with self.connection.execute(
            """SELECT version, profile_json, change_summary, created_at,
                      attributes_json, removed_attributes, redacted_attributes
               FROM workspace_profiles WHERE user_id=? AND (? IS NULL OR version=?)
               ORDER BY version DESC LIMIT 1""",
            (user_id, version, version),
        ) as rows:
            row = await rows.fetchone()
            if not row:
                return None
            result = dict(
                user_id=user_id,
                version=row[0],
                preferences=json.loads(row[1]),
                change_summary=row[2],
                created_at=row[3],
                removed_attributes=row[5],
                redacted_attributes=row[6],
            )
            attributes = json.loads(row[4])
            if attributes:
                result["attributes"] = attributes
            return result

    async def profile_versions(self, user_id: str, limit: int, before: int) -> list[Snapshot]:
        """List profile version summaries before a version cursor.

        Args:
            user_id: Owning user id. / 所属用户 ID。
            limit: Maximum versions returned. / 返回版本数上限。
            before: Exclusive upper version bound. / 版本上界（不包含）。

        Returns:
            Version summaries newest first. / 按版本从新到旧的摘要。
        """
        async with self.connection.execute(
            """SELECT version, change_summary, created_at, removed_attributes, redacted_attributes
               FROM workspace_profiles
               WHERE user_id=? AND version<? ORDER BY version DESC LIMIT ?""",
            (user_id, before, limit),
        ) as rows:
            return [dict(row) async for row in rows]

    async def save_profile(
        self, user_id: str, expected_version: int, preferences: dict, summary: str
    ) -> bool:
        """Append a profile version with optimistic version checking.

        The insert only succeeds while the stored maximum version still equals the expected
        version; removal counts are derived from the previous version. The caller then prunes
        old history.
        / 仅当已存最大版本仍等于期望版本时插入成功；移除数量由上一版本推导。调用方随后会裁剪
        旧历史。

        Args:
            user_id: Owning user id. / 所属用户 ID。
            expected_version: Version the caller last read. / 调用方最后读到的版本。
            preferences: Manual preference mapping for the new version. / 新版本的手动偏好映射。
            summary: Human-readable change summary. / 可读的变更摘要。

        Returns:
            True when the new version was written. / 新版本写入成功时为 True。
        """
        previous = await self.get_profile(user_id, expected_version) if expected_version else None
        removed = len(set((previous or {}).get("preferences", {})) - set(preferences))
        async with self.connection.execute(
            """INSERT INTO workspace_profiles (
                   user_id, version, profile_json, change_summary,
                   attributes_json, removed_attributes
               )
               SELECT ?, ?, ?, ?, coalesce((
                   SELECT attributes_json FROM workspace_profiles WHERE user_id=? AND version=?
               ), '[]'), ? WHERE ?=(
                   SELECT coalesce(max(version),0) FROM workspace_profiles WHERE user_id=?
               )
               ON CONFLICT DO NOTHING""",
            (
                user_id,
                expected_version + 1,
                json.dumps(preferences, ensure_ascii=False),
                summary,
                user_id,
                expected_version,
                removed,
                expected_version,
                user_id,
            ),
        ) as cursor:
            saved = cursor.rowcount == 1
        if saved:
            await self.prune_profile_history(user_id)
        return saved

    async def prune_profile_history(self, user_id: str):
        """Keep only the newest profile versions per user.

        Args:
            user_id: Owning user id. / 所属用户 ID。
        """
        await self.connection.execute(
            """DELETE FROM workspace_profiles WHERE user_id=? AND version NOT IN (
                SELECT version FROM workspace_profiles WHERE user_id=? ORDER BY version DESC LIMIT ?
            )""",
            (user_id, user_id, PROFILE_HISTORY_LIMIT),
        )


class BoundedWorkspaceContext:
    """Build the bounded personalization reference text for one run.
    / 为单轮构建有界的个性化参考文本。"""

    async def build(
        self, *, user_input, user_profile, relevant_memories, project_id, token_budget
    ) -> ContextBuildResult:
        """Build reference text within the shared token budget.

        The budget is measured in UTF-8 bytes as a conservative tokenizer-free estimate.
        Attributes outside the current thread/project scope are excluded, manual
        preferences win over derived memories, and text that does not fit is skipped.
        / 预算按 UTF-8 字节计，作为不依赖分词器的保守估算。当前线程/项目作用域之外的属性会被
        排除，手动偏好优先于派生记忆，放不下的文本会被跳过。

        Args:
            user_input: Current user request, kept for relevance decisions.
                / 当前用户请求，用于相关性判断。
            user_profile: Latest profile snapshot, or None. / 最新画像快照或 None。
            relevant_memories: Bounded retrieval candidates for this run.
                / 本轮有界的检索候选。
            project_id: Current project scope, or None. / 当前项目作用域或 None。
            token_budget: Shared profile-and-memory budget. / 画像与记忆共享的预算。

        Returns:
            Built reference text, used memory ids and the estimated token cost.
            / 构建出的参考文本、使用的记忆 ID 与估算 token 开销。
        """
        # English: UTF-8 bytes provide a conservative tokenizer-free budget.
        # 中文：UTF-8 字节提供保守预算，不依赖模型分词器。
        parts = []
        used_ids = []
        remaining = token_budget
        candidates = []
        by_id = {item["id"]: item for item in relevant_memories}

        def semantic_metadata(item):
            concepts = item.get("concepts", [])[:8]
            if not concepts:
                return ""
            labels = {c["id"]: c["label"] for c in concepts}
            metadata = {
                "concepts": [{"label": c["label"], "kind": c["kind"]} for c in concepts],
                "subject": item.get("subject", "user"),
                "predicate": item.get("predicate"),
                "source": labels.get(item.get("source_concept_id")),
                "target": labels.get(item.get("target_concept_id")),
            }
            return " | Semantic: " + json.dumps(
                metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )

        if user_profile:
            preferences = json.dumps(
                user_profile.get("preferences", {}),
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if user_profile.get("preferences", {}):
                candidates.append((None, "Preferences: " + preferences))
        manual = user_profile.get("preferences", {}) if user_profile else {}
        included = set()
        scope_rank = {"global": 0, "project": 1, "thread": 2}
        attribute_slots = {}
        for attribute in (user_profile or {}).get("attributes", []):
            scope, reference = attribute.get("scope"), attribute.get("scope_ref")
            if not (
                (scope == "global" and not reference)
                or (scope == "project" and project_id and reference == project_id)
                or (scope == "thread" and reference == (user_profile or {}).get("active_thread_id"))
            ):
                continue
            slot = attribute.get("key") or attribute.get("memory_id")
            previous = attribute_slots.get(slot)
            if previous is None or scope_rank.get(attribute.get("scope"), -1) > scope_rank.get(
                previous.get("scope"), -1
            ):
                attribute_slots[slot] = attribute
        for attribute in sorted(
            attribute_slots.values(),
            key=lambda item: (
                item.get("dimension") != "identity",
                item.get("scope", ""),
                item.get("key", ""),
                item.get("memory_id", ""),
            ),
        ):
            scope, reference = attribute.get("scope"), attribute.get("scope_ref")
            if not (
                (scope == "global" and not reference)
                or (scope == "project" and project_id and reference == project_id)
                or (
                    scope == "thread"
                    and (user_profile or {}).get("active_thread_id")
                    and reference == user_profile["active_thread_id"]
                )
            ):
                continue
            if attribute.get("key") in manual:
                continue
            identifier, value = attribute.get("memory_id"), attribute.get("value")
            if not isinstance(identifier, str):
                continue
            if not isinstance(value, str):
                value = json.dumps(value, ensure_ascii=False, sort_keys=True)
            if attribute.get("dimension") == "skill" and identifier not in {
                m["id"] for m in relevant_memories
            }:
                continue
            candidates.append(
                (
                    identifier,
                    f"User {attribute['dimension']} ({attribute['key']}): {value}"
                    + semantic_metadata(by_id.get(identifier, {})),
                )
            )
            included.add(identifier)
        for item in sorted(
            relevant_memories,
            key=lambda item: (-item.get("retrieval_score", item.get("importance", 0)), item["id"]),
        ):
            if item.get("profile_key") in manual or item["id"] in included:
                continue
            if item.get("type") == "episodic":
                date = item.get("reported_at")
                label = (
                    f"Episode reported on {date[:10]} (UTC)" if isinstance(date, str) else "Episode"
                )
                text = f"{label}: {item['content']}"
            else:
                text = f"{item['title']}: {item['content']}"
            candidates.append((item["id"], text + semantic_metadata(item)))
        for identifier, text in candidates:
            cost = len(text.encode("utf-8")) + 1
            if cost > remaining:
                continue
            parts.append(text)
            remaining -= cost
            if identifier is not None:
                used_ids.append(identifier)
        return ContextBuildResult(
            text="\n".join(parts),
            memory_ids=tuple(used_ids),
            token_estimate=token_budget - remaining,
        )
