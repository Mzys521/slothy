"""Account usage based on distinct, completed conversation runs. / 账户用量统计。"""

import json
import zlib
from binascii import crc32
from collections import Counter
from collections.abc import AsyncIterable
from datetime import UTC, date, datetime, timedelta
from struct import unpack

from pydantic import BaseModel, Field

from app.tools.contracts import ToolMetric


def validate_avatar(image: bytes) -> None:
    """Validate a square 8-bit PNG avatar up to 256 pixels. / 校验方形 PNG 头像。

    Args:
        image: Raw PNG bytes supplied by the client. / 客户端提供的原始 PNG 字节。

    Raises:
        ValueError: If the PNG is malformed or out of bounds. / PNG 损坏或超出限制时抛出。
    """
    if not image.startswith(b"\x89PNG\r\n\x1a\n"):
        raise ValueError("Upload a PNG avatar.")
    position = 8
    pixels = bytearray()
    header = None
    while position < len(image):
        if position + 12 > len(image):
            raise ValueError("The PNG is incomplete.")
        length = int.from_bytes(image[position : position + 4], "big")
        end = position + length + 12
        if end > len(image):
            raise ValueError("The PNG is incomplete.")
        chunk = image[position + 4 : position + 8]
        payload = image[position + 8 : end - 4]
        checksum = int.from_bytes(image[end - 4 : end], "big")
        if crc32(image[position + 4 : end - 4]) != checksum:
            raise ValueError("The PNG checksum is invalid.")
        if position == 8:
            if chunk != b"IHDR" or length != 13:
                raise ValueError("The PNG header is invalid.")
            header = unpack(">IIBBBBB", payload)
        elif chunk == b"IDAT":
            pixels.extend(payload)
        elif chunk == b"IEND":
            if length or end != len(image):
                raise ValueError("The PNG ending is invalid.")
            break
        position = end
    else:
        raise ValueError("The PNG ending is missing.")
    if header is None:
        raise ValueError("The PNG header is missing.")
    width, height, depth, color, compression, filtering, interlace = header
    channels = {0: 1, 2: 3, 4: 2, 6: 4}.get(color)
    if not (
        1 <= width == height <= 256
        and depth == 8
        and channels
        and compression == filtering == interlace == 0
    ):
        raise ValueError("Upload a square 8-bit PNG avatar up to 256 pixels.")
    stride = width * channels + 1
    expected = stride * height
    decoder = zlib.decompressobj()
    try:
        data = decoder.decompress(pixels, expected + 1)
    except zlib.error as error:
        raise ValueError("The PNG pixel data is invalid.") from error
    if (
        len(data) != expected
        or not decoder.eof
        or any(data[row] > 4 for row in range(0, expected, stride))
    ):
        raise ValueError("The PNG pixel data is invalid.")


class ActivityDay(BaseModel):
    """One calendar day of activity. / 单日活动数据。"""

    date: date
    turns: int
    tokens: int | None = None
    unreported: int = 0


class AccountActivity(BaseModel):
    """Aggregated account usage for the heatmap and stats. / 用于热力图与统计的账户用量汇总。"""

    as_of: date
    daily: list[ActivityDay]
    prior_tokens: int
    total_tokens: int | None
    peak_daily_tokens: int | None
    longest_model_seconds: float | None
    longest_streak: int
    current_streak: int
    turns: int
    chat_turns: int
    reasoning_effort: str | None
    reasoning_share: float | None
    distinct_tools: int
    tool_calls: int
    memory_tokens: int = 0
    unreported_calls: int = 0
    tools: list[ToolMetric] = Field(default_factory=list)
    tool_daily: list[dict] = Field(default_factory=list)


async def summarize_activity(
    runs: AsyncIterable[tuple], memory_runs: AsyncIterable[tuple] | None = None
) -> AccountActivity:
    """Aggregate completed runs and memory usage into activity. / 汇总轮次与记忆用量。

    Args:
        runs: Completed turns as (recorded_at, mode, metrics, activity) JSON. / 已完成轮次元组。
        memory_runs: Optional (recorded_at, tokens) rows. / 可选的记忆用量行。

    Returns:
        Daily series, streaks, tokens and tool counters. / 每日序列、连续天数、token 与工具计数。
    """
    today = datetime.now(UTC).date()
    # Keep complete calendar columns for the 53-week heatmap, starting on Monday.
    # 中文：按周一起始保留完整的 53 周日历列，用于热力图。
    start = today - timedelta(days=today.weekday() + 52 * 7)
    days: dict[date, ActivityDay] = {}
    efforts: Counter[str] = Counter()
    tools: Counter[str] = Counter()
    total_tokens = None
    longest_model_seconds = None
    turns = chat_turns = 0
    memory_tokens = unreported = 0
    tool_totals: dict[str, ToolMetric] = {}
    tool_days: dict[str, dict[str, ToolMetric]] = {}
    async for recorded_at, mode, metrics_json, activity_json in runs:
        turns += 1
        chat_turns += mode == "chat"
        metrics = json.loads(metrics_json) if metrics_json else {}
        tokens = metrics.get("total_tokens") if metrics.get("usage_reported", True) else None
        if tokens is not None:
            total_tokens = (total_tokens or 0) + tokens
        else:
            unreported += 1
        if (
            seconds := (
                metrics.get("model_seconds") if metrics.get("usage_reported", True) else None
            )
        ) is not None:
            longest_model_seconds = max(longest_model_seconds or 0, seconds)
        if effort := metrics.get("reasoning_effort"):
            efforts[effort] += 1
        records = metrics.get("tools") or None
        if records is not None:
            for record in records:
                item = ToolMetric.model_validate(record)
                if item.calls > item.rejected:
                    tools[item.name] += item.calls - item.rejected
                total = tool_totals.setdefault(item.name, ToolMetric(name=item.name))
                daily = tool_days.setdefault(recorded_at[:10], {})
                day_total = daily.setdefault(item.name, ToolMetric(name=item.name))
                for field in ToolMetric.model_fields:
                    if field != "name":
                        setattr(total, field, getattr(total, field) + getattr(item, field))
                        setattr(day_total, field, getattr(day_total, field) + getattr(item, field))
        for item in json.loads(activity_json) if records is None else []:
            if item.get("kind") == "tool" and item.get("status") in ("done", "error"):
                if name := item.get("name"):
                    tools[name] += 1
        if recorded_at:
            day = date.fromisoformat(recorded_at[:10])
            current = days.setdefault(day, ActivityDay(date=day, turns=0))
            current.turns += 1
            if tokens is not None:
                current.tokens = (current.tokens or 0) + tokens
            else:
                current.unreported += 1
    # Memory-usage rows share the same daily buckets. / 记忆用量行共用同一批每日桶。
    if memory_runs is not None:
        async for recorded_at, tokens in memory_runs:
            current = days.setdefault(
                date.fromisoformat(recorded_at[:10]),
                ActivityDay(date=date.fromisoformat(recorded_at[:10]), turns=0),
            )
            if tokens is None:
                current.unreported += 1
                unreported += 1
            else:
                memory_tokens += tokens
                total_tokens = (total_tokens or 0) + tokens
                current.tokens = (current.tokens or 0) + tokens
    longest = streak = 0
    previous = None
    for day in sorted(day for day in days if days[day].turns):
        streak = streak + 1 if previous and day - previous == timedelta(days=1) else 1
        longest = max(longest, streak)
        previous = day
    most_used = efforts.most_common(1)
    return AccountActivity(
        as_of=today,
        daily=[days[day] for day in sorted(days) if start <= day <= today],
        prior_tokens=sum(entry.tokens or 0 for day, entry in days.items() if day < start),
        total_tokens=total_tokens,
        peak_daily_tokens=max(
            (entry.tokens for entry in days.values() if entry.tokens is not None), default=None
        ),
        longest_model_seconds=longest_model_seconds,
        longest_streak=longest,
        current_streak=streak if previous in (today, today - timedelta(days=1)) else 0,
        turns=turns,
        chat_turns=chat_turns,
        reasoning_effort=most_used[0][0] if most_used else None,
        reasoning_share=most_used[0][1] / sum(efforts.values()) if most_used else None,
        distinct_tools=len(tools),
        tool_calls=tools.total(),
        memory_tokens=memory_tokens,
        unreported_calls=unreported,
        tools=[tool_totals[name] for name in sorted(tool_totals)],
        tool_daily=[
            {
                "date": day,
                "tools": [tool_days[day][name].model_dump() for name in sorted(tool_days[day])],
            }
            for day in sorted(tool_days)
        ],
    )
