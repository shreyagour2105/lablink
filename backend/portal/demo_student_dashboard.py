"""Read-only dashboard data derived from the existing demo schedule."""

from .demo_scheduling import NOW, at

ACTIVITY_LABELS = {
    "pending": "Request submitted",
    "approved": "Faculty approved the request",
    "rejected": "Faculty rejected the request",
    "reschedule requested": "Faculty requested rescheduling",
}


def record_activity(row: dict, requests: list[dict]) -> None:
    """Keep the latest transition per request, ordered without a real-world clock."""
    row["activity_order"] = 1 + max((item.get("activity_order", 0) for item in requests), default=0)


def student_overview(units: list[dict], requests: list[dict]) -> dict:
    pending = [row for row in requests if row["status"] == "pending"]
    approved = [row for row in requests if row["status"] == "approved"]
    upcoming = sorted(
        (row for row in approved if not row.get("past") and at(row["allocated"]) >= NOW),
        key=lambda row: (row["allocated"], row["number"]),
    )
    recommendations = [
        {
            **row,
            "alternatives": [label for value, label in row["options"] if value != row["recommended"]][:2],
        }
        for row in pending[:3]
    ]
    recent = sorted(
        (row for row in requests if row.get("activity_order")),
        key=lambda row: row["activity_order"],
        reverse=True,
    )[:4]
    return {
        "day": NOW.date(),
        "metrics": (
            ("Available Equipment", sum(bool(unit["slots"]) for unit in units)),
            ("Pending Requests", len(pending)),
            ("Approved Bookings", len(approved)),
            ("Upcoming Reservations", len(upcoming)),
        ),
        "upcoming": upcoming,
        "projects": requests,
        "recommendations": recommendations,
        "activity": [(row, ACTIVITY_LABELS[row["status"]]) for row in recent],
    }
