"""Read-only fact answers and metadata-only per-turn diagnostics.
/ 只读事实直答及不含正文的逐轮诊断。"""

import json
import re

from app.memory.semantics import identity_intent


def memory_trace(state, *, source="model", status=None):
    """Describe provided evidence; never claim that the model adopted it. /
    记录提供的证据而非模型采纳。"""
    used = set(state.get("used_memory_ids", []))
    candidates = {m["id"]: m for m in state.get("relevant_memories", [])}
    for attribute in (state.get("user_profile") or {}).get("attributes", []):
        mid = attribute["memory_id"]
        if mid in used and mid not in candidates:
            candidates[mid] = {"id": mid, "match_reason": "profile_attribute", "concept_ids": []}
    if status is None:
        status = "provided" if used else "no_match"
        if not state.get("personalization_enabled"):
            status = "disabled"
        elif any("TIMEOUT" in w or "FAILED" in w for w in state.get("warnings", [])):
            status = "read_failed"
        elif candidates and not used:
            status = "budget_excluded"
    return {
        "schema_version": 3,
        "intent": identity_intent(state["user_input"]),
        "response_source": source,
        "status": status,
        "facts": [
            {
                "id": mid,
                "included": mid in used,
                "reason": item.get("match_reason", "profile_attribute")
                if mid in used
                else "budget_excluded",
                "score": item.get("retrieval_score"),
                "strength": item.get("strength"),
                "concept_ids": item.get("matched_concept_ids", item.get("concept_ids", []))[:8],
                "relation_path": {
                    "source": item.get("source_concept_id"),
                    "predicate": item.get("predicate"),
                    "target": item.get("target_concept_id"),
                },
            }
            for mid, item in list(candidates.items())[:24]
        ],
        "context_token_estimate": state.get("context_token_estimate", 0),
        "warnings": list(state.get("warnings", []))[:12],
    }


class IdentityAnswers:
    """Deterministic identity answers from live owned global facts. /
    由当前本人全局事实确定性直答。"""

    def __init__(self, store, conversations):
        self.store, self.conversations = store, conversations

    async def __call__(self, user, query, enabled):
        intent = identity_intent(query)
        if intent is None:
            return None
        zh = bool(re.search(r"[\u3400-\u9fff]", query))
        status, records = "disabled", []
        if enabled:
            records = await self.store.rows(
                """SELECT * FROM workspace_memories WHERE user_id=?
                AND dimension='identity' AND scope='global' AND scope_ref IS NULL
                AND status='active' ORDER BY memory_key,id""",
                (user,),
            )
            status = "no_record"
        keys = (
            ("identity.preferred_name",)
            if intent == "preferred_name"
            else (
                ("identity.name",)
                if intent == "name"
                else ("identity.name", "identity.preferred_name")
            )
        )
        selected = [
            m
            for m in records
            if m["memory_key"] in keys
            and isinstance(json.loads(m["value_json"]), str)
            and json.loads(m["value_json"]).strip()
        ]
        pending = (
            await self.store.rows(
                """SELECT profile_key,value,semantic_json,status FROM memory_claims WHERE user_id=?
            AND dimension='identity' AND scope='global' AND status IN ('pending','observed')""",
                (user,),
            )
            if enabled
            else []
        )
        current_values = {m["memory_key"]: json.loads(m["value_json"]) for m in selected}
        conflicts = [
            p
            for p in pending
            if p["profile_key"] in current_values
            and json.loads(p["semantic_json"]).get("value", p["value"])
            != current_values[p["profile_key"]]
        ]
        if conflicts:
            return {
                "text": "存在待确认的姓名变更，我暂时无法确认当前姓名。"
                if zh
                else "A name change is awaiting confirmation; I cannot confirm your current name.",
                "status": "identity_conflict",
                "memory_ids": [],
            }
        if selected:
            status = "identity_fact"
            values = {m["memory_key"]: json.loads(m["value_json"]) for m in selected}
            if "identity.name" in values:
                text = ("我记录的姓名是：" if zh else "Your recorded name is: ") + values[
                    "identity.name"
                ]
            else:
                text = ("你希望我称呼你为：" if zh else "Your preferred name is: ") + values[
                    "identity.preferred_name"
                ]
            if len(values) > 1 and values["identity.preferred_name"] != values["identity.name"]:
                text += ("；你希望我称呼你为：" if zh else "; your preferred name is: ") + values[
                    "identity.preferred_name"
                ]
        elif not enabled:
            text = (
                "定制记忆已关闭，我不会读取已保存的姓名。"
                if zh
                else ("Personalized memory is off, so I am not reading a saved name.")
            )
        elif pending:
            status = "pending_confirmation"
            text = (
                "姓名信息尚待确认，我目前没有可可靠使用的姓名记录。"
                if zh
                else (
                    "Your identity information is awaiting confirmation; I have no "
                    "confirmed name to use."
                )
            )
        elif (await self.conversations.learning_status(user))["pending"]:
            status = "pending_learning"
            text = (
                "记忆仍在整理中，我目前没有已确认的姓名记录。"
                if zh
                else ("Memory learning is still pending; I do not yet have a confirmed name.")
            )
        else:
            text = "我没有你的姓名记录。" if zh else "I have no record of your name."
        return {"text": text, "status": status, "memory_ids": [m["id"] for m in selected]}
