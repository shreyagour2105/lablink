from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

from django.utils import timezone

HORIZON_DAYS = 7


def local_instant(value, zone, *, allow_ambiguous=False):
    """Return UTC for a local wall time, avoiding invalid DST mappings."""
    first = value.replace(tzinfo=zone, fold=0).astimezone(UTC)
    if first.astimezone(zone).replace(tzinfo=None) != value:
        raise ValueError("That local time does not exist due to a clock change.")
    second = value.replace(tzinfo=zone, fold=1).astimezone(UTC)
    if not allow_ambiguous and first != second:
        raise ValueError("That local time is ambiguous due to a clock change.")
    return first


def available_windows(resource, reserved=(), *, now=None):
    zone = ZoneInfo(resource["time_zone"])
    now = (now or timezone.now()).astimezone(UTC)
    local_date = now.astimezone(zone).date()
    horizon_end = datetime.combine(local_date + timedelta(days=HORIZON_DAYS), time.min, zone)
    yield from operating_intervals(resource, now, horizon_end, reserved)


def operating_intervals(resource, period_start, period_end, reserved=()):
    """Opening intervals after closures, clipped to an arbitrary reporting period."""
    if period_start >= period_end:
        return
    zone = ZoneInfo(resource["time_zone"])
    local_date = period_start.astimezone(zone).date()
    last_date = period_end.astimezone(zone).date()
    blocks = [(block["starts_at"], block["ends_at"]) for block in resource.get("blocks", [])]
    blocks.extend((item["starts_at"], item["ends_at"]) for item in reserved)
    blocks.sort()

    for offset in range((last_date - local_date).days + 1):
        date = local_date + timedelta(days=offset)
        day_ranges = []
        for window in sorted(resource["windows"], key=lambda item: item["opens_at"]):
            if window["weekday"] != date.weekday():
                continue
            opens = datetime.combine(date, time.fromisoformat(window["opens_at"]))
            closes = datetime.combine(date, time.fromisoformat(window["closes_at"]))
            try:
                start = max(period_start, local_instant(opens, zone, allow_ambiguous=True))
                stop = min(period_end, local_instant(closes, zone, allow_ambiguous=True))
            except ValueError:
                # A schedule boundary skipped by a clock change cannot be offered.
                continue
            if start >= stop:
                continue
            if day_ranges and start <= day_ranges[-1][1]:
                previous_start, previous_stop = day_ranges[-1]
                day_ranges[-1] = (previous_start, max(previous_stop, stop))
            else:
                day_ranges.append((start, stop))

        for start, stop in day_ranges:
            for block_start, block_end in blocks:
                if block_end <= start:
                    continue
                if block_start >= stop:
                    break
                if block_start > start:
                    yield start, block_start
                start = max(start, block_end)
                if start >= stop:
                    break
            if start < stop:
                yield start, stop


def interval_is_available(resource, reservations, start, end, *, now):
    return any(left <= start < end <= right for left, right in available_windows(resource, reservations, now=now))
