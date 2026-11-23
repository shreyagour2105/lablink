from collections import defaultdict
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

HORIZON_DAYS = 7
Interval = tuple[datetime, datetime]


def local_instant(value, zone, *, allow_ambiguous=False):
    """Return UTC for a local wall time, avoiding invalid DST mappings."""
    first = value.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if first.astimezone(zone).replace(tzinfo=None) != value:
        raise ValueError("That local time does not exist due to a clock change.")
    second = value.replace(tzinfo=zone, fold=1).astimezone(UTC)
    if not allow_ambiguous and first != second:
        raise ValueError("That local time is ambiguous due to a clock change.")
    return first


def _opening_intervals(resource, start: datetime, end: datetime) -> Iterator[Interval]:
    zone = ZoneInfo(resource["time_zone"])
    first_day = start.astimezone(zone).date()
    last_day = end.astimezone(zone).date()
    by_weekday = defaultdict(list)
    for window in sorted(resource["windows"], key=lambda item: item["opens_at"]):
        by_weekday[window["weekday"]].append(window)

    for offset in range((last_day - first_day).days + 1):
        day = first_day + timedelta(days=offset)
        merged: Interval | None = None
        for window in by_weekday[day.weekday()]:
            opens = datetime.combine(day, time.fromisoformat(window["opens_at"]))
            closes = datetime.combine(day, time.fromisoformat(window["closes_at"]))
            try:
                left = max(start, local_instant(opens, zone, allow_ambiguous=True))
                right = min(end, local_instant(closes, zone, allow_ambiguous=True))
            except ValueError:
                # A schedule boundary skipped by a clock change cannot be offered.
                continue
            if left >= right:
                continue
            if merged is not None and left <= merged[1]:
                merged = (merged[0], max(merged[1], right))
            else:
                if merged is not None:
                    yield merged
                merged = (left, right)
        if merged is not None:
            yield merged


def subtract_intervals(windows: Iterable[Interval], blocked: Iterable[Interval]) -> Iterator[Interval]:
    """Subtract occupied time from ordered, non-overlapping half-open windows."""
    blocks = iter(sorted((left, right) for left, right in blocked if left < right))
    block = next(blocks, None)
    for start, end in windows:
        while block is not None and block[0] < end:
            block_start, block_end = block
            if block_end > start:
                if block_start > start:
                    yield start, block_start
                start = block_end
                if start >= end:
                    # This block can continue into another opening window.
                    break
            block = next(blocks, None)
        if start < end:
            yield start, end


def operating_intervals(
    resource, period_start: datetime, period_end: datetime, reserved=()
) -> Iterator[Interval]:
    """Opening intervals after closures and bookings, clipped to a reporting period."""
    if period_start >= period_end:
        return
    closures = resource.get("blocks", [])
    blocked = ((row["starts_at"], row["ends_at"]) for row in (*closures, *reserved))
    yield from subtract_intervals(_opening_intervals(resource, period_start, period_end), blocked)


def available_windows(resource, reserved=(), *, now=None) -> Iterator[Interval]:
    zone = ZoneInfo(resource["time_zone"])
    now = (now or timezone.now()).astimezone(UTC)
    local_date = now.astimezone(zone).date()
    horizon_end = datetime.combine(local_date + timedelta(days=HORIZON_DAYS), time.min, zone)
    yield from operating_intervals(resource, now, horizon_end, reserved)


def interval_is_available(resource, reservations, start, end, *, now) -> bool:
    return any(
        left <= start < end <= right for left, right in available_windows(resource, reservations, now=now)
    )
