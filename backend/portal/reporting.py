from datetime import timedelta

from .availability import operating_intervals, subtract_intervals

REPORT_PERIODS = (7, 30)


def report_period(value, now):
    days = 30 if value == "30" else 7
    return {"days": days, "start": now - timedelta(days=days), "end": now}


def reservation_state(start, end, now):
    if now < start:
        return "Upcoming"
    if now < end:
        return "In progress"
    return "Ended"


def equipment_usage(resource, reservations, *, start, end):
    windows = list(operating_intervals(resource, start, end))
    available = sum((right - left).total_seconds() for left, right in windows)
    blocked = ((row["starts_at"], row["ends_at"]) for row in reservations)
    unbooked = sum((right - left).total_seconds() for left, right in subtract_intervals(windows, blocked))
    booked = available - unbooked
    return {
        "available_hours": available / 3600,
        "booked_hours": booked / 3600,
        "percent": 100 * booked / available if available else None,
    }
