"""Evidence-backed V3 write service; facts are the sole profile/semantic-edge authority.
/ 有证据的 V3 写入服务：事实是画像和语义边的唯一权威。"""

import json
import re
from dataclasses import dataclass
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
from app.personalization.settings import MemoryConfiguration


@dataclass(frozen=True)
class CapacityPlan:
    """Capacity decision for one active-memory limit.
    / 单个有效记忆上限的容量决策。

    Attributes:
        limit: Requested active-memory limit. / 请求的有效记忆上限。
        active: Active fact count before archiving. / 归档前的有效事实数。
        protected: Minimum capacity that must stay active. / 必须保留的最低容量。
        required: Facts that must be archived to reach the limit. / 为达到上限必须归档的事实数。
        archivable: Unprotected active facts available as candidates. / 可归档的非保护候选数。
        reachable: Whether the candidates suffice for the requested limit. / 候选是否足够。
        archive: Candidate ids that would be archived, when reachable. / 可达时将被归档的候选 ID。
    """

    limit: int
    active: int
    protected: int
    required: int
    archivable: int
    reachable: bool
    archive: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        """Return the JSON-safe capacity projection used by the settings API.

        / 返回设置接口使用的容量投影（可 JSON 序列化）。

        Returns:
            Mapping with the limit, counts and reachability. / 含上限、计数与可达性的映射。
        """

        return {
            "limit": self.limit,
            "active": self.active,
            "protected": self.protected,
            "required": self.required,
            "archivable": self.archivable,
            "reachable": self.reachable,
        }


def _profile_value(raw: str):
    """Read one stored value without letting one bad row break profile projection.
    / 读取单条存储值；单行损坏不应破坏整个画像投影。

    Args:
        raw: Serialized ``value_json`` column. / 序列化的 ``value_json`` 列。

    Returns:
        Parsed JSON value, or the raw string when parsing fails. / 解析后的值；失败时返回原字符串。
    """
    try:
        return json.loads(raw)
    except (TypeError, ValueError):
        return raw


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

    @staticmethod
    def _capacity_rank(memory, strength_c):
        """Rank unprotected facts for capacity archiving. / 为容量归档排序非保护事实。

        Args:
            memory: Active fact row with importance and effective frequency. /
                含重要度与实际频次的有效事实行。
            strength_c: Half-saturation constant of the scoring function. / 评分函数的半饱和常数。

        Returns:
            Sort key: lower-value facts are archived first. / 排序键：价值较低的事实优先归档。
        """

        return (
            0.6 * memory["importance"]
            + 0.4 * frequency_strength(memory["effective_frequency"], strength_c),
            memory["id"],
        )

    async def _configured_strength_c(self, user):
        """Read the scoring parameter saved with the current configuration.

        / 读取当前配置中保存的评分参数。

        Args:
            user: Owning user id. / 所属用户 ID。

        Returns:
            Configured ``strength_c`` or the deployment default.
            / 配置的 ``strength_c`` 或部署默认值。
        """

        saved = await self.conversations.memory_configuration(user)
        configuration = MemoryConfiguration.model_validate(saved[1]) if saved else None
        return configuration.strength_c if configuration else MemoryConfiguration().strength_c

    async def _plan_capacity(self, user, limit, strength_c=None, incoming=0):
        """Compute the capacity plan without acquiring the write lock.

        Callers either hold ``self.lock`` already or are inside a workspace transaction.
        / 计算容量计划但不获取写锁；调用方已持有 ``self.lock`` 或处于工作区事务中。

        Args:
            user: Owning user id. / 所属用户 ID。
            limit: Target active-memory limit. / 目标有效记忆上限。
            strength_c: Scoring constant; reads the saved configuration when omitted. /
                评分常数；省略时读取已保存配置。
            incoming: Facts about to be inserted before archiving. / 归档前即将插入的事实数。

        Returns:
            The capacity decision for the requested limit. / 请求上限的容量决策。
        """

        if strength_c is None:
            strength_c = await self._configured_strength_c(user)
        active = await self.rows(
            "SELECT id,importance,effective_frequency,protected FROM "
            "workspace_memories WHERE user_id=? AND status='active'",
            (user,),
        )
        candidates = sorted(
            (m for m in active if not m["protected"]),
            key=lambda memory: self._capacity_rank(memory, strength_c),
        )
        required = max(0, len(active) + incoming - limit)
        reachable = required <= len(candidates)
        return CapacityPlan(
            limit=limit,
            active=len(active),
            protected=len(active) - len(candidates),
            required=required,
            archivable=len(candidates),
            reachable=reachable,
            archive=tuple(m["id"] for m in candidates[:required]) if reachable else (),
        )

    async def plan_capacity(self, user, new_limit, strength_c=None):
        """Plan archiving for a new limit under the memory write lock.

        Protected facts form the minimum capacity; only unprotected facts are candidates.
        / 在记忆写锁下为新上限规划归档；受保护事实构成最低容量，只有非保护事实可作候选。

        Args:
            user: Owning user id. / 所属用户 ID。
            new_limit: Requested active-memory limit. / 请求的有效记忆上限。
            strength_c: Scoring constant to rank candidates, or None for the saved one. /
                排序候选使用的评分常数；None 表示读取已保存值。

        Returns:
            The capacity decision for the requested limit. / 请求上限的容量决策。
        """

        async with self.lock:
            return await self._plan_capacity(user, new_limit, strength_c)

    async def ensure_capacity(self, user, incoming=1):
        configuration = (await self.planner.configuration(user))[1] if self.planner else None
        limit = configuration.active_limit if configuration else 500
        c = configuration.strength_c if configuration else 4.0
        plan = await self._plan_capacity(user, limit, c, incoming)
        if plan.required <= 0:
            return True
        if not plan.reachable:
            await self.audit(user, "capacity_blocked", reason="all_remaining_facts_protected")
            return False
        for memory_id in plan.archive:
            await self.store.update_memory(user, memory_id, {"status": "archived"})
            await self.audit(user, "archived", memory_id, "capacity")
        return True

    async def reconcile_configuration(self, user, revision):
        """Apply one saved configuration revision to facts, profile and scoring.

        A save advances the stored revision before capacity reconciliation; this method makes
        the application idempotent so a crash, restart or retry converges. It archives
        over-capacity facts and rebuilds the profile inside one workspace transaction, then
        marks the revision applied and updates the scoring parameter.
        / 保存会先推进修订号，再执行容量协调；本方法保证应用幂等，使崩溃、重启或重试都能收敛。
        它在同一个工作区事务内归档超容量事实并重建画像，随后标记该修订已生效并更新评分参数。

        Args:
            user: Owning user id. / 所属用户 ID。
            revision: Saved revision to apply. / 要应用的已保存修订号。

        Returns:
            Application state with ``applied`` and ``capacity`` details, or None when the
            revision does not exist. / 含 ``applied`` 与 ``capacity`` 的应用状态；修订不存在
            时为 None。
        """

        state = await self.conversations.memory_configuration_state(user)
        if state is None:
            return None
        if state["applied_revision"] >= revision:
            return {**state, "applied": True, "capacity": None}
        if state["revision"] != revision:
            # A newer save superseded this revision, which stays pending for the next retry.
            # 更新的保存已取代此修订；该修订由下一次重试继续处理。
            return {**state, "applied": False, "capacity": None}
        configuration = MemoryConfiguration.model_validate(state["configuration"])
        async with self.transaction():
            current = await self.conversations.memory_configuration_state(user)
            if current is None:
                return None
            if current["applied_revision"] >= revision:
                return {**current, "applied": True, "capacity": None}
            if current["revision"] != revision:
                return {**current, "applied": False, "capacity": None}
            configuration = MemoryConfiguration.model_validate(current["configuration"])
            plan = await self._plan_capacity(
                user, configuration.active_limit, configuration.strength_c
            )
            if not plan.reachable:
                # The revision stays pending; protected facts must leave the active set first.
                # 该修订保持待应用；需先让受保护事实退出有效集合。
                return {**current, "applied": False, "capacity": plan.as_dict()}
            for memory_id in plan.archive:
                await self.store.update_memory(user, memory_id, {"status": "archived"})
                await self.audit(user, "archived", memory_id, "capacity")
            await self.rebuild(user)
        await self.conversations.mark_memory_configuration_applied(user, revision)
        final = await self.conversations.memory_configuration_state(user) or state
        if final["revision"] == revision:
            parameters = getattr(self.store, "strength_parameters", None)
            if parameters is None:
                parameters = self.store.strength_parameters = {}
            parameters[user] = configuration.strength_c
        return {
            **final,
            "applied": final["applied_revision"] >= revision,
            "capacity": plan.as_dict(),
        }

    async def resume_pending_configurations(self):
        """Apply saved revisions whose capacity reconciliation did not finish.

        A capacity conflict waits for the user to free protected capacity; a transient failure
        keeps returning True so the worker retries it. / 容量冲突等待用户释放受保护容量；暂时性
        失败持续返回 True，由工作器继续重试。

        Returns:
            True when at least one revision needs another apply attempt.
            / 仍有修订需要再次尝试应用时为 True。
        """

        remaining = False
        for user, revision in await self.conversations.pending_memory_configurations():
            try:
                result = await self.reconcile_configuration(user, revision)
            except Exception:
                remaining = True
                continue
            if result is None or result["revision"] != revision:
                continue
            if not result["applied"] and result.get("capacity") is None:
                remaining = True
        return remaining

    async def _ensure_projection_state(self):
        """Create the durable pending-projection marker table on first use.

        The marker lives in the workspace database so a failed profile rebuild is recorded
        atomically with the facts that made it necessary. / 标记存放在工作区数据库中，使画像重建
        失败与引发它的事实写入在同一事务内原子记录。
        """

        await self.db.execute(
            """CREATE TABLE IF NOT EXISTS memory_projection_state (
                user_id TEXT PRIMARY KEY,
                needs_rebuild INTEGER NOT NULL DEFAULT 0,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error TEXT,
                requested_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
            )"""
        )

    async def mark_rebuild_pending(self, user, error="PROFILE_REBUILD_FAILED"):
        """Persist a needs-rebuild marker inside the caller's fact transaction.

        / 在调用方的事实事务内持久化待重建标记。

        Args:
            user: Owning user id. / 所属用户 ID。
            error: Stable failure code kept for diagnostics. / 用于诊断的稳定失败码。
        """

        await self._ensure_projection_state()
        await self.db.execute(
            """INSERT INTO memory_projection_state (user_id, needs_rebuild, last_error)
               VALUES (?,1,?) ON CONFLICT(user_id) DO UPDATE SET needs_rebuild=1,
               last_error=excluded.last_error,
               requested_at=strftime('%Y-%m-%dT%H:%M:%fZ','now')""",
            (user, error),
        )

    async def clear_rebuild_pending(self, user):
        """Clear one durable needs-rebuild marker. / 清除一个持久化待重建标记。

        Args:
            user: Owning user id. / 所属用户 ID。
        """

        await self._ensure_projection_state()
        await self.db.execute(
            "UPDATE memory_projection_state SET needs_rebuild=0,last_error=NULL WHERE user_id=?",
            (user,),
        )

    async def pending_rebuilds(self):
        """List users whose profile projection still needs a rebuild.

        / 列出画像投影仍待重建的用户。

        Returns:
            User ids with an outstanding marker. / 仍有未完成标记的用户 ID。
        """

        await self._ensure_projection_state()
        records = await self.rows(
            "SELECT user_id FROM memory_projection_state WHERE needs_rebuild=1 ORDER BY user_id"
        )
        return [record["user_id"] for record in records]

    async def retry_projection(self, user):
        """Rebuild one pending profile projection and clear its marker on success.

        / 重建一个待处理画像投影；成功时清除标记。

        Args:
            user: Owning user id. / 所属用户 ID。

        Returns:
            True when the projection converged. / 投影已收敛时为 True。
        """

        try:
            async with self.transaction():
                await self.rebuild(user)
                await self.refresh_relationships(user)
                await self.clear_rebuild_pending(user)
        except Exception as error:
            await self._record_projection_failure(user, error)
            return False
        return True

    async def _record_projection_failure(self, user, error):
        """Record a failed projection retry without blocking the learner.

        / 记录一次失败的投影重试，但不阻塞学习器。

        Args:
            user: Owning user id. / 所属用户 ID。
            error: The failure raised by the rebuild attempt. / 重建尝试抛出的失败。
        """

        await self._ensure_projection_state()
        await self.db.execute(
            "UPDATE memory_projection_state SET attempts=attempts+1,last_error=? "
            "WHERE user_id=? AND needs_rebuild=1",
            (type(error).__name__, user),
        )

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
            resolved = await self._concepts(user, semantics, scope, reference)
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
            for previous in slots:
                await self.db.execute(
                    "UPDATE workspace_memories SET superseded_by=? WHERE id=?",
                    (memory_id, previous["id"]),
                )
            await self.audit(user, "created", memory_id)
        if resolved:
            await self.db.executemany(
                "INSERT OR IGNORE INTO memory_fact_concepts VALUES(?,?)",
                [(memory_id, cid) for cid in sorted(set(resolved.values()))],
            )
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
                "value": _profile_value(m["value_json"]),
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
                json.dumps(attributes, ensure_ascii=False, sort_keys=True, default=str),
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
