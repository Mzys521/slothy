"""SQLite-backed user profile and explicitly managed long-term memories."""

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
    # Match words and adjacent Han characters without requiring spaces in Chinese queries.
    words = re.findall(r"[\u3400-\u9fff]+|[^\W_\u3400-\u9fff]+", query.lower())
    terms = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            terms.extend(word[index : index + 2] for index in range(len(word) - 1))
        elif len(word) >= 2:
            terms.append(word)
    return list(dict.fromkeys(terms))[:limit]


class WorkspaceStore:
    def __init__(self, connection: aiosqlite.Connection):
        self.connection = connection

    async def learning_usage(self, user: str):
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
            # Retrieval also reads claim keys and evidence dates. All writers share these triggers.
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
        async with self.connection.execute(
            """SELECT * FROM workspace_memories
               WHERE user_id=? AND status=? AND (? IS NULL OR type=?)
                 AND (? IS NULL OR scope=?) AND (? IS NULL OR id>?)
               ORDER BY id LIMIT ?""",
            (user_id, status, memory_type, memory_type, scope, scope, cursor, cursor, limit),
        ) as rows:
            return [dict(row) async for row in rows]

    async def get_memory(self, user_id: str, memory_id: str) -> Snapshot | None:
        async with self.connection.execute(
            "SELECT * FROM workspace_memories WHERE user_id=? AND id=?",
            (user_id, memory_id),
        ) as rows:
            row = await rows.fetchone()
            return dict(row) if row else None

    async def create_memory(self, user_id: str, values: dict) -> Snapshot:
        async with self.connection.execute(
            """INSERT INTO workspace_memories
               (id,user_id,title,content,type,scope,scope_ref,source,importance)
               VALUES (:id,:user_id,:title,:content,:type,:scope,:scope_ref,:source,:importance)
               RETURNING *""",
            {**values, "id": uuid4().hex, "user_id": user_id},
        ) as rows:
            return dict(await rows.fetchone())

    async def update_memory(self, user_id: str, memory_id: str, values: dict) -> Snapshot | None:
        if not values:
            return await self.get_memory(user_id, memory_id)
        columns = {"title", "content", "source", "importance", "status"}
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
        async with self.connection.execute(
            "DELETE FROM workspace_memories WHERE user_id=? AND id=?", (user_id, memory_id)
        ) as cursor:
            return cursor.rowcount == 1

    async def add_feedback(self, user_id: str, memory_id: str, values: dict) -> Snapshot | None:
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
        await self.connection.execute(
            """DELETE FROM workspace_profiles WHERE user_id=? AND version NOT IN (
                SELECT version FROM workspace_profiles WHERE user_id=? ORDER BY version DESC LIMIT ?
            )""",
            (user_id, user_id, PROFILE_HISTORY_LIMIT),
        )


class BoundedWorkspaceContext:
    async def build(
        self, *, user_input, user_profile, relevant_memories, project_id, token_budget
    ) -> ContextBuildResult:
        # UTF-8 bytes provide a conservative budget without depending on a model tokenizer.
        parts = []
        used_ids = []
        remaining = token_budget
        candidates = []
        if user_profile:
            preferences = json.dumps(
                user_profile["preferences"],
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
            if user_profile["preferences"]:
                candidates.append((None, "Preferences: " + preferences))
        manual = user_profile["preferences"] if user_profile else {}
        included = set()
        for attribute in sorted(
            (user_profile or {}).get("attributes", []),
            key=lambda item: (
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
            if not isinstance(identifier, str) or not isinstance(value, str):
                continue
            candidates.append(
                (identifier, f"User {attribute['dimension']} ({attribute['key']}): {value}")
            )
            included.add(identifier)
        for item in sorted(
            relevant_memories,
            key=lambda item: (item.get("type", ""), -item.get("importance", 0), item["id"]),
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
            candidates.append((item["id"], text))
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
