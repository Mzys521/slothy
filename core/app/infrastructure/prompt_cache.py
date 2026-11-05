"""Transparent retrieval caching and revocable per-thread global profile snapshots."""

import asyncio
import json
from collections import OrderedDict
from copy import deepcopy
from time import time

from app.agent.prompting.plan import canonical, digest
from app.config import PromptSettings
from app.infrastructure.workspace import EPISODE_QUERY_NOISE, memory_terms


class CachedMemoryReader:
    def __init__(self, store, settings: PromptSettings, clock=time):
        self.store, self.settings, self.clock = store, settings, clock
        self.entries = OrderedDict()
        self.hits = self.misses = self.disk_hits = self.failures = 0

    async def retrieve(self, *, user_id, query, thread_id, project_id, limit):
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
                    "episode_terms": sorted(memory_terms(EPISODE_QUERY_NOISE.sub(" ", query), 24)),
                    "memory_index_version": revisions[0],
                    "profile_version": revisions[1],
                }
            )
            now = self.clock()
            # Keep obsolete private payloads out of the in-memory LRU as well.
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
                # A concurrent learning write must not label old data with a new revision.
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
        return {
            "hits": self.hits,
            "misses": self.misses,
            "disk_hits": self.disk_hits,
            "failures": self.failures,
            "entries": len(self.entries),
        }


def global_profile(current: dict | None, user: str) -> dict:
    current = current or {}
    attributes = [
        {
            key: item.get(key)
            for key in ("memory_id", "key", "dimension", "value", "scope", "scope_ref")
        }
        for item in current.get("attributes", [])
        if item.get("scope") == "global" and not item.get("scope_ref")
    ]
    return {
        "user_id": user,
        "version": current.get("version", 0),
        "preferences": deepcopy(current.get("preferences", {})),
        "attributes": sorted(
            attributes, key=lambda item: (item.get("key") or "", item.get("memory_id") or "")
        ),
    }


class ProfileSnapshots:
    def __init__(self, store):
        self.store = store
        self.lock = asyncio.Lock()

    async def capture(self, user: str, thread: str):
        current = await self.store.get_profile(user)
        await self.read(user, thread, current)

    async def read(self, user: str, thread: str, current: dict | None) -> dict:
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
                current_keys = (current or {}).get("preferences", {})
                snapshot["preferences"] = {
                    key: value
                    for key, value in snapshot["preferences"].items()
                    if key in current_keys
                }
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
        await self.store.connection.execute(
            "DELETE FROM prompt_profile_snapshots WHERE user_id=? AND thread_id=?", (user, thread)
        )

    async def copy(self, user: str, source: str, target: str):
        await self.store.connection.execute(
            "INSERT OR REPLACE INTO prompt_profile_snapshots "
            "SELECT user_id,?,snapshot_json,revision FROM prompt_profile_snapshots "
            "WHERE user_id=? AND thread_id=?",
            (target, user, source),
        )

    async def redact_memory(self, user: str, identifier: str):
        # Eager erasure from stored snapshots, not only an invisible cache-key invalidation.
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
