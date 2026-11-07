"""Current time and offset-aware timezone conversion. / 当前时间与带偏移的时区换算。"""

from datetime import UTC, datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.tools.contracts import ToolOperationError, ToolResult


def get_datetime(timezone="local", format="iso", datetime_iso=None):
    """
    Return the current time or convert an offset-aware timestamp. /
    返回当前时间或换算带偏移的时间戳。

    Args:
        timezone: Local zone or IANA name such as Asia/Shanghai. / 本地时区或 Asia/Shanghai 等
        IANA 名称。
        format: iso output or a strftime pattern. / iso 输出或 strftime 模式。
        datetime_iso: Optional offset-aware ISO timestamp. / 可选带时区偏移的 ISO 时间戳。

    Returns:
        Envelope with ISO, UTC, weekday and formatted values. / 含
        ISO、UTC、星期与格式化值的信封。

    Raises:
        ToolOperationError: If the timezone, ISO time or format is invalid. / 时区、ISO
        时间或格式无效时抛出。
    """
    try:
        zone = datetime.now().astimezone().tzinfo if timezone == "local" else ZoneInfo(timezone)
        current = datetime.fromisoformat(datetime_iso) if datetime_iso else datetime.now(UTC)
        if current.tzinfo is None:
            raise ValueError("A UTC offset is required")
        local = current.astimezone(zone)
        formatted = local.isoformat() if format == "iso" else local.strftime(format)
    except (ValueError, ZoneInfoNotFoundError) as error:
        raise ToolOperationError(
            "INVALID_ARGS",
            "Use an IANA timezone and offset-aware ISO time.",
            hint="Example: Asia/Shanghai; 2026-11-06T00:00:00+00:00.",
        ) from error
    return ToolResult(
        ok=True,
        summary=formatted,
        data={
            "iso": local.isoformat(),
            "local": local.isoformat(),
            "utc": current.astimezone(UTC).isoformat(),
            "weekday": local.strftime("%A"),
            "timezone": timezone,
            "formatted": formatted,
        },
    ).model_dump()
