"""Adapt the fictional demo clock and records to the production scheduler."""

from datetime import UTC, datetime, timedelta

from .availability import available_windows
from .reporting import equipment_usage
from .scheduling import (
    approved_intervals, candidates, prior_minutes, recommend, requested_slot_conflicts, score_components, slot_is_feasible,
)

NOW = datetime(2030, 1, 7, 8, tzinfo=UTC)
STUDENT = {"role": "student"}
UNITS = {
    "printer": ("FDM 3D Printer", "Mechanical Engineering", "Prototype fabrication", "220 × 220 mm print bed"),
    "scope": ("Digital Oscilloscope", "Electrical Engineering", "Signal analysis", "100 MHz · 4 channels"),
    "station": ("GPU Workstation", "Computer Science", "GPU computing", "16 GB GPU memory"),
}


def at(minutes):
    return NOW.replace(hour=0) + timedelta(minutes=minutes)


def interval(start, end):
    return f"{start:%H:%M}–{end:%H:%M}"


def resource(key):
    name, department, capability, specs = UNITS[key]
    return {
        "id": key, "name": name, "department": department, "capability": capability,
        "specs": specs, "lab_id": key, "active": True, "time_zone": "UTC",
        "windows": [{"weekday": NOW.weekday(), "opens_at": "09:00", "closes_at": "12:00"}],
    }


def new_row(unit, title, purpose, start, end, deadline, minutes, *, capability=None, student="visitor", waited=0):
    return {
        "unit": unit, "project": title, "purpose": purpose, "start": start, "end": end,
        "deadline": deadline, "minutes": minutes, "student": student, "waited": waited, "status": "pending",
        "capability": capability or UNITS[unit][2],
    }


def initial_rows():
    urgent = new_row("printer", "Prototype enclosure", "Fit a protective casing before assembly.",
                     540, 720, 610, 60, student="alex", waited=6)
    flexible = new_row("printer", "Robot sensor mount", "Print the bracket for a sensor test.",
                       540, 720, 720, 60, student="sam", waited=2)
    past = new_row("printer", "Chassis fit check", "Check mounting hole spacing.",
                   540, 720, 720, 90, student="sam")
    past.update(status="approved", allocated=540, past=True)
    return [urgent, flexible, past]


def request_data(row, number):
    unit = resource(row["unit"])
    return {
        "id": str(number), "resource_id": unit["id"], "lab_id": unit["lab_id"],
        "student_uid": row["student"], "required_capabilities": [row.get("capability", unit["capability"])],
        "duration_minutes": row["minutes"], "starts_at": at(row["start"]),
        "earliest_start": at(row["start"]), "latest_end": at(row["end"]),
        "deadline": at(row["deadline"]), "submitted_at": NOW - timedelta(hours=row["waited"]),
    }


def snapshots(rows):
    by_unit = {key: [] for key in UNITS}
    histories = {}
    for row in rows:
        if row["status"] != "approved":
            continue
        start = at(row["allocated"])
        if row.get("past"):
            start -= timedelta(days=7)
        booking = {
            "starts_at": start, "ends_at": start + timedelta(minutes=row["minutes"]),
            "status": "approved", "reservation_id": "simulation",
        }
        by_unit[row["unit"]].append(booking)
        histories.setdefault(row["student"], []).append(booking)
    return by_unit, histories


def feasible(row, rows):
    by_unit, histories = snapshots(rows)
    return candidates(request_data(row, 0), [resource(row["unit"])], STUDENT, by_unit,
                      approved_intervals(histories.get(row["student"], [])), NOW)


def can_approve(row, rows, start):
    by_unit, histories = snapshots(rows)
    return slot_is_feasible(
        request_data(row, 0), resource(row["unit"]), STUDENT, at(start),
        by_unit[row["unit"]], approved_intervals(histories.get(row["student"], [])), NOW,
    )


def scenario(rows):
    resources = [resource(key) for key in UNITS]
    by_unit, histories = snapshots(rows)
    pending = {str(i): request_data(row, i) for i, row in enumerate(rows) if row["status"] == "pending"}
    students = {row["student"]: STUDENT for row in rows}
    plan = recommend(pending.values(), resources, students, by_unit, histories, now=NOW)
    order = [int(result.request_id) for result in plan]
    order.extend(index for index, row in enumerate(rows) if row["status"] != "pending")
    planned = {int(result.request_id): result for result in plan}
    requests = []
    for index in order:
        row = rows[index]
        item = {**row, "number": index, "equipment": UNITS[row["unit"]][0],
                "preferred": interval(at(row["start"]), at(row["end"])),
                "deadline_label": at(row["deadline"]).strftime("%H:%M")}
        if result := planned.get(index):
            options = feasible(row, rows)
            personal = histories.get(row["student"], [])
            req = pending[str(index)]
            item.update(
                options=[(slot.starts_at.hour * 60 + slot.starts_at.minute, interval(slot.starts_at, slot.ends_at))
                         for slot in options],
                recommended=result.slots[0].starts_at.hour * 60 + result.slots[0].starts_at.minute if result.slots else None,
                recommended_label=interval(result.slots[0].starts_at, result.slots[0].ends_at) if result.slots else "",
                score=result.score, prior=prior_minutes(personal, NOW),
                factors=score_components(req, options, prior_minutes(personal, NOW), NOW) if options else None,
                conflicts=requested_slot_conflicts(
                    req, resource(row["unit"]), STUDENT, by_unit[row["unit"]], approved_intervals(personal), now=NOW,
                ),
            )
            if result.slots and result.slots[0].starts_at != req["starts_at"] and not item["conflicts"]:
                item["conflicts"] = ["The preferred start overlaps an earlier recommendation in the ranked queue."]
            competing = [rows[int(other)]["project"] for other, candidate in pending.items()
                         if other != str(index) and candidate["resource_id"] == req["resource_id"]
                         and req["starts_at"] < candidate["starts_at"] + timedelta(minutes=candidate["duration_minutes"])
                         and candidate["starts_at"] < req["starts_at"] + timedelta(minutes=req["duration_minutes"])]
            item["competing"] = competing
        if row["status"] == "approved":
            start = at(row["allocated"])
            item["allocated_label"] = interval(start, start + timedelta(minutes=row["minutes"]))
        requests.append(item)
    for unit in resources:
        bookings = by_unit[unit["id"]]
        unit["slots"] = [interval(start, end) for start, end in available_windows(unit, bookings, now=NOW)]
        unit["usage"] = equipment_usage(unit, bookings, start=at(540), end=at(720))
    return resources, requests
