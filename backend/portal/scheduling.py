from dataclasses import dataclass
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .availability import HORIZON_DAYS, available_windows, interval_is_available

STEP = timedelta(minutes=15)
AGING_THRESHOLD = timedelta(hours=48)
USAGE_PERIOD = timedelta(days=7)


@dataclass(frozen=True)
class Slot:
    resource_id: str
    starts_at: datetime
    ends_at: datetime


@dataclass(frozen=True)
class Recommendation:
    request_id: str
    slots: tuple[Slot, ...]
    score: int | None = None


def normalize_capabilities(values):
    return frozenset(" ".join(value.split()).casefold() for value in values if value.strip())


def equipment_capabilities(resource):
    return normalize_capabilities(resource.get("capabilities", [resource["capability"]]))


def eligible(resource, student):
    if not resource.get("active") or not student or student.get("role") != "student":
        return False
    if resource.get("access", "open") == "open":
        return True
    return resource.get("access") == "restricted" and resource["lab_id"] in student.get("eligible_lab_ids", [])


def duration(request):
    minutes = request["duration_minutes"]
    if type(minutes) is not int or minutes % 15 or not 15 <= minutes <= 480:
        return None
    return timedelta(minutes=minutes)


def matches(request, resource, student):
    return (
        resource["lab_id"] == request["lab_id"]
        and eligible(resource, student)
        and normalize_capabilities(request.get("required_capabilities", [])) <= equipment_capabilities(resource)
    )


def respects_request(request, slot, now):
    if slot.starts_at <= now or slot.ends_at > request["deadline"]:
        return False
    if request.get("earliest_start") is None or request.get("latest_end") is None:
        return slot.resource_id == request["resource_id"] and slot.starts_at == request["starts_at"]
    return request["earliest_start"] <= slot.starts_at and slot.ends_at <= request["latest_end"]


def respects_grid(start, resource):
    local = start.astimezone(ZoneInfo(resource["time_zone"]))
    return not (local.minute % 15 or local.second or local.microsecond)


def slot_is_feasible(request, resource, student, start, reservations, student_reservations, now):
    length = duration(request)
    if not length or start <= now or request["deadline"] - start < length:
        return False
    if not matches(request, resource, student) or not respects_grid(start, resource):
        return False
    slot = Slot(resource["id"], start, start + length)
    return (
        respects_request(request, slot, now)
        and interval_is_available(resource, [*reservations, *student_reservations], start, slot.ends_at, now=now)
    )


def approved_intervals(history):
    return [
        {
            "starts_at": item.get("reserved_start", item["starts_at"]),
            "ends_at": item.get("reserved_end", item["ends_at"]),
        }
        for item in history if item["status"] == "approved" and item.get("reservation_id")
    ]


def prior_minutes(history, now):
    seconds = sum(
        max(0, (min(item["ends_at"], now) - max(item["starts_at"], now - USAGE_PERIOD)).total_seconds())
        for item in approved_intervals(history)
    )
    return int(seconds // 60)


def candidates(request, resources, student, reservations, student_reservations, now):
    length = duration(request)
    if not length or request["deadline"] <= now:
        return []
    options = []
    for resource in resources:
        if not matches(request, resource, student):
            continue
        resource_id = resource["id"]
        blocked = [*reservations.get(resource_id, []), *student_reservations]
        if not request.get("earliest_start") or not request.get("latest_end"):
            start = request["starts_at"]
            if slot_is_feasible(request, resource, student, start, reservations.get(resource_id, []), student_reservations, now):
                options.append(Slot(resource_id, start, start + length))
            continue
        for left, right in available_windows(resource, blocked, now=now):
            start = max(left, request["earliest_start"], now + timedelta(microseconds=1))
            local = start.astimezone(ZoneInfo(resource["time_zone"]))
            remainder = timedelta(minutes=local.minute % 15, seconds=local.second, microseconds=local.microsecond)
            if remainder:
                start += STEP - remainder
            last_end = min(right, request["deadline"], request["latest_end"])
            while start + length <= last_end:
                if respects_grid(start, resource):
                    options.append(Slot(resource_id, start, start + length))
                start += STEP
    return sorted(options, key=lambda slot: option_key(request, slot))


def option_key(request, slot):
    return (
        slot.resource_id != request["resource_id"],
        abs(slot.starts_at - request["starts_at"]),
        slot.starts_at,
        slot.resource_id,
    )


def score(request, slots, prior_usage, now):
    remaining = (request["deadline"] - now) // timedelta(seconds=1)
    urgency = max(0, 40 - 40 * remaining // (HORIZON_DAYS * 86400))
    scarcity = max(0, 26 - len(slots))
    fit = 20 if slots[0].resource_id == request["resource_id"] else 10
    fairness = max(0, 15 - prior_usage // 30)
    return urgency + scarcity + fit + fairness


def overlaps(first, second):
    return first.starts_at < second.ends_at and second.starts_at < first.ends_at


def recommend(requests, resources, students, reservations, histories, *, now, options_limit=3):
    """Rank once using the persisted snapshot, then propose non-overlapping primary slots."""
    usage = {uid: prior_minutes(history, now) for uid, history in histories.items()}
    personal = {uid: approved_intervals(history) for uid, history in histories.items()}
    ranked = []
    for request in requests:
        uid = request["student_uid"]
        slots = candidates(request, resources, students.get(uid), reservations, personal.get(uid, []), now)
        points = score(request, slots, usage.get(uid, 0), now) if slots else None
        aged = request["submitted_at"] <= now - AGING_THRESHOLD
        priority = (
            points is None,
            not aged,
            request["submitted_at"] if aged else now,
            -(points or 0),
            request["deadline"],
            request["submitted_at"],
            request["id"],
        )
        ranked.append((priority, request, slots, points))

    allocations = []
    recommendations = []
    for _, request, slots, points in sorted(ranked, key=lambda item: item[0]):
        uid = request["student_uid"]
        options = tuple(
            slot for slot in slots
            if not any(
                (uid == other_uid or slot.resource_id == primary.resource_id) and overlaps(slot, primary)
                for other_uid, primary in allocations
            )
        )[:options_limit]
        if options:
            allocations.append((uid, options[0]))
        recommendations.append(Recommendation(request["id"], options, points))
    return recommendations


def requested_slot_conflicts(request, resource, student, reservations, student_reservations, *, now):
    """Explain why the original request cannot use its preferred interval."""
    length = duration(request)
    if not length:
        return ["The required duration is outside the supported 15-minute increments."]
    start = request["starts_at"]
    end = start + length
    reasons = []
    if request["deadline"] <= now:
        reasons.append("The project deadline has passed.")
    elif end > request["deadline"]:
        reasons.append("The requested interval ends after the project deadline.")
    if start <= now:
        reasons.append("The requested start has passed.")
    if not resource or not resource.get("active") or resource["lab_id"] != request["lab_id"]:
        return [*reasons, "The requested equipment is unavailable in this lab."]

    if not eligible(resource, student):
        reasons.append("The student does not currently have access to this equipment.")
    if not normalize_capabilities(request.get("required_capabilities", [])) <= equipment_capabilities(resource):
        reasons.append("The requested equipment no longer meets all required capabilities.")
    if not respects_grid(start, resource):
        reasons.append("The start does not align with a 15-minute boundary.")
    if start > now and not interval_is_available({**resource, "blocks": []}, [], start, end, now=now):
        reasons.append("The interval is outside operating hours or the seven-day booking window.")

    def intersects(intervals):
        return any(block["starts_at"] < end and start < block["ends_at"] for block in intervals)

    if intersects(resource.get("blocks", [])):
        reasons.append("A maintenance or closure block overlaps the requested interval.")
    if intersects(reservations):
        reasons.append("Another confirmed reservation overlaps on this equipment.")
    if intersects(student_reservations):
        reasons.append("The student has a confirmed reservation during this interval.")
    return reasons
