from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TypedDict
from zoneinfo import ZoneInfo

from .availability import HORIZON_DAYS, available_windows, interval_is_available

STEP = timedelta(minutes=15)
AGING_THRESHOLD = timedelta(hours=48)
USAGE_PERIOD = timedelta(days=7)
LOOKAHEAD_REQUESTS = 8
LOOKAHEAD_SLOTS = 24


@dataclass(frozen=True)
class Slot:
    resource_id: str
    starts_at: datetime
    ends_at: datetime


class ScoreFactors(TypedDict):
    urgency: int
    scarcity: int
    fit: int
    fairness: int


@dataclass(frozen=True)
class Recommendation:
    request_id: str
    slots: tuple[Slot, ...]
    feasible_slots: tuple[Slot, ...]
    factors: ScoreFactors | None
    prior_minutes: int
    packing_gain: int
    blocking_requests: tuple[str, ...]

    @property
    def score(self) -> int | None:
        return sum(self.factors.values()) if self.factors is not None else None


@dataclass(frozen=True)
class _RequestPlan:
    request_id: str
    student_uid: str
    deadline: datetime
    submitted_at: datetime
    feasible_slots: tuple[Slot, ...]
    preferred_slot: Slot | None
    factors: ScoreFactors | None
    prior_minutes: int

    def priority(self, now: datetime) -> tuple[bool, bool, datetime, int, datetime, datetime, str]:
        aged = self.submitted_at <= now - AGING_THRESHOLD
        return (
            self.factors is None,
            not aged,
            self.submitted_at if aged else now,
            -sum(self.factors.values()) if self.factors is not None else 0,
            self.deadline,
            self.submitted_at,
            self.request_id,
        )


@dataclass(frozen=True)
class _Allocation:
    request_id: str
    student_uid: str
    slot: Slot


def normalize_capabilities(values):
    return frozenset(" ".join(value.split()).casefold() for value in values if value.strip())


def equipment_capabilities(resource):
    return normalize_capabilities(resource.get("capabilities", [resource["capability"]]))


def eligible(resource, student):
    if not resource.get("active") or not student or student.get("role") != "student":
        return False
    if resource.get("access", "open") == "open":
        return True
    return resource.get("access") == "restricted" and resource["lab_id"] in student.get(
        "eligible_lab_ids", []
    )


def duration(request):
    minutes = request["duration_minutes"]
    if type(minutes) is not int or minutes % 15 or not 15 <= minutes <= 480:
        return None
    return timedelta(minutes=minutes)


def matches(request, resource, student):
    return (
        resource["lab_id"] == request["lab_id"]
        and eligible(resource, student)
        and normalize_capabilities(request.get("required_capabilities", []))
        <= equipment_capabilities(resource)
    )


def allows_alternatives(request):
    return request.get("earliest_start") is not None and request.get("latest_end") is not None


def respects_request(request, slot, now):
    if slot.starts_at <= now or slot.ends_at > request["deadline"]:
        return False
    if not allows_alternatives(request):
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
    return respects_request(request, slot, now) and interval_is_available(
        resource, [*reservations, *student_reservations], start, slot.ends_at, now=now
    )


def approved_intervals(history):
    return [
        {
            "starts_at": item.get("reserved_start", item["starts_at"]),
            "ends_at": item.get("reserved_end", item["ends_at"]),
        }
        for item in history
        if item["status"] == "approved" and item.get("reservation_id")
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
        if not allows_alternatives(request):
            start = request["starts_at"]
            if slot_is_feasible(
                request,
                resource,
                student,
                start,
                reservations.get(resource_id, []),
                student_reservations,
                now,
            ):
                options.append(Slot(resource_id, start, start + length))
            continue
        for left, right in available_windows(resource, blocked, now=now):
            start = max(left, request["earliest_start"], now + timedelta(microseconds=1))
            local = start.astimezone(ZoneInfo(resource["time_zone"]))
            remainder = timedelta(
                minutes=local.minute % 15, seconds=local.second, microseconds=local.microsecond
            )
            if remainder:
                start += STEP - remainder
            last_end = min(right, request["deadline"], request["latest_end"])
            while last_end - start >= length:
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


def score_components(request, slots: Sequence[Slot], prior_usage: int, now: datetime) -> ScoreFactors:
    remaining = (request["deadline"] - now) // timedelta(seconds=1)
    urgency = max(0, 40 - 40 * remaining // (HORIZON_DAYS * 86400))
    scarcity = max(0, 26 - len(slots))
    fit = 20 if slots[0].resource_id == request["resource_id"] else 10
    fairness = max(0, 15 - prior_usage // 30)
    return {"urgency": urgency, "scarcity": scarcity, "fit": fit, "fairness": fairness}


def score(request, slots, prior_usage, now):
    return sum(score_components(request, slots, prior_usage, now).values())


def overlaps(first: Slot, second: Slot) -> bool:
    return first.starts_at < second.ends_at and second.starts_at < first.ends_at


def conflicts_with(slot: Slot, student_uid: str, allocation: Slot, other_uid: str) -> bool:
    return (student_uid == other_uid or slot.resource_id == allocation.resource_id) and overlaps(
        slot, allocation
    )


def preferred_conflicts(request, peers):
    """Find pending requests with overlapping preferred intervals."""
    length = duration(request)
    if not length:
        return []
    start = request["starts_at"]
    return [
        other
        for other in peers
        if other["id"] != request["id"]
        and (other["resource_id"] == request["resource_id"] or other["student_uid"] == request["student_uid"])
        and (other_length := duration(other))
        and other["starts_at"] < start + length
        and start < other["starts_at"] + other_length
    ]


def keep_later_options(
    options: tuple[Slot, ...], student_uid: str, later: Sequence[tuple[str, tuple[Slot, ...]]]
) -> tuple[tuple[Slot, ...], int]:
    """Within the preferred shortlist, choose the slot that preserves the most later requests."""
    if len(options) < 2 or not later:
        return options, 0

    def preserved(candidate):
        return sum(
            any(not conflicts_with(candidate, student_uid, option, other_uid) for option in peer_options)
            for other_uid, peer_options in later
        )

    shortlist = options[:LOOKAHEAD_SLOTS]
    counts = [preserved(slot) for slot in shortlist]
    best = max(range(len(counts)), key=counts.__getitem__)
    if best == 0:
        return options, 0
    return (options[best], *options[:best], *options[best + 1 :]), counts[best] - counts[0]


def _rank_requests(
    requests, resources, students, reservations, histories, now: datetime
) -> list[_RequestPlan]:
    usage = {uid: prior_minutes(history, now) for uid, history in histories.items()}
    personal = {uid: approved_intervals(history) for uid, history in histories.items()}
    ranked = []
    for request in requests:
        uid = request["student_uid"]
        feasible = tuple(
            candidates(
                request,
                resources,
                students.get(uid),
                reservations,
                personal.get(uid, []),
                now,
            )
        )
        prior = usage.get(uid, 0)
        preferred = next(
            (
                slot
                for slot in feasible
                if slot.resource_id == request["resource_id"] and slot.starts_at == request["starts_at"]
            ),
            None,
        )
        ranked.append(
            _RequestPlan(
                request_id=request["id"],
                student_uid=uid,
                deadline=request["deadline"],
                submitted_at=request["submitted_at"],
                feasible_slots=feasible,
                preferred_slot=preferred,
                factors=score_components(request, feasible, prior, now) if feasible else None,
                prior_minutes=prior,
            )
        )
    return sorted(ranked, key=lambda plan: plan.priority(now))


def _remaining_slots(plan: _RequestPlan, allocations: Sequence[_Allocation]) -> tuple[Slot, ...]:
    return tuple(
        slot
        for slot in plan.feasible_slots
        if not any(
            conflicts_with(slot, plan.student_uid, entry.slot, entry.student_uid) for entry in allocations
        )
    )


def _allocate(ranked: list[_RequestPlan], options_limit: int) -> list[Recommendation]:
    allocations: list[_Allocation] = []
    recommendations = []
    for index, plan in enumerate(ranked):
        options = _remaining_slots(plan, allocations)
        gain = 0
        if len(options) > 1:
            later = [
                (peer.student_uid, feasible)
                for peer in ranked[index + 1 : index + 1 + LOOKAHEAD_REQUESTS]
                if (feasible := _remaining_slots(peer, allocations))
            ]
            options, gain = keep_later_options(options, plan.student_uid, later)
        blocking = tuple(
            entry.request_id
            for entry in allocations
            if plan.preferred_slot
            and conflicts_with(plan.preferred_slot, plan.student_uid, entry.slot, entry.student_uid)
        )
        visible = options[:options_limit]
        if visible:
            allocations.append(_Allocation(plan.request_id, plan.student_uid, visible[0]))
        recommendations.append(
            Recommendation(
                request_id=plan.request_id,
                slots=visible,
                feasible_slots=plan.feasible_slots,
                factors=plan.factors,
                prior_minutes=plan.prior_minutes,
                packing_gain=gain,
                blocking_requests=blocking,
            )
        )
    return recommendations


def recommend(
    requests, resources, students, reservations, histories, *, now: datetime, options_limit: int = 3
) -> list[Recommendation]:
    """Assess feasibility and priority once, then propose non-overlapping primary slots."""
    ranked = _rank_requests(requests, resources, students, reservations, histories, now)
    return _allocate(ranked, options_limit)


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
    if not normalize_capabilities(request.get("required_capabilities", [])) <= equipment_capabilities(
        resource
    ):
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
