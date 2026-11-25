"""Select DeepSeek pricing by Beijing time and the official China calendar."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

from chinese_calendar import is_holiday


BEIJING_TZ = timezone(timedelta(hours=8), name="Asia/Shanghai")
PEAK_WINDOWS = (
    (time(9, 0), time(12, 0)),
    (time(14, 0), time(18, 0)),
)


def is_statutory_holiday(day: date) -> bool:
    """Return whether the official Chinese calendar marks a weekday as a day off.

    ``chinesecalendar`` follows the State Council's annual holiday and make-up
    workday schedule. When its data range ends, fall back to the weekday rule
    instead of breaking every hook invocation.
    """

    try:
        return bool(is_holiday(day))
    except NotImplementedError:
        return False


def is_peak_time(when: datetime | None = None) -> bool:
    """Return True inside Beijing weekday peak windows, excluding holidays."""

    local = (when or datetime.now(BEIJING_TZ)).astimezone(BEIJING_TZ)
    # Weekends remain off-peak even when the government schedules make-up work.
    if local.weekday() >= 5:
        return False
    if is_statutory_holiday(local.date()):
        return False
    current = local.time().replace(tzinfo=None)
    return any(start <= current < end for start, end in PEAK_WINDOWS)


def pricing_path_for(
    config_dir: Path,
    when: datetime | None = None,
) -> Path:
    """Return the peak or off-peak pricing file for a Beijing timestamp."""

    filename = "pricing.peak.json" if is_peak_time(when) else "pricing.offpeak.json"
    return config_dir / filename
