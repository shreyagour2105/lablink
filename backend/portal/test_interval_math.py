from datetime import timedelta

from django.test import SimpleTestCase

from .availability import available_windows, interval_is_available, subtract_intervals
from .demo_scheduling import NOW, at, initial_rows, request_data, resource
from .reporting import equipment_usage
from .scheduling import requested_slot_conflicts


class IntervalMathTests(SimpleTestCase):
    def test_zero_duration_block_does_not_split_availability_or_report_conflict(self):
        instant = at(600)
        point = {"starts_at": instant, "ends_at": instant}
        unit = {**resource("printer"), "blocks": [point]}
        self.assertEqual(list(available_windows(unit, now=NOW)), [(at(540), at(720))])
        self.assertTrue(
            interval_is_available(
                unit,
                [point],
                instant - timedelta(minutes=30),
                instant + timedelta(minutes=30),
                now=NOW,
            )
        )
        request = {**request_data(initial_rows()[1], 1), "starts_at": at(570)}
        self.assertEqual(
            requested_slot_conflicts(
                request,
                unit,
                {"role": "student"},
                [point],
                [point],
                now=NOW,
            ),
            [],
        )

    def test_block_continuing_into_next_window_is_not_discarded(self):
        windows = [(at(540), at(600)), (at(660), at(720))]
        blocks = [(at(690), at(705)), (at(570), at(675)), (at(585), at(660))]
        self.assertEqual(
            list(subtract_intervals(windows, blocks)),
            [(at(540), at(570)), (at(675), at(690)), (at(705), at(720))],
        )

    def test_utilisation_counts_union_of_reservations_after_closures(self):
        unit = {**resource("printer"), "blocks": [{"starts_at": at(600), "ends_at": at(615)}]}
        reservations = [
            {"starts_at": at(660), "ends_at": at(720)},
            {"starts_at": at(599), "ends_at": at(630)},
            {"starts_at": at(555), "ends_at": at(630)},
            {"starts_at": at(640), "ends_at": at(635)},
            {"starts_at": at(650), "ends_at": at(650)},
        ]
        result = equipment_usage(unit, reservations, start=at(540), end=at(720))
        self.assertAlmostEqual(result["available_hours"], 2.75)
        self.assertAlmostEqual(result["booked_hours"], 2)
        self.assertAlmostEqual(result["percent"], 100 * 2 / 2.75)
