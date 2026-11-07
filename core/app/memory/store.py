"""SQLite V3 fact projections, semantic graph and scoped local retrieval.
/ SQLite V3 事实投影、语义图谱及局部检索。"""

import json
from contextlib import asynccontextmanager

from app.infrastructure.workspace import WorkspaceStore, memory_terms
from app.memory.schema import install_schema
from app.memory.semantics import (
    SCORING_VERSION,
    frequency_strength,
    identity_intent,
    mentions,
    normalized,
    scope_allowed,
)


class MemoryStore(WorkspaceStore):
    """Reuse existing store lifecycle/management; V3 owns all online projections.
    / 复用存储生命周期和管理能力；在线画像与检索全部使用 V3。"""

    @classmethod
    @asynccontextmanager
    async def open(cls, path, timeout):
        async with super().open(path, timeout) as store:
            await install_schema(store.connection)
            yield store

    async def rows(self, sql, args=()):
        async with self.connection.execute(sql, args) as cursor:
            return [dict(row) async for row in cursor]

    async def scoring_c(self, user):
        """Read stable scoring config; it never depends on collection
        statistics. / 读取固定评分参数。"""
        # Configuration is owned by the conversations store; composition root refreshes this map.
        # 配置属于会话存储；组装根更新映射，不从词频分布推导参数。
        return getattr(self, "strength_parameters", {}).get(user, 4.0)

    async def decorate(self, record):
        if not record:
            return None
        record["value"] = json.loads(record.pop("value_json", "null"))
        record["protected"] = bool(record["protected"])
        record["strength"] = frequency_strength(
            record["effective_frequency"], await self.scoring_c(record["user_id"])
        )
        record["scoring_version"] = SCORING_VERSION
        record["concepts"] = await self.rows(
            "SELECT c.id,c.label,c.kind FROM memory_concepts c "
            "JOIN memory_fact_concepts f ON f.concept_id=c.id WHERE f.memory_id=? AND c.user_id=? "
            "ORDER BY c.id",
            (record["id"], record["user_id"]),
        )
        record["concept_ids"] = [c["id"] for c in record["concepts"]]
        return record

    async def get_memory(self, user_id, memory_id):
        return await self.decorate(await super().get_memory(user_id, memory_id))

    async def list_memories(self, user_id, **options):
        return [await self.decorate(row) for row in await super().list_memories(user_id, **options)]

    async def get_profile(self, user_id, version=None):
        records = await self.rows(
            """SELECT version,attributes_json,change_summary,created_at,
            removed_attributes,redacted_attributes FROM workspace_profiles
            WHERE user_id=? AND (? IS NULL OR version=?) ORDER BY version DESC LIMIT 1""",
            (user_id, version, version),
        )
        if not records:
            return None
        row = records[0]
        row["attributes"] = json.loads(row.pop("attributes_json"))
        return {"user_id": user_id, **row}

    async def reindex(self, user, memory_id):
        """Index only rendered facts and their typed concept aliases. /
        索引事实及同类型概念别名。"""
        await self.connection.execute("DELETE FROM memory_search WHERE memory_id=?", (memory_id,))
        records = await self.rows(
            "SELECT * FROM workspace_memories WHERE user_id=? AND id=?", (user, memory_id)
        )
        if not records:
            return
        item = records[0]
        concepts = await self.rows(
            """SELECT c.* FROM memory_concepts c JOIN memory_fact_concepts f
            ON f.concept_id=c.id WHERE f.memory_id=? AND c.user_id=?""",
            (memory_id, user),
        )
        text = " ".join(
            [
                item["title"],
                item["content"],
                item["memory_key"] or "",
                *[c["label"] + " " + " ".join(json.loads(c["aliases_json"])) for c in concepts],
            ]
        )
        if (item["memory_key"] or "").startswith("identity."):
            text += " 姓名 名字 称呼 昵称 我叫 我是 name nickname identity"
        terms = memory_terms(text, 512)
        await self.connection.execute(
            "INSERT INTO memory_search VALUES(?,?,?)", (memory_id, user, " ".join(terms))
        )

    async def concept_frequency(self, user, concept, thread, project):
        """Count sessions, not linked facts; deletion removes unsupported event credit.
        / 按会话计数，不按关联事实数量；删除后无来源的计数不再参与。"""
        events = await self.rows(
            """SELECT r.scope,r.scope_ref,r.session_id
            FROM concept_reinforcements r JOIN memory_concepts c ON c.id=r.concept_id
            WHERE c.user_id=? AND c.id=? AND EXISTS (
                SELECT 1 FROM memory_fact_concepts f JOIN workspace_memories m ON m.id=f.memory_id
                JOIN memory_reinforcements e ON e.memory_id=m.id AND e.session_id=r.session_id
                WHERE f.concept_id=c.id AND m.user_id=c.user_id AND m.status<>'invalidated'
                  AND m.scope=r.scope AND coalesce(m.scope_ref,'')=r.scope_ref)""",
            (user, concept),
        )
        return len(
            {
                e["session_id"]
                for e in events
                if scope_allowed(e["scope"], e["scope_ref"], thread, project)
            }
        )

    async def concept(self, user, concept_id, thread="", project=None):
        records = await self.rows(
            "SELECT * FROM memory_concepts WHERE user_id=? AND id=?", (user, concept_id)
        )
        if not records:
            return None
        record = records[0]
        facts = [
            await self.decorate(row)
            for row in await self.rows(
                """SELECT m.*
            FROM workspace_memories m JOIN memory_fact_concepts f ON f.memory_id=m.id
            WHERE m.user_id=? AND f.concept_id=? AND m.status='active' ORDER BY m.id""",
                (user, concept_id),
            )
            if scope_allowed(row["scope"], row["scope_ref"], thread, project)
        ]
        if not facts:
            return None
        frequency = await self.concept_frequency(user, concept_id, thread, project)
        return {
            **record,
            "aliases": json.loads(record.pop("aliases_json")),
            "facts": facts,
            "effective_frequency": frequency,
            "strength": frequency_strength(frequency, await self.scoring_c(user)),
        }

    async def retrieve(self, *, user_id, query, thread_id, project_id, limit):
        """Filter first, local recall then expand one hop through fact/concept membership.
        / 先过滤作用域，再本地召回并沿事实概念关系扩展一跳。"""
        intent = identity_intent(query)
        terms = memory_terms(query, 24)
        eligible = await self.rows(
            """SELECT * FROM workspace_memories WHERE user_id=?
            AND status='active' AND ((scope='global' AND scope_ref IS NULL)
              OR (scope='thread' AND scope_ref=?) OR (scope='project' AND scope_ref=?))""",
            (user_id, thread_id, project_id),
        )
        rank = {"global": 0, "project": 1, "thread": 2}
        selected_slots = {}
        for fact in eligible:
            slot = fact["memory_key"] or fact["id"]
            if (
                slot not in selected_slots
                or rank[fact["scope"]] > rank[selected_slots[slot]["scope"]]
            ):
                selected_slots[slot] = fact
        eligible = list(selected_slots.values())
        matches = set()
        if terms:
            expression = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
            matches = {
                r["memory_id"]
                for r in await self.rows(
                    """SELECT s.memory_id
                FROM memory_search s JOIN workspace_memories m ON m.id=s.memory_id
                WHERE memory_search MATCH ? AND m.user_id=? AND m.status='active'
                  AND (m.scope='global' OR (m.scope='thread' AND m.scope_ref=?)
                       OR (m.scope='project' AND m.scope_ref=?)) LIMIT 256""",
                    (expression, user_id, thread_id, project_id),
                )
            }
        concepts = [
            c
            for c in await self.rows("SELECT * FROM memory_concepts WHERE user_id=?", (user_id,))
            if scope_allowed(c["scope"], c["scope_ref"], thread_id, project_id)
        ]
        hit_concepts = set()
        normalized_query = normalized(query)
        for c in concepts:
            aliases = [c["label"], c["concept_key"], *json.loads(c["aliases_json"])]
            if any(
                (len(normalized(a)) >= 2 or c["kind"] == "skill") and mentions(normalized_query, a)
                for a in aliases
            ):
                hit_concepts.add(c["id"])
        hit_keys = {(c["kind"], c["concept_key"]) for c in concepts if c["id"] in hit_concepts}
        hit_concepts.update(c["id"] for c in concepts if (c["kind"], c["concept_key"]) in hit_keys)
        memberships = await self.rows(
            """SELECT f.* FROM memory_fact_concepts f
            JOIN workspace_memories m ON m.id=f.memory_id WHERE m.user_id=?""",
            (user_id,),
        )
        by_fact = {}
        for item in memberships:
            by_fact.setdefault(item["memory_id"], set()).add(item["concept_id"])
        selected = []
        for item in eligible:
            identity = bool(
                intent and item["dimension"] == "identity" and item["scope"] == "global"
            )
            matched = by_fact.get(item["id"], set()) & hit_concepts
            if not identity and item["id"] not in matches and not matched:
                continue
            tokens = set(memory_terms(item["title"] + " " + item["content"], 128))
            relevance = (
                1.0
                if identity or matched
                else min(1.0, len(tokens & set(terms)) / max(1, len(terms)))
            )
            if relevance <= 0:
                continue
            item = await self.decorate(item)
            if item["type"] == "episodic":
                reported = await self.rows(
                    "SELECT min(e.recorded_at) AS reported_at FROM memory_evidence e "
                    "JOIN memory_claims c ON c.id=e.claim_id "
                    "WHERE c.user_id=? AND c.memory_id=?",
                    (user_id, item["id"]),
                )
                item["reported_at"] = reported[0]["reported_at"]
            item["relevance"] = relevance
            item["retrieval_score"] = (
                0.75 * relevance + 0.15 * item["strength"] + 0.10 * item["importance"]
            )
            item["match_reason"] = (
                "identity_intent" if identity else ("concept_relation" if matched else "lexical")
            )
            item["matched_concept_ids"] = sorted(matched)
            selected.append(item)
        return sorted(selected, key=lambda r: (-r["retrieval_score"], r["id"]))[:limit]

    async def graph(self, user_id, limit, focus=None, *, thread_id="", project_id=None):
        """Semantic edges are fact projections; manual links remain separately identified.
        / 语义边直接投影事实，手动关联保留独立来源标记。"""
        facts = [
            await self.decorate(row)
            for row in await self.rows(
                "SELECT * FROM workspace_memories WHERE user_id=? AND status='active' ORDER BY id",
                (user_id,),
            )
            if scope_allowed(row["scope"], row["scope_ref"], thread_id, project_id)
        ]
        nodes, edges = {}, []
        for fact in facts:
            for cid in fact["concept_ids"]:
                if cid not in nodes:
                    concept = await self.concept(user_id, cid, thread_id, project_id)
                    if concept:
                        nodes[cid] = {
                            "id": cid,
                            "label": concept["label"],
                            "type": fact["type"],
                            "importance": fact["importance"],
                            "node_kind": "concept",
                            "concept_kind": concept["kind"],
                            "scope": concept["scope"],
                            "scope_ref": concept["scope_ref"] or None,
                            "fact_ids": [],
                            "effective_frequency": concept["effective_frequency"],
                            "strength": concept["strength"],
                        }
                if cid in nodes:
                    nodes[cid]["fact_ids"].append(fact["id"])
            source, target = fact["source_concept_id"], fact["target_concept_id"]
            if source and target and source != target:
                edges.append(
                    {
                        "id": fact["id"],
                        "fact_id": fact["id"],
                        "source": source,
                        "target": target,
                        "relation": fact["predicate"],
                        "relevance": fact["importance"],
                        "directed": True,
                        "origin": "semantic",
                        "effective_frequency": fact["effective_frequency"],
                        "strength": fact["strength"],
                        "evidence_ids": [
                            r["thread_id"] + ":" + r["run_id"]
                            for r in await self.rows(
                                """SELECT e.thread_id,e.run_id FROM
                                memory_evidence e JOIN memory_claims c
                        ON c.id=e.claim_id WHERE c.user_id=? AND c.memory_id=? LIMIT 8""",
                                (user_id, fact["id"]),
                            )
                        ],
                    }
                )
            if not fact["concept_ids"]:
                nodes[fact["id"]] = {
                    "id": fact["id"],
                    "label": fact["title"],
                    "type": fact["type"],
                    "importance": fact["importance"],
                    "node_kind": "fact",
                    "fact_ids": [fact["id"]],
                    "effective_frequency": fact["effective_frequency"],
                    "strength": fact["strength"],
                }
        active_ids = {f["id"] for f in facts}
        manual = await super().graph(user_id, 100000)
        for edge in manual["edges"]:
            if edge["source"] in active_ids and edge["target"] in active_ids:
                for mid in (edge["source"], edge["target"]):
                    fact = next(f for f in facts if f["id"] == mid)
                    nodes.setdefault(
                        mid,
                        {
                            "id": mid,
                            "label": fact["title"],
                            "type": fact["type"],
                            "importance": fact["importance"],
                            "node_kind": "fact",
                            "fact_ids": [mid],
                        },
                    )
                edges.append(edge)
        focus_ids = {focus} | {cid for f in facts if f["id"] == focus for cid in f["concept_ids"]}
        ordered = sorted(
            nodes.values(), key=lambda n: (n["id"] not in focus_ids, -n["importance"], n["id"])
        )[:limit]
        included = {n["id"] for n in ordered}
        return {
            "nodes": ordered,
            "edges": [e for e in edges if e["source"] in included and e["target"] in included],
            "total": len(nodes),
            "truncated": len(nodes) > limit,
            "limit": limit,
        }

    async def metrics(self, user):
        states = {
            r["status"]: r["count"]
            for r in await self.rows(
                (
                    "SELECT status,count(*) AS count FROM workspace_memories WHERE "
                    "user_id=? GROUP BY status"
                ),
                (user,),
            )
        }
        events = {
            r["event"]: r["count"]
            for r in await self.rows(
                "SELECT event,count(*) AS count FROM memory_audit WHERE user_id=? GROUP BY event",
                (user,),
            )
        }
        total = events.get("reinforced", 0) + events.get("reinforcement_deduplicated", 0)
        return {
            "states": states,
            "events": events,
            "reinforcement_dedup_rate": events.get("reinforcement_deduplicated", 0) / total
            if total
            else None,
            "scoring_version": SCORING_VERSION,
        }
