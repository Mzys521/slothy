"""Transparent retrieval caching and revocable per-thread global profile snapshots.
/ 透明的检索缓存与可撤销的每线程全局画像快照。"""

import asyncio
import json
from collections import OrderedDict
from copy import deepcopy
from time import time

from app.agent.prompting.plan import canonical, digest
from app.config import PromptSettings
from app.infrastructure.workspace import EPISODE_QUERY_NOISE, memory_terms
from app.memory.semantics import identity_intent


class CachedMemoryReader:
    """Cache memory retrieval behind a revision-aware key.

    Args:
        store: Workspace store owning the SQLite connection and retrieval query.
            / 持有 SQLite 连接与检索查询的工作区存储。
        settings: Prompt settings supplying TTL and cache entry limits.
            / 提供 TTL 与缓存条目上限的提示词配置。
        clock: Time source used for expiry; injectable for tests.
            / 用于过期的时钟；可注入以便测试。
    """

    def __init__(self, store, settings: PromptSettings, clock=time):
        """Initialize counters and the in-memory LRU.

        Args:
            store: Workspace store used for real retrieval. / 用于真实检索的工作区存储。
            settings: Cache TTL and size limits. / 缓存 TTL 与容量限制。
            clock: Callable returning the current epoch seconds. / 返回当前纪元秒的可调用对象。
        """
        self.store, self.settings, self.clock = store, settings, clock
        self.entries = OrderedDict()
        self.hits = self.misses = self.disk_hits = self.failures = 0

    async def retrieve(self, *, user_id, query, thread_id, project_id, limit):
        """Retrieve memories through the cache with a safe fallback.

        The cache key covers user, thread, project, limit, query terms and both revision
        counters; any learning or profile write changes the key. Failures fall back to the
        uncached query instead of failing the run.
        / 缓存键涵盖用户、线程、项目、上限、查询词以及两个版本计数；任何学习或画像写入都会改变
        键。失败时回退到无缓存查询，而不是让本轮失败。

        Args:
            user_id: Authenticated user scope. / 已认证用户范围。
            query: Raw user query driving term extraction. / 驱动词项提取的原始用户查询。
            thread_id: Thread allowed for thread-scoped memories. / 允许读取线程作用域记忆的线程。
            project_id: Project scope, or None. / 项目作用域或 None。
            limit: Maximum returned memories. / 返回记忆条数上限。

        Returns:
            Retrieved memory rows, deep-copied from the cache when hit.
            / 检索到的记忆行；命中缓存时返回深拷贝。
        """
        options = dict(
            user_id=user_id, query=query, thread_id=thread_id, project_id=project_id, limit=limit
        )
        if not self.settings.retrieval_cache_ttl_seconds:
            return await self.store.retrieve(**options)
        try:
            async with self.store.connection.execute(
                "SELECT coalesce((SELECT memory_index_version FROM memory_meta "
                "WHERE user_id=?),0), coalesce((SELECT max(version) "
                "FROM workspace_profiles WHERE user_id=?),0)",
                (user_id, user_id),
            ) as cursor:
                revisions = tuple(await cursor.fetchone())
            key = digest(
                {
                    "user": user_id,
                    "thread": thread_id,
                    "project": project_id,
                    "limit": limit,
                    "terms": sorted(memory_terms(query, 16)),
                    "intent": identity_intent(query),
                    "scoring": 3,
                    "c": await self.store.scoring_c(user_id)
                    if hasattr(self.store, "scoring_c")
                    else 4,
                    "episode_terms": sorted(memory_terms(EPISODE_QUERY_NOISE.sub(" ", query), 24)),
                    "memory_index_version": revisions[0],
                    "profile_version": revisions[1],
                }
            )
            now = self.clock()
            # English: Keep obsolete private payloads out of the in-memory LRU as well.
            # 中文：同时把过期的私有载荷移出内存 LRU。
            for old_key, entry in list(self.entries.items()):
                if entry[0] <= now or (entry[1] == user_id and entry[2] != revisions):
                    del self.entries[old_key]
            if key in self.entries:
                entry = self.entries.pop(key)
                self.entries[key] = entry
                self.hits += 1
                return deepcopy(entry[3])
            async with self.store.connection.execute(
                "SELECT payload, expires_at FROM retrieval_cache "
                "WHERE cache_key=? AND user_id=? AND expires_at>?",
                (key, user_id, now),
            ) as cursor:
                cached = await cursor.fetchone()
            if cached:
                result, expiry = json.loads(cached[0]), cached[1]
                self.hits += 1
                self.disk_hits += 1
            else:
                self.misses += 1
                result = await self.store.retrieve(**options)
                # English: A concurrent learning write must not label old data with a new revision.
                # 中文：并发学习写入不得用新版本标记旧数据。
                expiry = now + self.settings.retrieval_cache_ttl_seconds
                await self.store.connection.execute(
                    "INSERT OR REPLACE INTO retrieval_cache VALUES(?,?,?,?,?)",
                    (key, user_id, canonical(result), now, expiry),
                )
                await self.store.connection.execute(
                    "DELETE FROM retrieval_cache WHERE expires_at<=? OR cache_key IN ("
                    "SELECT cache_key FROM retrieval_cache ORDER BY created_at DESC,cache_key "
                    "LIMIT -1 OFFSET ?)",
                    (now, self.settings.retrieval_cache_max_entries),
                )
            self.entries[key] = (expiry, user_id, revisions, deepcopy(result))
            while len(self.entries) > self.settings.retrieval_cache_max_entries:
                self.entries.popitem(last=False)
            return deepcopy(result)
        except Exception:
            self.failures += 1
            return await self.store.retrieve(**options)

    def stats(self):
        """Return hit, miss, disk-hit, failure and entry counters.

        Returns:
            Snapshot of the cache counters for diagnostics. / 用于诊断的缓存计数快照。
        """
        return {
            "hits": self.hits,
            "misses": self.misses,
            "disk_hits": self.disk_hits,
            "failures": self.failures,
            "entries": len(self.entries),
        }


def global_profile(current: dict | None, user: str) -> dict:
    """Project only global attributes from a full profile snapshot.

    Args:
        current: Latest profile snapshot, or None. / 最新画像快照或 None。
        user: Owning user id. / 所属用户 ID。

    Returns:
        A profile containing global attributes sorted by key and memory id.
        / 仅含全局属性、按 key 与 memory id 排序的画像。
    """
    current = current or {}
    attributes = [
        {
            key: item.get(key)
            for key in ("memory_id", "key", "dimension", "value", "scope", "scope_ref")
        }
        for item in current.get("attributes", [])
        if item.get("scope") == "global"
        and not item.get("scope_ref")
        and item.get("dimension") != "skill"
    ]
    return {
        "user_id": user,
        "version": current.get("version", 0),
        "attributes": sorted(
            attributes, key=lambda item: (item.get("key") or "", item.get("memory_id") or "")
        ),
    }


class ProfileSnapshots:
    """Keep revocable per-thread copies of the global profile.

    Args:
        store: Workspace store owning the snapshot tables. / 持有快照表的工作区存储。
    """

    def __init__(self, store):
        """Bind the store and create the serialization lock.

        Args:
            store: Workspace store used for snapshot queries. / 用于快照查询的工作区存储。
        """
        self.store = store
        self.lock = asyncio.Lock()

    async def capture(self, user: str, thread: str):
        """Create the thread snapshot when absent.

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread receiving the snapshot. / 接收快照的线程。
        """
        current = await self.store.get_profile(user)
        await self.read(user, thread, current)

    async def read(self, user: str, thread: str, current: dict | None) -> dict:
        """Read and rebase a thread snapshot against current state.

        Inactive memories and removed preferences are pruned with an optimistic revision
        bump; four conflicting attempts raise instead of returning stale data.
        / 非活跃记忆与已移除偏好会被裁剪并乐观递增版本；四次冲突后抛出异常而不是返回过期数据。

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread owning the snapshot. / 快照所属线程。
            current: Latest full profile, or None. / 最新完整画像或 None。

        Returns:
            Mapping with the pruned profile, its version and the snapshot revision.
            / 含裁剪后画像、版本与快照修订号的映射。
        Raises:
            RuntimeError: When the snapshot keeps changing concurrently.
                / 快照持续并发变化时抛出。
        """
        async with self.lock:
            for _ in range(4):
                encoded = canonical(global_profile(current, user))
                await self.store.connection.execute(
                    "INSERT OR IGNORE INTO prompt_profile_snapshots VALUES(?,?,?,1)",
                    (user, thread, encoded),
                )
                async with self.store.connection.execute(
                    "SELECT snapshot_json,revision FROM prompt_profile_snapshots "
                    "WHERE user_id=? AND thread_id=?",
                    (user, thread),
                ) as cursor:
                    raw, revision = await cursor.fetchone()
                snapshot = json.loads(raw)
                identifiers = [item["memory_id"] for item in snapshot.get("attributes", [])]
                async with self.store.connection.execute(
                    "SELECT id FROM workspace_memories "
                    "WHERE user_id=? AND status='active' AND scope='global' "
                    "AND id IN (SELECT value FROM json_each(?))",
                    (user, canonical(identifiers)),
                ) as cursor:
                    active = {row[0] async for row in cursor}
                snapshot["attributes"] = [
                    item for item in snapshot.get("attributes", []) if item["memory_id"] in active
                ]
                live = {a["memory_id"]: a for a in (current or {}).get("attributes", [])}
                snapshot["attributes"] = [
                    a
                    for a in snapshot["attributes"]
                    if a["memory_id"] in live
                    and all(
                        a.get(k) == live[a["memory_id"]].get(k)
                        for k in ("key", "value", "dimension", "scope", "scope_ref")
                    )
                ]
                snapshot.pop("preferences", None)
                clean = canonical(snapshot)
                if clean != raw:
                    async with self.store.connection.execute(
                        "UPDATE prompt_profile_snapshots SET snapshot_json=?,revision=revision+1 "
                        "WHERE user_id=? AND thread_id=? AND snapshot_json=? AND revision=?",
                        (clean, user, thread, raw, revision),
                    ) as cursor:
                        if cursor.rowcount != 1:
                            continue
                    revision += 1
                return {"profile": snapshot, "version": snapshot["version"], "revision": revision}
        raise RuntimeError("Profile snapshot changed concurrently")

    async def reset(self, user: str, thread: str):
        """Delete the thread's stored snapshot.

        Args:
            user: Owning user id. / 所属用户 ID。
            thread: Thread whose snapshot is removed. / 被删除快照的线程。
        """
        await self.store.connection.execute(
            "DELETE FROM prompt_profile_snapshots WHERE user_id=? AND thread_id=?", (user, thread)
        )

    async def copy(self, user: str, source: str, target: str):
        """Copy a snapshot between threads for a fork.

        Args:
            user: Owning user id. / 所属用户 ID。
            source: Thread to copy from. / 复制来源线程。
            target: Thread to copy to. / 复制目标线程。
        """
        await self.store.connection.execute(
            "INSERT OR REPLACE INTO prompt_profile_snapshots "
            "SELECT user_id,?,snapshot_json,revision FROM prompt_profile_snapshots "
            "WHERE user_id=? AND thread_id=?",
            (target, user, source),
        )

    async def redact_memory(self, user: str, identifier: str):
        """Erase one memory id from every stored snapshot eagerly.

        Args:
            user: Owning user id. / 所属用户 ID。
            identifier: Memory id to remove. / 要移除的记忆 ID。
        """
        # English: Eager erasure from stored snapshots, not only cache-key invalidation.
        # 中文：对已存快照做即时擦除，而不只是让缓存键失效。
        async with self.store.connection.execute(
            "SELECT thread_id,snapshot_json FROM prompt_profile_snapshots WHERE user_id=?", (user,)
        ) as cursor:
            rows = await cursor.fetchall()
        for thread, raw in rows:
            profile = json.loads(raw)
            cleaned = [
                item for item in profile.get("attributes", []) if item["memory_id"] != identifier
            ]
            if cleaned != profile.get("attributes", []):
                profile["attributes"] = cleaned
                await self.store.connection.execute(
                    "UPDATE prompt_profile_snapshots SET snapshot_json=?,revision=revision+1 "
                    "WHERE user_id=? AND thread_id=? AND snapshot_json=?",
                    (canonical(profile), user, thread, raw),
                )
