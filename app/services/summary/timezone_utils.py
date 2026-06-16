from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import CALENDAR_DEFAULT_TIMEZONE, SUMMARY_PERIOD_MINUTES


def get_tz() -> ZoneInfo:
    return ZoneInfo(CALENDAR_DEFAULT_TIMEZONE)


def utc_now() -> datetime:
    return datetime.utcnow()


def local_today() -> date:
    return utc_now().replace(tzinfo=ZoneInfo("UTC")).astimezone(get_tz()).date()


def naive_utc_to_local(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt.astimezone(get_tz())


def local_hour_start_to_utc_naive(local_day: date, hour: int) -> datetime:
    local_dt = datetime(local_day.year, local_day.month, local_day.day, hour, 0, 0, tzinfo=get_tz())
    return local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def hour_start_utc_naive(dt: datetime) -> datetime:
    local = naive_utc_to_local(dt)
    period_minutes = SUMMARY_PERIOD_MINUTES
    minute = (local.minute // period_minutes) * period_minutes
    local_dt = datetime(
        local.year, local.month, local.day, local.hour, minute, 0, tzinfo=get_tz()
    )
    return local_dt.astimezone(ZoneInfo("UTC")).replace(tzinfo=None)


def next_hour_start_utc_naive(dt: datetime) -> datetime:
    return hour_start_utc_naive(dt) + timedelta(minutes=SUMMARY_PERIOD_MINUTES)


def day_start_utc_naive(local_day: date) -> datetime:
    return local_hour_start_to_utc_naive(local_day, 0)


def next_day_start_utc_naive(local_day: date) -> datetime:
    return day_start_utc_naive(local_day + timedelta(days=1))


def format_local_range(period_start: datetime, period_end: datetime) -> str:
    start = naive_utc_to_local(period_start)
    end = naive_utc_to_local(period_end)
    return f"{start.strftime('%Y-%m-%d %H:%M')}–{end.strftime('%H:%M')}"
