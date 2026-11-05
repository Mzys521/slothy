"""Measurements from completed model calls in one conversation turn."""

from pydantic import BaseModel


class RunMetrics(BaseModel):
    model: str | None = None
    reasoning_effort: str | None = None
    total_tokens: int
    output_tokens: int
    input_tokens: int
    cached_input_tokens: int
    cache_reported_input_tokens: int
    model_seconds: float
    context_tokens: int
    context_window: int | None = None
    cache_write_tokens: int | None = None
    prefix_hit_ratio: float | None = None
    prompt_plan_id: str | None = None
    prompt_version: str | None = None
    l1_hash: str | None = None
    l2_hash: str | None = None
    l3_hash: str | None = None
    l1_bytes: int | None = None
    l2_bytes: int | None = None
    toolset_hash: str | None = None
    l2_turns: int | None = None
    l3_bytes: int | None = None
    profile_snapshot_version: int | None = None
    profile_snapshot_revision: int | None = None
    history_segment_version: int | None = None
    summary_tokens: int = 0


def run_metrics(state: dict) -> RunMetrics | None:
    calls = state.get("model_stats") or []
    if not calls:
        return None
    selection = state.get("model_selection") or {}
    reported = sum(call["input_tokens"] for call in calls if call["cached_tokens"] is not None)
    read = sum(call["cached_tokens"] or 0 for call in calls)
    writes = [
        call["cache_write_tokens"] for call in calls if call.get("cache_write_tokens") is not None
    ]
    metadata = {
        key: calls[-1].get(key)
        for key in (
            "prompt_plan_id",
            "prompt_version",
            "l1_hash",
            "l2_hash",
            "l3_hash",
            "l1_bytes",
            "l2_bytes",
            "toolset_hash",
            "l2_turns",
            "l3_bytes",
            "profile_snapshot_version",
            "profile_snapshot_revision",
            "history_segment_version",
        )
    }
    return RunMetrics(
        model=selection.get("id"),
        reasoning_effort=selection.get("effort"),
        total_tokens=sum(call["total_tokens"] for call in calls),
        output_tokens=sum(call["output_tokens"] for call in calls),
        input_tokens=sum(call["input_tokens"] for call in calls),
        cached_input_tokens=sum(call["cached_tokens"] or 0 for call in calls),
        cache_reported_input_tokens=sum(
            call["input_tokens"] for call in calls if call["cached_tokens"] is not None
        ),
        model_seconds=sum(call["seconds"] for call in calls),
        context_tokens=calls[-1]["total_tokens"],
        context_window=selection.get("context_window"),
        cache_write_tokens=sum(writes) if writes else None,
        summary_tokens=sum(
            call["total_tokens"] for call in calls if call.get("purpose") == "history_summary"
        ),
        prefix_hit_ratio=read / reported if reported else None,
        **metadata,
    )
