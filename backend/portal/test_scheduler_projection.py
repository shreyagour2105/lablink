from unittest.mock import Mock, patch

from django.test import SimpleTestCase

from .availability import available_windows
from .demo_scheduling import (
    NOW,
    STUDENT,
    initial_rows,
    request_data,
    resource,
    scenario,
    snapshots,
)
from .faculty_dashboard import prepare_queue


class SchedulerProjectionTests(SimpleTestCase):
    def test_demo_reuses_the_planners_feasible_slots_and_scoring_factors(self):
        with patch("portal.scheduling.available_windows", wraps=available_windows) as windows:
            _, rows = scenario(initial_rows())

        # Each pending request has one compatible unit. The view must not rescan it.
        self.assertEqual(windows.call_count, 2)
        urgent, flexible = rows[:2]
        self.assertEqual(urgent["score"], sum(urgent["factors"].values()))
        self.assertEqual(flexible["prior"], 90)
        self.assertEqual(flexible["recommended_label"], "10:00–11:00")
        self.assertIn((540, "09:00–10:00"), flexible["options"])

    def test_faculty_overrides_use_the_same_feasibility_snapshot(self):
        rows = initial_rows()
        resources = {"printer": resource("printer")}
        reservations, histories = snapshots(rows)
        pending = [
            {**request_data(row, index), "project_title": row["project"]}
            for index, row in enumerate(rows)
            if row["status"] == "pending"
        ]
        store = Mock()
        store.account.return_value = STUDENT
        store.history.side_effect = lambda uid: histories.get(uid, [])

        with patch("portal.scheduling.available_windows", wraps=available_windows) as windows:
            urgent, flexible = prepare_queue(store, pending, resources, reservations, NOW)

        self.assertEqual(windows.call_count, 2)
        self.assertEqual(urgent["rank"], 1)
        self.assertEqual(flexible["score"], sum(flexible["factors"].values()))
        self.assertEqual(flexible["prior_minutes"], 90)
        self.assertEqual(flexible["options"][0]["starts_at"].hour, 10)
        self.assertEqual(flexible["overrides"][0]["starts_at"].hour, 9)
