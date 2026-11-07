"""Evidence-backed V3 write service; facts are the sole profile/semantic-edge authority.
/ 有证据的 V3 写入服务：事实是画像和语义边的唯一权威。"""

import json
import re
from uuid import uuid4

from app.memory.semantics import (
    LEVELS,
    PROFILE_DIMENSIONS,
    canonical_key,
    frequency_strength,
    identifier,
    normalized,
)
from app.personalization.evolution import SENSITIVE, MemoryEvolution, profile_key


class MemoryService(MemoryEvolution):
    """Reuse the durable learner/review/evidence lifecycle, replace fact writes/projections.
    / 复用队列、审核与证据生命周期，统一事实写入与投影。"""

    async def audit(self, user, event, memory=None, reason=None):
        await self.db.execute(
            "INSERT INTO memory_audit(user_id,event,memory_id,reason) VALUES(?,?,?,?)",
            (user, event, memory, reason),
        )

    async def _concepts(self, user, semantics, scope, scope_ref):
        resolved = {}
        for concept in semantics.get("concepts", []):
            kind = concept["kind"]
            key = normalized(concept["key"])
            if kind == "level":
                key = LEVELS.get(key, key)
            aliases = list(dict.fromkeys([concept["label"], key, *concept.get("aliases", [])]))[:24]
            if kind == "level":
                aliases = list(
                    dict.fromkeys([*aliases, *[a for a, v in LEVELS.items() if v == key]])
                )[:24]
            records = await self.rows(
                "SELECT * FROM memory_concepts WHERE user_id=? AND kind=? "
                "AND scope=? AND scope_ref=?",
                (user, kind, scope, scope_ref or ""),
            )
            same = next(
                (c for c in records if c["concept_key"] == key),
                None,
            )
            cid = same["id"] if same else identifier(user, scope, scope_ref, kind, key)
            if same:
                aliases = list(dict.fromkeys([*json.loads(same["aliases_json"]), *aliases]))[:24]
                await self.db.execute(
                    "UPDATE memory_concepts SET aliases_json=? WHERE id=?",
                    (json.dumps(aliases, ensure_ascii=False), cid),
                )
            else:
                await self.db.execute(
                    "INSERT INTO memory_concepts VALUES(?,?,?,?,?,?,?,?)",
                    (
                        cid,
                        user,
                        key,
                        kind,
                        concept["label"],
                        json.dumps(aliases, ensure_ascii=False),
                        scope,
                        scope_ref or "",
                    ),
                )
            resolved[concept["key"]] = cid
        return resolved

    async def reinforce(self, user, memory_id, session, event):
        """Insert the event first; only the successful unique insert changes frequency.
        / 先插入唯一事件，插入成功才改变频次；概念事件另行去重。"""
        memory = await self.store.get_memory(user, memory_id)
        if not memory or memory["status"] not in {"active", "archived"}:
            return False
        cursor = await self.db.execute(
            "INSERT OR IGNORE INTO memory_reinforcements VALUES(?,?,?)", (memory_id, session, event)
        )
        inserted = cursor.rowcount == 1
        if inserted:
            await self.db.execute(
                "UPDATE workspace_memories SET effective_frequency="
                "effective_frequency+1 WHERE user_id=? AND id=?",
                (user, memory_id),
            )
        for cid in memory["concept_ids"]:
            await self.db.execute(
                "INSERT OR IGNORE INTO concept_reinforcements VALUES(?,?,?,?)",
                (cid, memory["scope"], memory["scope_ref"] or "", session),
            )
        await self.audit(
            user, "reinforced" if inserted else "reinforcement_deduplicated", memory_id
        )
        return inserted

    async def ensure_capacity(self, user, incoming=1):
        configuration = (await self.planner.configuration(user))[1] if self.planner else None
        limit = configuration.active_limit if configuration else 500
        c = configuration.strength_c if configuration else 4.0
        active = await self.rows(
            "SELECT id,importance,effective_frequency,protected FROM "
            "workspace_memories WHERE user_id=? AND status='active'",
            (user,),
        )
        required = len(active) + incoming - limit
        if required <= 0:
            return True
        candidates = sorted(
            (m for m in active if not m["protected"]),
            key=lambda m: (
                0.6 * m["importance"] + 0.4 * frequency_strength(m["effective_frequency"], c),
                m["id"],
            ),
        )
        if len(candidates) < required:
            await self.audit(user, "capacity_blocked", reason="all_remaining_facts_protected")
            return False
        for memory in candidates[:required]:
            await self.store.update_memory(user, memory["id"], {"status": "archived"})
            await self.audit(user, "archived", memory["id"], "capacity")
        return True

    async def confirm(self, claim, value, *, rebuild=True, override=False):
        user = claim["user_id"]
        current = await self.rows(
            "SELECT * FROM memory_claims WHERE id=? AND user_id=?", (claim["id"], user)
        )
        if not current:
            return None
        claim = {**current[0], **claim}
        semantics = json.loads(claim.get("semantic_json") or "{}")
        if semantics.get("action") == "invalidate":
            target = await self.store.get_memory(user, semantics.get("target_memory_id"))
            if not target or target["status"] != "active":
                await self.db.execute(
                    "UPDATE memory_claims SET status='ignored',"
                    "pending_reason='stale_target' WHERE id=?",
                    (claim["id"],),
                )
                return None
            if (target["scope"], target["scope_ref"], target["memory_key"]) != (
                claim["scope"],
                claim["scope_ref"],
                canonical_key(claim["profile_key"]),
            ):
                raise ValueError("Invalidation target is outside the fact slot")
            await self.store.update_memory(user, target["id"], {"status": "invalidated"})
            await self.db.execute(
                "UPDATE memory_claims SET status='invalidated' WHERE user_id=? AND memory_id=?",
                (user, target["id"]),
            )
            await self.db.execute(
                "UPDATE memory_claims SET status='confirmed',memory_id=? WHERE id=?",
                (target["id"], claim["id"]),
            )
            await self.audit(user, "invalidated", target["id"], "explicit_denial")
            if rebuild:
                await self.rebuild(user)
            return target["id"]
        structured = semantics.get("value", value)
        if override:
            structured = value
            if claim["dimension"] == "skill" and semantics.get("predicate") == "proficiency":
                structured = LEVELS.get(normalized(value), normalized(value))
                source_key = "level:" + str(structured)
                semantics["source_concept"] = source_key
                semantics["concepts"] = [
                    c for c in semantics.get("concepts", []) if c["kind"] != "level"
                ] + [
                    {
                        "key": source_key,
                        "kind": "level",
                        "label": value,
                        "aliases": [str(structured)],
                    }
                ]
        key = canonical_key(claim["profile_key"])
        dimension = "identity" if key.startswith("identity.") else claim["dimension"]
        scope, reference = claim["scope"], claim["scope_ref"]
        if dimension == "identity":
            scope, reference = "global", None
        encoded = json.dumps(structured, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        slots = await self.rows(
            """SELECT * FROM workspace_memories WHERE user_id=?
            AND scope=? AND scope_ref IS ? AND memory_key=? AND status='active'""",
            (user, scope, reference, key),
        )
        same = next(
            (m for m in slots if m["value_json"] == encoded and m["dimension"] == dimension), None
        )
        if not slots and claim.get("memory_id"):
            archived = await self.store.get_memory(user, claim["memory_id"])
            if archived and archived["status"] == "archived" and archived["value"] == structured:
                if not await self.ensure_capacity(user):
                    return None
                await self.store.update_memory(user, archived["id"], {"status": "active"})
                same = {"id": archived["id"]}
        if same:
            memory_id = same["id"]
            await self._concepts(user, semantics, scope, reference)
        else:
            # A correction retires the old fact before creating a different immutable version.
            # 更正先使旧事实退出 active，再建立不可混淆的新版本。
            for previous in slots:
                await self.store.update_memory(user, previous["id"], {"status": "superseded"})
                await self.db.execute(
                    "UPDATE memory_claims SET status='superseded' WHERE user_id=? AND memory_id=?",
                    (user, previous["id"]),
                )
                await self.audit(user, "conflict", previous["id"], "explicit_replacement")
            if not await self.ensure_capacity(user):
                await self.db.execute(
                    "UPDATE memory_claims SET status='pending',"
                    "pending_reason='capacity' WHERE id=?",
                    (claim["id"],),
                )
                return None
            resolved = await self._concepts(user, semantics, scope, reference)
            memory_id = uuid4().hex
            source = resolved.get(semantics.get("source_concept"))
            target = resolved.get(semantics.get("target_concept"))
            protected = bool(
                semantics.get("protected")
                or dimension == "identity"
                or (dimension == "preference" and semantics.get("basis") != "inferred")
            )
            await self.db.execute(
                """INSERT INTO workspace_memories
                (id,user_id,title,content,type,scope,scope_ref,source,importance,memory_key,
                 dimension,value_json,predicate,source_concept_id,target_concept_id,protected)
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    memory_id,
                    user,
                    claim["title"],
                    value,
                    {"habit": "preference", "goal": "semantic", "fact": "semantic"}.get(
                        dimension, dimension
                    ),
                    scope,
                    reference,
                    "manual" if claim["id"].startswith("manual:") else "conversation",
                    claim.get("importance") if claim.get("importance") is not None else 0.7,
                    key,
                    dimension,
                    encoded,
                    semantics.get("predicate", dimension),
                    source,
                    target,
                    protected,
                ),
            )
            for cid in set(resolved.values()):
                await self.db.execute(
                    "INSERT OR IGNORE INTO memory_fact_concepts VALUES(?,?)", (memory_id, cid)
                )
            for previous in slots:
                await self.db.execute(
                    "UPDATE workspace_memories SET superseded_by=? WHERE id=?",
                    (memory_id, previous["id"]),
                )
            await self.audit(user, "created", memory_id)
        await self.db.execute(
            """UPDATE memory_claims SET status='confirmed',memory_id=?,value=?,
            profile_key=?,dimension=?,scope=?,scope_ref=?,pending_reason=NULL,
            updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE id=?""",
            (memory_id, value, key, dimension, scope, reference, claim["id"]),
        )
        evidence = await self.rows(
            "SELECT e.* FROM memory_evidence e WHERE e.claim_id=? "
            "AND e.qualified=1 AND NOT EXISTS (SELECT 1 FROM memory_learning_exclusions x "
            "WHERE x.user_id=? AND x.thread_id=e.thread_id AND x.run_id=e.run_id)",
            (claim["id"], user),
        )
        for row in evidence:
            await self.reinforce(user, memory_id, row["thread_id"], row["run_id"])
        if claim["id"].startswith("manual:"):
            await self.reinforce(user, memory_id, "@manual", "manual")
        await self.store.reindex(user, memory_id)
        if rebuild:
            await self.rebuild(user)
        return memory_id

    async def record(
        self, user, thread, run, quote, claim, recorded_at, *, semantic_value=None, eligible=False
    ):
        """Group a canonical value within its fact-version epoch, never by sentence frequency.
        / 按规范值和事实版本分组；证据可靠性与是否实际记次分开保存。"""
        excluded = await self.rows(
            "SELECT 1 FROM memory_learning_exclusions WHERE user_id=? AND thread_id=? AND run_id=?",
            (user, thread, run),
        )
        if excluded:
            return None, False
        value = claim.value if semantic_value is None else semantic_value
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        anchors = await self.rows(
            "SELECT id,value_json,dimension,status FROM workspace_memories "
            "WHERE user_id=? AND scope=? "
            "AND scope_ref IS ? AND memory_key=? "
            "ORDER BY (status='active') DESC,rowid DESC LIMIT 1",
            (user, claim.scope, claim.scope_ref, claim.key),
        )
        anchor = anchors[0]["id"] if anchors else None
        cid = identifier(
            user, claim.scope, claim.scope_ref, claim.key, claim.dimension, value, anchor
        )
        if (
            anchors
            and anchors[0]["status"] == "active"
            and (anchors[0]["value_json"] == encoded and anchors[0]["dimension"] == claim.dimension)
        ):
            confirmed = await self.rows(
                "SELECT id FROM memory_claims WHERE user_id=? AND memory_id=? "
                "AND status='confirmed' AND id NOT LIKE 'manual:%' ORDER BY rowid LIMIT 1",
                (user, anchor),
            )
            if confirmed:
                cid = confirmed[0]["id"]
        old_sources = await self.rows(
            "SELECT 1 FROM memory_evidence e JOIN memory_claims c ON "
            "c.id=e.claim_id JOIN workspace_memories m ON m.id=c.memory_id WHERE c.user_id=? "
            "AND m.memory_key=? AND m.scope=? AND m.scope_ref IS ? AND m.value_json=? "
            "AND e.thread_id=? AND e.run_id=? LIMIT 1",
            (user, claim.key, claim.scope, claim.scope_ref, encoded, thread, run),
        )
        previous = await self.rows(
            "SELECT * FROM memory_claims WHERE id=? AND user_id=?", (cid, user)
        )
        if previous and previous[0].get("pending_reason") == "user_ignored":
            return None, False
        await self.db.execute(
            "INSERT INTO memory_claims "
            "(id,user_id,scope,scope_ref,profile_key,dimension,value,title,status,semantic_json) "
            "VALUES(?,?,?,?,?,?,?,?,'observed',?) ON CONFLICT DO NOTHING",
            (
                cid,
                user,
                claim.scope,
                claim.scope_ref,
                claim.key,
                claim.dimension,
                claim.value,
                claim.title,
                json.dumps(
                    {
                        "value": value,
                        "basis": "explicit" if claim.source != "observed" else "inferred",
                    },
                    ensure_ascii=False,
                ),
            ),
        )
        if previous and previous[0]["status"] in {"ignored", "superseded", "invalidated"}:
            await self.db.execute(
                "UPDATE memory_claims SET status='observed',value=?,title=? WHERE id=?",
                (claim.value, claim.title, cid),
            )
        await self.db.execute(
            "INSERT INTO memory_evidence "
            "(claim_id,thread_id,run_id,quote,source,recorded_at,qualified) VALUES(?,?,?,?,?,?,?) "
            "ON CONFLICT(claim_id,thread_id,run_id) DO UPDATE SET "
            "qualified=max(memory_evidence.qualified,excluded.qualified)",
            (
                cid,
                thread,
                run,
                quote,
                claim.source,
                recorded_at,
                bool(eligible or claim.source == "remember"),
            ),
        )
        row = (await self.rows("SELECT * FROM memory_claims WHERE id=?", (cid,)))[0]
        if row["status"] == "confirmed":
            if self.planner is None and claim.source != "observed":
                await self.reinforce(user, row["memory_id"], thread, run)
            return cid, not old_sources
        count = (
            await self.rows(
                "SELECT count(DISTINCT thread_id) AS n FROM memory_evidence "
                "WHERE claim_id=? AND qualified=1",
                (cid,),
            )
        )[0]["n"]
        if claim.source == "remember" and not await self.conflicts(row):
            await self.confirm(row, claim.value, rebuild=False)
        elif claim.source != "observed" or count >= 2:
            await self.db.execute("UPDATE memory_claims SET status='pending' WHERE id=?", (cid,))
        return cid, not old_sources

    async def conflicts(self, claim):
        semantic = json.loads(claim.get("semantic_json") or "{}")
        value = semantic.get("value", claim["value"])
        encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        return await self.rows(
            "SELECT DISTINCT m.id AS memory_id,m.memory_key AS profile_key,"
            "m.dimension,m.content AS value FROM workspace_memories m WHERE m.user_id=? "
            "AND m.scope=? AND m.scope_ref IS ? AND m.memory_key=? AND m.status='active' "
            "AND (m.value_json<>? OR m.dimension<>?)",
            (
                claim["user_id"],
                claim["scope"],
                claim["scope_ref"],
                claim["profile_key"],
                encoded,
                claim["dimension"],
            ),
        )

    async def evidence(self, claim_id):
        return await self.rows(
            "SELECT thread_id,run_id,quote,source,recorded_at,qualified "
            "FROM memory_evidence WHERE claim_id=? ORDER BY rowid",
            (claim_id,),
        )

    async def rebuild(self, user, summary="Updated from confirmed facts"):
        rows = await self.rows(
            """SELECT m.*,c.label AS target_label FROM workspace_memories m
            LEFT JOIN memory_concepts c ON c.id=m.target_concept_id AND c.user_id=m.user_id
            WHERE m.user_id=? AND m.status='active' ORDER BY
            m.scope,m.scope_ref,m.memory_key,m.id""",
            (user,),
        )
        attributes = [
            {
                "key": m["memory_key"],
                "dimension": m["dimension"],
                "value": json.loads(m["value_json"]),
                "scope": m["scope"],
                "scope_ref": m["scope_ref"],
                "memory_id": m["id"],
                "source": m["source"],
                "protected": bool(m["protected"]),
                "predicate": m["predicate"],
                "target_concept_id": m["target_concept_id"],
                "target_label": m["target_label"],
                "updated_at": m["updated_at"],
            }
            for m in rows
            if m["dimension"] in PROFILE_DIMENSIONS
        ]
        previous = await self.store.get_profile(user)
        if attributes == (previous or {}).get("attributes", []):
            return
        version = (previous or {}).get("version", 0) + 1
        if version >= 2**63 - 1:
            raise ValueError("Profile version capacity reached")
        old_ids = {a["memory_id"] for a in (previous or {}).get("attributes", [])}
        removed = len(old_ids - {a["memory_id"] for a in attributes})
        await self.db.execute(
            """INSERT INTO workspace_profiles
            (user_id,version,change_summary,attributes_json,removed_attributes)
            VALUES(?,?,?,?,?)""",
            (
                user,
                version,
                summary,
                json.dumps(attributes, ensure_ascii=False, sort_keys=True),
                removed,
            ),
        )
        await self.store.prune_profile_history(user)

    async def refresh_relationships(self, user=None):
        """Semantic edges are projected from facts; co-occurrence creates no facts.
        / 语义边由事实投影，共现不创建关系。"""
        return None

    async def _manual(self, user, values, dimension=None, key=None):
        dimension = dimension or ("preference" if values["type"] == "preference" else "fact")
        key = canonical_key(
            key
            or values.get("memory_key")
            or profile_key(dimension, values["content"], values["title"])
        )
        if key.startswith("identity."):
            dimension = "identity"
        elif key.startswith("skills."):
            dimension = "skill"
        scope = "global" if dimension == "identity" else values["scope"]
        reference = None if scope == "global" else values.get("scope_ref")
        value = values["value"] if "value" in values else values["content"]
        if dimension in PROFILE_DIMENSIONS and SENSITIVE.search(
            values["content"] + json.dumps(value, ensure_ascii=False)
        ):
            raise ValueError("Sensitive information cannot become profile attributes")
        if dimension == "identity" and (
            not isinstance(value, str) or not value.strip() or len(value) > 100
        ):
            raise ValueError("Identity requires a name of at most 100 characters")
        semantics = {
            "value": value,
            "predicate": values.get("predicate") or dimension,
            "concepts": values.get("concepts", []),
            "source_concept": values.get("source_concept"),
            "target_concept": values.get("target_concept"),
            "protected": values.get("protected", True),
            "basis": "explicit",
        }
        if dimension == "identity" and key not in {"identity.name", "identity.preferred_name"}:
            raise ValueError("Use identity.name or identity.preferred_name for identity")
        if dimension == "skill":
            slot = re.fullmatch(r"skills\.([a-z][a-z0-9_:-]{0,63})\.(level|learning)", key)
            if not slot:
                raise ValueError("Skill attributes require skills.<target>.level or .learning")
            target, facet = slot.groups()
            semantics["target_concept"] = target
            semantics["predicate"] = "proficiency" if facet == "level" else "learning"
            semantics["concepts"] = [
                {"key": target, "kind": "skill", "label": target, "aliases": []}
            ]
            if facet == "level":
                level = LEVELS.get(normalized(str(value)))
                if not level:
                    raise ValueError(
                        "Use beginner, intermediate, advanced or expert for proficiency"
                    )
                semantics["value"] = level
                semantics["source_concept"] = level
                semantics["concepts"].append(
                    {"key": level, "kind": "level", "label": str(value), "aliases": [level]}
                )
            elif not isinstance(value, bool):
                raise ValueError("A learning attribute requires true or false")
        anchors = await self.rows(
            "SELECT id FROM workspace_memories WHERE user_id=? AND scope=? "
            "AND scope_ref IS ? AND memory_key=? "
            "ORDER BY (status='active') DESC,rowid DESC LIMIT 1",
            (user, scope, reference, key),
        )
        claim_id = "manual:" + identifier(
            user,
            scope,
            reference,
            key,
            dimension,
            semantics["value"],
            anchors[0]["id"] if anchors else None,
        )
        await self.db.execute(
            """INSERT INTO memory_claims
            (id,user_id,scope,scope_ref,profile_key,dimension,value,title,status,importance,semantic_json)
            VALUES(?,?,?,?,?,?,?,?,'pending',?,?) ON CONFLICT(id) DO UPDATE SET
            semantic_json=excluded.semantic_json,title=excluded.title,importance=excluded.importance""",
            (
                claim_id,
                user,
                scope,
                reference,
                key,
                dimension,
                values["content"],
                values["title"],
                values.get("importance", 0.7),
                json.dumps(semantics, ensure_ascii=False),
            ),
        )
        claim = (await self.rows("SELECT * FROM memory_claims WHERE id=?", (claim_id,)))[0]
        mid = await self.confirm(claim, values["content"], rebuild=False)
        if mid is None:
            return None
        return await self.store.get_memory(user, mid)

    async def create_memory(self, user, values, dimension=None, key=None):
        async with self.transaction():
            memory = await self._manual(user, values, dimension, key)
            await self.rebuild(user)
            return memory

    async def update_profile(self, user, expected, attributes, summary):
        async with self.transaction():
            previous = await self.store.get_profile(user)
            if expected != (previous or {}).get("version", 0):
                return None
            supplied = {
                (a["scope"], a.get("scope_ref"), canonical_key(a["key"])) for a in attributes
            }
            for old in (previous or {}).get("attributes", []):
                if (old["scope"], old.get("scope_ref"), old["key"]) not in supplied:
                    await self.store.update_memory(user, old["memory_id"], {"status": "archived"})
                    await self.db.execute(
                        "INSERT OR IGNORE INTO memory_learning_exclusions "
                        "SELECT ?,e.thread_id,e.run_id FROM memory_evidence e JOIN memory_claims c "
                        "ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=?",
                        (user, user, old["memory_id"]),
                    )
            for attribute in attributes:
                key = canonical_key(attribute["key"])
                old = next(
                    (
                        a
                        for a in (previous or {}).get("attributes", [])
                        if a["key"] == key
                        and a["scope"] == attribute["scope"]
                        and a.get("scope_ref") == attribute.get("scope_ref")
                    ),
                    None,
                )
                if (
                    old
                    and old["value"] == attribute["value"]
                    and old["dimension"] == attribute["dimension"]
                ):
                    continue
                content = (
                    attribute["value"]
                    if isinstance(attribute["value"], str)
                    else json.dumps(attribute["value"], ensure_ascii=False)
                )
                result = await self._manual(
                    user,
                    {
                        "title": key,
                        "content": content,
                        "type": "semantic",
                        "scope": attribute["scope"],
                        "scope_ref": attribute.get("scope_ref"),
                        "value": attribute["value"],
                        "protected": True,
                    },
                    attribute["dimension"],
                    key,
                )
                if result is None:
                    raise ValueError("Protected memory capacity reached")
            await self.rebuild(user, summary or "Updated profile attributes")
            return await self.store.get_profile(user) or {"version": 0, "attributes": []}

    async def edit_memory(self, user, memory_id, values):
        async with self.transaction():
            old = await self.store.get_memory(user, memory_id)
            if not old:
                return None
            if any(k in values and values[k] != old.get(k) for k in ("content", "value")):
                replacement = {**old, **values, "value": values.get("value", values.get("content"))}
                if "value" in values and "content" not in values:
                    replacement["content"] = (
                        values["value"]
                        if isinstance(values["value"], str)
                        else json.dumps(values["value"], ensure_ascii=False)
                    )
                claim_rows = await self.rows(
                    "SELECT semantic_json FROM memory_claims "
                    "WHERE user_id=? AND memory_id=? LIMIT 1",
                    (user, memory_id),
                )
                if claim_rows:
                    meta = json.loads(claim_rows[0]["semantic_json"])
                    replacement.update(
                        {
                            k: meta.get(k)
                            for k in ("concepts", "predicate", "source_concept", "target_concept")
                            if k in meta
                        }
                    )
                result = await self._manual(user, replacement, old["dimension"], old["memory_key"])
            else:
                if values.get("status") in {"active", "archived"} and old["status"] in {
                    "superseded",
                    "invalidated",
                }:
                    raise ValueError(
                        "Reconfirm the corrected fact instead of restoring an obsolete version"
                    )
                if values.get("status") == "active" and old["status"] != "active":
                    if not await self.ensure_capacity(user):
                        raise ValueError("Protected memory capacity reached")
                if values.get("status") == "active" and old["status"] == "archived":
                    conflicts = await self.rows(
                        "SELECT id FROM workspace_memories WHERE user_id=? "
                        "AND scope=? AND scope_ref IS ? AND memory_key=? AND "
                        "status='active' AND id<>?",
                        (user, old["scope"], old["scope_ref"], old["memory_key"], memory_id),
                    )
                    for conflict in conflicts:
                        await self.db.execute(
                            "UPDATE workspace_memories SET status='superseded',"
                            "superseded_by=? WHERE id=?",
                            (memory_id, conflict["id"]),
                        )
                        await self.db.execute(
                            "UPDATE memory_claims SET status='superseded' "
                            "WHERE user_id=? AND memory_id=?",
                            (user, conflict["id"]),
                        )
                result = await self.store.update_memory(user, memory_id, values)
                if values.get("status") == "active" and old["status"] == "archived":
                    await self.db.execute(
                        "UPDATE memory_claims SET status='confirmed',"
                        "last_source_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                        "WHERE user_id=? AND memory_id=?",
                        (user, memory_id),
                    )
                if values.get("status") in {"archived", "invalidated"}:
                    await self.db.execute(
                        "INSERT OR IGNORE INTO memory_learning_exclusions "
                        "SELECT ?,e.thread_id,e.run_id FROM memory_evidence e JOIN memory_claims c "
                        "ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=?",
                        (user, user, memory_id),
                    )
                    await self.audit(user, values["status"], memory_id, "manual")
            await self.rebuild(user)
            if result:
                await self.store.reindex(user, result["id"])
            return result

    async def suggestions(self, user):
        async with self.lock:
            records = await self.rows(
                "SELECT id,value,title,dimension,profile_key,scope,scope_ref,"
                "pending_reason FROM memory_claims WHERE user_id=? AND status='pending' "
                "ORDER BY rowid DESC LIMIT 100",
                (user,),
            )
            for record in records:
                record["evidence"] = await self.evidence(record["id"])
            return records

    async def review(self, user, claim_id, action, value, key=None):
        async with self.transaction():
            rows = await self.rows(
                "SELECT * FROM memory_claims WHERE user_id=? AND id=?", (user, claim_id)
            )
            if not rows:
                return None
            claim = rows[0]
            if claim["status"] != "pending":
                if claim.get("pending_reason") == "review_edited":
                    accepted = await self.rows(
                        "SELECT a.memory_id FROM memory_audit a JOIN workspace_memories m "
                        "ON m.id=a.memory_id WHERE a.user_id=? AND a.event='review_edited' "
                        "AND a.reason=? ORDER BY a.id DESC LIMIT 1",
                        (user, claim_id),
                    )
                    if accepted:
                        return {"status": "confirmed", "memory_id": accepted[0]["memory_id"]}
                return {"status": claim["status"], "memory_id": claim["memory_id"]}
            if action == "ignore":
                await self.db.execute(
                    "INSERT OR IGNORE INTO memory_learning_exclusions "
                    "SELECT ?,thread_id,run_id FROM memory_evidence WHERE claim_id=?",
                    (user, claim_id),
                )
                await self.db.execute(
                    "UPDATE memory_claims SET status='ignored',pending_reason='user_ignored' "
                    "WHERE id=?",
                    (claim_id,),
                )
                return {"status": "ignored", "memory_id": None}
            edited = (value is not None and value != claim["value"]) or (
                key and canonical_key(key) != claim["profile_key"]
            )
            if edited:
                semantic = json.loads(claim["semantic_json"])
                structured = value if value is not None else semantic.get("value", claim["value"])
                result = await self._manual(
                    user,
                    {
                        "title": claim["title"],
                        "content": value or claim["value"],
                        "value": structured,
                        "type": "semantic",
                        "scope": claim["scope"],
                        "scope_ref": claim["scope_ref"],
                    },
                    claim["dimension"],
                    key or claim["profile_key"],
                )
                if result is None:
                    raise ValueError("All remaining active facts are protected")
                await self.db.execute(
                    "INSERT OR IGNORE INTO memory_learning_exclusions "
                    "SELECT ?,thread_id,run_id FROM memory_evidence WHERE claim_id=?",
                    (user, claim_id),
                )
                await self.db.execute(
                    "UPDATE memory_claims SET status='superseded',"
                    "pending_reason='review_edited' WHERE id=?",
                    (claim_id,),
                )
                await self.audit(user, "review_edited", result["id"], claim_id)
                await self.rebuild(user)
                return {"status": "confirmed", "memory_id": result["id"]}
            if claim.get("pending_reason") == "ambiguous_target":
                raise ValueError(
                    "The skill target is ambiguous; create a target-specific fact first"
                )
            if key:
                claim["profile_key"] = canonical_key(key)
            await self.db.execute(
                "UPDATE memory_evidence SET qualified=1 WHERE claim_id=?", (claim_id,)
            )
            mid = await self.confirm(claim, value or claim["value"])
            if not mid:
                raise ValueError("All remaining active facts are protected")
            return {"status": "confirmed", "memory_id": mid}

    async def memory_evidence(self, user, memory_id):
        async with self.lock:
            return await self.rows(
                """SELECT DISTINCT e.thread_id,e.run_id,e.quote,e.source,e.recorded_at
                FROM memory_evidence e JOIN memory_claims c ON c.id=e.claim_id
                WHERE c.user_id=? AND c.memory_id=? ORDER BY e.recorded_at,e.thread_id,e.run_id""",
                (user, memory_id),
            )

    async def cleanup(self, user):
        await self.db.execute(
            "DELETE FROM memory_search WHERE user_id=? AND memory_id NOT IN "
            "(SELECT id FROM workspace_memories WHERE user_id=?)",
            (user, user),
        )
        await self.db.execute(
            "DELETE FROM memory_concepts WHERE user_id=? AND NOT EXISTS "
            "(SELECT 1 FROM memory_fact_concepts f WHERE f.concept_id=memory_concepts.id)",
            (user,),
        )

    async def delete_memory(self, user, memory_id):
        """Erase the fact, semantic payload, evidence and projections atomically.
        / 原子删除事实、语义载荷、证据及投影；不留下两个事务间的隐私窗口。"""
        async with self.transaction():
            if not await self.store.get_memory(user, memory_id):
                return False
            await self.db.execute(
                "INSERT OR IGNORE INTO memory_learning_exclusions "
                "SELECT ?,e.thread_id,e.run_id FROM memory_evidence e JOIN memory_claims c "
                "ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=?",
                (user, user, memory_id),
            )
            await self.db.execute(
                "DELETE FROM memory_evidence WHERE claim_id IN "
                "(SELECT id FROM memory_claims WHERE user_id=? AND memory_id=?)",
                (user, memory_id),
            )
            await self.db.execute(
                "UPDATE memory_claims SET status='ignored',value='',title='',"
                "semantic_json='{}',pending_reason=NULL WHERE user_id=? AND memory_id=?",
                (user, memory_id),
            )
            await self.store.delete_memory(user, memory_id)
            await self.rebuild(user)
            await self.redact_profile_history(user, memory_id)
            await self.cleanup(user)
            await self.audit(user, "deleted", memory_id)
            return True

    async def forget_thread(self, user, thread, delete_source):
        await super().forget_thread(user, thread, delete_source)
        async with self.transaction():
            await self.db.execute(
                "DELETE FROM memory_reinforcements WHERE session_id=? AND "
                "memory_id IN (SELECT id FROM workspace_memories WHERE user_id=?)",
                (thread, user),
            )
            await self.db.execute(
                "UPDATE workspace_memories SET effective_frequency=(SELECT count(*) "
                "FROM memory_reinforcements r WHERE "
                "r.memory_id=workspace_memories.id) WHERE user_id=?",
                (user,),
            )
            await self.cleanup(user)
