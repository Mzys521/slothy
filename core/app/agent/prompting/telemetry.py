"""Bounded counters without retaining prompt bodies or user profile text."""

from collections import OrderedDict


def cache_write_tokens(usage: dict | None) -> int | None:
    details = (usage or {}).get("input_token_details") or {}
    specific = [
        details[key]
        for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        if details.get(key) is not None
    ]
    return sum(specific) if specific else details.get("cache_creation")


class PromptTelemetry:
    def __init__(self):
        self.threads = OrderedDict()
        self.segments = OrderedDict()
        self.calls = self.reported_input = self.read_tokens = self.write_tokens = 0
        self.compressions = 0
        self.summary_calls = 0

    def observe(
        self, user: str, thread: str, run: str, namespace: str, plan: dict, usage: dict | None
    ):
        key = (user, thread, namespace)
        previous = self.threads.pop(key, {})
        stable = previous.get("stable_turns", 0)
        if previous.get("run") != run:
            stable = stable + 1 if previous.get("hash") == plan["l1_hash"] else 1
        elif previous.get("hash") != plan["l1_hash"]:
            stable = 1
        segment = plan["history_segment_version"]
        session = (user, thread)
        last_segment = self.segments.pop(session, segment)
        self.compressions += max(0, segment - last_segment)
        self.segments[session] = segment
        while len(self.segments) > 128:
            self.segments.popitem(last=False)
        self.threads[key] = {
            "hash": plan["l1_hash"],
            "run": run,
            "stable_turns": stable,
            "segment": segment,
        }
        while len(self.threads) > 128:
            self.threads.popitem(last=False)
        self._usage(usage)

    def observe_summary(self, usage: dict | None):
        self.summary_calls += 1
        self._usage(usage)

    def _usage(self, usage: dict | None):
        self.calls += 1
        details = (usage or {}).get("input_token_details") or {}
        if details.get("cache_read") is not None:
            self.reported_input += usage["input_tokens"]
            self.read_tokens += details["cache_read"]
        self.write_tokens += cache_write_tokens(usage) or 0

    def stats(self) -> dict:
        return {
            "scope": "process_lifetime",
            "tracked_threads": len(self.threads),
            "model_calls": self.calls,
            "summary_calls": self.summary_calls,
            "cache_read_tokens": self.read_tokens,
            "cache_write_tokens": self.write_tokens,
            "cache_reported_input_tokens": self.reported_input,
            "prefix_hit_ratio": self.read_tokens / self.reported_input
            if self.reported_input
            else None,
            "prefix_stable_turns": max(
                (item["stable_turns"] for item in self.threads.values()), default=0
            ),
            "compression_events": self.compressions,
        }
