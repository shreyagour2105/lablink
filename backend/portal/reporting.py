from datetime import timedelta

from .availability import operating_intervals

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
    # Merge intersecting booking fragments so the same minute cannot be counted twice.
    fragments = sorted(
        (max(left, reservation["starts_at"]), min(right, reservation["ends_at"]))
        for reservation in reservations
        for left, right in windows
        if reservation["starts_at"] < right and left < reservation["ends_at"]
    )
    booked = 0.0
    previous_end = start
    for left, right in fragments:
        if right > previous_end:
            booked += (right - max(left, previous_end)).total_seconds()
            previous_end = right
    return {
        "available_hours": available / 3600,
        "booked_hours": booked / 3600,
        "percent": 100 * booked / available if available else None,
    }
