"""Times are stored in UTC and shown in one chosen time zone (default Pakistan time)."""
from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone, tzinfo

TIMEZONE_NAME = os.getenv("APP_TIMEZONE", "Asia/Karachi").strip() or "Asia/Karachi"


def _zone() -> tzinfo:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(TIMEZONE_NAME)
    except Exception:  # no tz database installed: Pakistan has no daylight saving, so UTC+5 is exact
        return timezone(timedelta(hours=5), "PKT")


ZONE = _zone()


def now_local() -> datetime:
    return datetime.now(ZONE)


def to_local(dt: datetime) -> datetime:
    """A naive datetime from the database is UTC; convert it."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZONE)


def fmt(dt: datetime) -> str:
    return to_local(dt).strftime("%Y-%m-%d %H:%M")


def today_key() -> str:
    """The quota day: midnight to midnight in the chosen time zone."""
    return now_local().strftime("%Y-%m-%d")


def reset_text() -> str:
    return f"00:00 ({ZONE.tzname(now_local()) or TIMEZONE_NAME})"
