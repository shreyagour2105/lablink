"""Build the student's booking history and read-only slot suggestions."""

from datetime import datetime

from .scheduling import (
    allows_alternatives,
    approved_intervals,
    candidates,
    matches,
    requested_slot_conflicts,
)
from .store import Store

MAX_REQUESTS_WITH_SUGGESTIONS = 10
MAX_SUGGESTIONS = 3


def booking_history(store: Store, account, *, now: datetime):
    items = store.history(account["id"])
    reservations = [item for item in items if item.get("reservation_id")]
    personal = approved_intervals(items)
    resources = {}
    availability = {}
    lab_equipment = {}

    def booked_for(slug):
        if slug not in availability:
            availability[slug] = store.upcoming_reservations(slug, now=now)
        return availability[slug]

    pending = [item for item in items if item["status"] == "pending"][:MAX_REQUESTS_WITH_SUGGESTIONS]
    for item in pending:
        slug = item["resource_id"]
        if slug not in resources:
            resources[slug] = store.resource(slug)
        unit = resources[slug]
        booked = booked_for(slug)
        item["conflicts"] = requested_slot_conflicts(item, unit, account, booked, personal, now=now)
        units = [unit] if unit else []
        suggestions = candidates(item, units, account, {slug: booked}, personal, now)
        if len(suggestions) < MAX_SUGGESTIONS and allows_alternatives(item):
            lab = item["lab_id"]
            if lab not in lab_equipment:
                lab_equipment[lab] = store.lab_records("equipment", [lab])
            alternatives = [
                option
                for option in lab_equipment[lab]
                if option["id"] != slug and matches(item, option, account)
            ]
            units.extend(alternatives)
            suggestions.extend(
                candidates(
                    item,
                    alternatives,
                    account,
                    {option["id"]: booked_for(option["id"]) for option in alternatives},
                    personal,
                    now,
                )
            )
        by_id = {option["id"]: option for option in units}
        item["suggestions"] = [
            {"equipment": by_id[slot.resource_id], "start": slot.starts_at, "end": slot.ends_at}
            for slot in suggestions[:MAX_SUGGESTIONS]
        ]
        item["show_suggestions"] = True
    return {"items": items, "reservations": reservations}
