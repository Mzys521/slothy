"""Bounded counters that never retain prompt bodies or profile text.
/ 有界计数器，不保留提示正文或画像文本。
"""

from collections import OrderedDict


def cache_write_tokens(usage: dict | None) -> int | None:
    """Extract cache-write tokens from provider usage metadata.
    / 从提供商用量元数据中提取缓存写入 Token 数。

    Args:
        usage: Provider usage mapping, or None when unavailable.
            / 提供商用量字典；不可用时为 None。

    Returns:
        Cache-write tokens, or None when unreported.
        / 缓存写入 Token 数；未上报时为 None。
    """
    details = (usage or {}).get("input_token_details") or {}
    specific = [
        details[key]
        for key in ("ephemeral_5m_input_tokens", "ephemeral_1h_input_tokens")
        if details.get(key) is not None
    ]
    return sum(specific) if specific else details.get("cache_creation")


class PromptTelemetry:
    """Process-lifetime counters without storing prompt content.
    / 不存储提示内容的进程级提示计数器。
    """

    def __init__(self):
        """Initialize empty thread, segment and token counters.
        / 初始化空的线程、分段与 Token 计数器。
        """
        self.threads = OrderedDict()
        self.segments = OrderedDict()
        self.calls = self.reported_input = self.read_tokens = self.write_tokens = 0
        self.compressions = 0
        self.summary_calls = 0

    def observe(
        self, user: str, thread: str, run: str, namespace: str, plan: dict, usage: dict | None
    ):
        """Record one planned model call and its cache usage.
        / 记录一次已规划的模型调用及其缓存用量。

        Args:
            user: User identifier. / 用户标识。
            thread: Thread identifier. / 会话标识。
            run: Run identifier. / 运行标识。
            namespace: Prompt namespace such as chat or memory.
                / 提示命名空间，例如 chat 或 memory。
            plan: Prompt plan metadata from compose(). / compose() 生成的提示计划元数据。
            usage: Provider usage mapping, or None. / 提供商用量字典，可为 None。
        """
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
        """Record one summary model call and its cache usage. / 记录一次摘要模型调用及其缓存用量。

        Args:
            usage: Provider usage mapping, or None. / 提供商用量字典，可为 None。
        """
        self.summary_calls += 1
        self._usage(usage)

    def _usage(self, usage: dict | None):
        """Accumulate cache read/write and reported-input counters.
        / 累加缓存读写与已上报输入计数器。

        Args:
            usage: Provider usage mapping, or None. / 提供商用量字典，可为 None。
        """
        self.calls += 1
        details = (usage or {}).get("input_token_details") or {}
        if details.get("cache_read") is not None:
            self.reported_input += usage["input_tokens"]
            self.read_tokens += details["cache_read"]
        self.write_tokens += cache_write_tokens(usage) or 0

    def stats(self) -> dict:
        """Return a snapshot of process-lifetime prompt counters. / 返回进程级提示计数器的快照。

        Returns:
            Mapping of counters including cache hit ratio and compression events.
            / 含缓存命中率与压缩事件等计数器的字典。
        """
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
