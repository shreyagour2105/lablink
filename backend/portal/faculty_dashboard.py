from itertools import islice

from .availability import available_windows
from .reporting import equipment_usage, report_period, reservation_state
from .scheduling import (
    Recommendation,
    Slot,
    approved_intervals,
    preferred_conflicts,
    recommend,
    requested_slot_conflicts,
)


def faculty_snapshot(store, lab_ids, period_value, now):
    period = report_period(period_value, now)
    resources = sorted(
        store.lab_records("equipment", lab_ids),
        key=lambda unit: (unit["lab_name"], unit["name"], unit["id"]),
    )
    by_id = {unit["id"]: unit for unit in resources}
    requests = store.lab_records("booking_requests", lab_ids)
    requests_by_id = {item["id"]: item for item in requests}
    pending = [item for item in requests if item["status"] == "pending"]
    decisions = sorted(
        (item for item in requests if item["status"] in {"approved", "rejected", "reschedule requested"}),
        key=lambda item: (item["decided_at"], item["id"]),
        reverse=True,
    )

    reservations = {}
    usage = []
    states = []
    schedule = []
    for unit in resources:
        confirmed = store.upcoming_reservations(unit["id"], now=period["start"])
        reservations[unit["id"]] = confirmed
        if unit["active"]:
            windows = list(islice(available_windows(unit, confirmed, now=now), 3))
            schedule.append({"unit": unit, "windows": windows})
        usage.append(
            {
                "unit": unit,
                **equipment_usage(unit, confirmed, start=period["start"], end=now),
            }
        )
        for row in confirmed:
            item = requests_by_id.get(row["request_id"])
            if not item or item["status"] != "approved" or item.get("reservation_id") != row["id"]:
                continue
            states.append(
                {
                    **row,
                    "unit": unit,
                    "project": item["project_title"],
                    "state": reservation_state(row["starts_at"], row["ends_at"], now),
                }
            )

    queue = prepare_queue(store, pending, by_id, reservations, now)
    state_priority = {"In progress": 0, "Upcoming": 1, "Ended": 2}
    states.sort(key=lambda row: (state_priority[row["state"]], row["starts_at"], row["id"]))
    return {
        "period": period,
        "queue": queue,
        "decisions": decisions,
        "usage": usage,
        "schedule": schedule,
        "reservations": states,
        "labs": store.labs(lab_ids),
    }


def prepare_queue(store, pending, resources, reservations, now):
    uids = sorted({item["student_uid"] for item in pending})
    students = {uid: store.account(uid) for uid in uids}
    histories = {uid: store.history(uid) for uid in uids}
    by_id = {item["id"]: item for item in pending}
    ranked = recommend(pending, list(resources.values()), students, reservations, histories, now=now)
    return [
        {
            **describe_recommendation(
                result, by_id[result.request_id], resources, reservations, students, histories, now
            ),
            "rank": rank,
            "competing": [
                item["project_title"] for item in preferred_conflicts(by_id[result.request_id], pending)
            ],
        }
        for rank, result in enumerate(ranked, 1)
    ]


def describe_recommendation(result: Recommendation, item, resources, reservations, students, histories, now):
    uid = item["student_uid"]
    preferred = item["resource_id"]
    reasons = requested_slot_conflicts(
        item,
        resources.get(preferred),
        students.get(uid),
        reservations.get(preferred, []),
        approved_intervals(histories[uid]),
        now=now,
    )

    def describe_slot(slot: Slot):
        resource = resources[slot.resource_id]
        return {
            "value": f"{slot.resource_id}|{slot.starts_at.isoformat()}",
            "resource_id": slot.resource_id,
            "name": resource["name"],
            "starts_at": slot.starts_at,
            "ends_at": slot.ends_at,
            "time_zone": resource["time_zone"],
            "alternative": slot.resource_id != preferred or slot.starts_at != item["starts_at"],
        }

    options = [describe_slot(slot) for slot in result.slots]
    alternatives = (slot for slot in result.feasible_slots if slot not in result.slots)
    overrides = [describe_slot(slot) for slot in islice(alternatives, 12)]
    if result.blocking_requests:
        reasons.append("A higher-priority recommendation uses the requested interval in this plan.")
    return {
        "item": item,
        "score": result.score,
        "options": options,
        "overrides": overrides,
        "conflicts": reasons,
        "packing_gain": result.packing_gain,
        "prior_minutes": result.prior_minutes,
        "factors": result.factors,
    }
