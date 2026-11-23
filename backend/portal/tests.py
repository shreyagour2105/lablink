import json
import secrets
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from unittest.mock import patch
from urllib.request import Request, urlopen

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse
from firebase_admin import auth
from google.api_core.exceptions import ServiceUnavailable

from .availability import available_windows, interval_is_available
from .firebase_auth import firebase_app
from .forms import BookingForm, DecisionForm
from .reporting import equipment_usage, report_period, reservation_state
from .scheduling import Slot, candidates, recommend, score, slot_is_feasible
from .store import BookingConflict, Store, get_store

NOW = datetime(2040, 1, 2, 9, tzinfo=UTC)
RESOURCE = {
    "name": "Sample spectrometer", "description": "Test fixture",
    "capability": "Spectroscopy", "lab_id": "sample-optics", "lab_name": "Sample Optics Lab",
    "department": "Sample Physics", "location": "Demo location", "time_zone": "UTC",
    "active": True, "sample": True,
    "windows": [{"weekday": NOW.weekday(), "opens_at": "09:00", "closes_at": "17:00"}],
}
START = NOW + timedelta(hours=1)
END = START + timedelta(hours=1)
DEADLINE = END + timedelta(hours=2)
INPUTS = {
    "project_title": "Optics prototype",
    "purpose": "Measure the emission spectrum.",
    "starts_at": START.strftime("%Y-%m-%dT%H:%M"),
    "duration_minutes": 60,
    "deadline": DEADLINE.strftime("%Y-%m-%dT%H:%M"),
}


class AvailabilityTests(SimpleTestCase):
    def test_removes_elapsed_time_closures_and_reservations(self):
        blocked = {**RESOURCE, "blocks": [{"starts_at": END, "ends_at": END + timedelta(hours=1)}]}
        reserved = [{"starts_at": START + timedelta(minutes=30), "ends_at": END}]
        result = list(available_windows(blocked, reserved, now=START))
        self.assertEqual(result, [
            (START, START + timedelta(minutes=30)),
            (END + timedelta(hours=1), NOW + timedelta(hours=8)),
        ])
        self.assertTrue(interval_is_available(blocked, reserved, START, START + timedelta(minutes=30), now=NOW))
        self.assertFalse(interval_is_available(blocked, reserved, START, END, now=NOW))

    @patch("portal.forms.timezone.now", return_value=NOW)
    def test_validation_reports_deadline_and_duration(self, _):
        form = BookingForm({**INPUTS, "duration_minutes": 23, "deadline": INPUTS["starts_at"]}, time_zone="UTC")
        self.assertFalse(form.is_valid())
        self.assertIn("duration_minutes", form.errors)
        self.assertIn("deadline", form.errors)

    @patch("portal.forms.timezone.now", return_value=NOW)
    def test_validation_rejects_off_grid_start(self, _):
        form = BookingForm({**INPUTS, "starts_at": START.replace(minute=1).strftime("%Y-%m-%dT%H:%M")}, time_zone="UTC")
        self.assertFalse(form.is_valid())
        self.assertIn("starts_at", form.errors)

    @patch("portal.forms.timezone.now", return_value=NOW)
    def test_extreme_datetimes_fail_validation_without_raising(self, _):
        form = BookingForm({
            **INPUTS, "starts_at": "9999-12-31T23:45", "deadline": "9999-12-31T23:59",
            "earliest_start": "9999-12-31T23:00", "latest_end": "9999-12-31T23:59",
        }, time_zone="UTC")
        self.assertFalse(form.is_valid())
        self.assertIn("deadline", form.errors)
        self.assertIn("latest_end", form.errors)
        invalid_local_date = BookingForm({**INPUTS, "starts_at": "0001-01-01T00:00"}, time_zone="Asia/Kolkata")
        self.assertFalse(invalid_local_date.is_valid())
        self.assertIn("starts_at", invalid_local_date.errors)
        invalid_slot = DecisionForm({"decision": "approved", "slot": "sample|9999-12-31T23:59:59-01:00"})
        self.assertFalse(invalid_slot.is_valid())
        self.assertIn("slot", invalid_slot.errors)

    def test_unauthenticated_routes_and_csrf(self):
        client = Client(enforce_csrf_checks=True)
        self.assertContains(client.get(reverse("home")), "Find the lab equipment")
        self.assertRedirects(client.get(reverse("catalog")), reverse("sign-in"), fetch_redirect_response=False)
        self.assertEqual(client.post(reverse("create-session"), {"id_token": "something"}).status_code, 403)
        self.assertEqual(client.get(reverse("sign-out")).status_code, 405)


@unittest.skipUnless(
    settings.FIRESTORE_EMULATOR_HOST and settings.FIREBASE_AUTH_EMULATOR_HOST,
    "Firebase Auth and Firestore emulators are required",
)
class EmulatorBookingTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.store = Store(get_store().client, prefix=f"test_{secrets.token_hex(6)}_")
        cls.app = firebase_app()
        cls.users = []
        cls.addClassCleanup(cls.cleanup_records)
        cls.app_patch = patch("portal.views.get_store", return_value=cls.store)
        cls.app_patch.start()
        cls.addClassCleanup(cls.app_patch.stop)
        clock = patch("django.utils.timezone.now", return_value=NOW)
        clock.start()
        cls.addClassCleanup(clock.stop)
        cls.store.collection("labs").document("sample-optics").set({
            "name": RESOURCE["lab_name"], "department": RESOURCE["department"], "location": RESOURCE["location"],
        })
        cls.store.collection("equipment").document("spectrometer").set(RESOURCE)
        cls.student = cls.create_user("student", claims={"role": "faculty"})
        cls.other_student = cls.create_user("student")
        cls.faculty = cls.create_user("faculty", lab_ids=["sample-optics"])
        cls.unassigned = cls.create_user("faculty")
        cls.unprovisioned = cls.create_user(None)

    @classmethod
    def cleanup_records(cls):
        for uid in cls.users:
            auth.delete_user(uid, app=cls.app)
        for collection in ("equipment", "booking_requests", "accounts", "labs"):
            cls.store.client.recursive_delete(cls.store.collection(collection))

    @classmethod
    def create_user(cls, role, *, lab_ids=(), claims=None):
        email = f"{secrets.token_hex(12)}@example.test"
        password = secrets.token_urlsafe(18)
        user = auth.create_user(email=email, password=password, app=cls.app)
        cls.users.append(user.uid)
        if role:
            cls.store.provision(user.uid, role, list(lab_ids))
        if claims:
            auth.set_custom_user_claims(user.uid, claims, app=cls.app)
        return user.uid, email, password

    def sign_in(self, login, expected="student"):
        _, email, password = login
        client = Client(enforce_csrf_checks=True)
        client.get(reverse("sign-in"))
        request = Request(
            f"http://{settings.FIREBASE_AUTH_EMULATOR_HOST}/identitytoolkit.googleapis.com/v1/accounts:signInWithPassword?key=demo",
            data=json.dumps({"email": email, "password": password, "returnSecureToken": True}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with urlopen(request, timeout=10) as result:
            token = json.load(result)["idToken"]
        response = self.post(client, reverse("create-session"), {"id_token": token, "role": "faculty"})
        if expected is None:
            self.assertEqual(response.status_code, 403)
        else:
            self.assertEqual(response.status_code, 200, response.content)
            self.assertEqual(response.json(), {"role": expected})
            self.assertTrue(response.cookies[settings.AUTH_COOKIE_NAME]["httponly"])
        return client

    def post(self, client, url, data):
        return client.post(url, data, HTTP_X_CSRFTOKEN=client.cookies[settings.CSRF_COOKIE_NAME].value)

    def test_catalog_booking_approval_and_history(self):
        student = self.sign_in(self.student)
        faculty = self.sign_in(self.faculty, expected="faculty")
        unassigned = self.sign_in(self.unassigned, expected="faculty")
        other_student = self.sign_in(self.other_student)
        self.sign_in(self.unprovisioned, expected=None)
        self.assertEqual(student.get(reverse("faculty")).status_code, 403)
        page = student.get(reverse("catalog"), {"q": "spectro", "department": "Sample Physics"})
        self.assertContains(page, "Demo sample")
        unmatched = student.get(reverse("catalog"), {"department": "Other"})
        self.assertNotContains(unmatched, RESOURCE["name"])

        detail = reverse("resource-detail", args=["spectrometer"])
        self.assertContains(student.get(detail), "Request this equipment")
        self.assertEqual(self.post(student, detail, {**INPUTS, "duration_minutes": 14}).status_code, 400)
        submitted = self.post(student, detail, INPUTS)
        self.assertRedirects(submitted, reverse("student-history"))
        self.assertContains(student.get(reverse("student-history")), "Pending")
        item = self.store.history(self.student[0])[0]
        self.assertEqual(item["duration_minutes"], 60)
        self.assertEqual(item["deadline"], DEADLINE)
        self.assertEqual(self.store.upcoming_reservations("spectrometer", now=NOW), [])
        self.assertNotContains(other_student.get(reverse("student-history")), INPUTS["project_title"])

        decide = reverse("faculty-decision", args=[item["id"]])
        self.assertNotContains(unassigned.get(reverse("faculty")), INPUTS["project_title"])
        unassigned.get(reverse("sign-in"))
        self.assertEqual(self.post(unassigned, decide, {"decision": "approved"}).status_code, 404)
        self.assertEqual(self.post(student, decide, {"decision": "approved"}).status_code, 403)
        self.assertContains(faculty.get(reverse("faculty")), INPUTS["project_title"])
        self.assertContains(self.post(faculty, decide, {"decision": "rejected"}), "Enter a reason", status_code=400)
        self.assertRedirects(self.post(faculty, decide, {"decision": "approved"}), reverse("faculty"))
        reservations = self.store.upcoming_reservations("spectrometer", now=NOW)
        self.assertEqual([(row["starts_at"], row["ends_at"]) for row in reservations], [(START, END)])
        history = student.get(reverse("student-history"))
        self.assertContains(history, "Approved")
        self.assertContains(history, "Reservation history")
        self.assertContains(history, "10:00–11:00")
        updated = student.get(detail)
        self.assertEqual(updated.context["availability"], [(NOW, START), (END, NOW + timedelta(hours=8))])
        self.assertEqual(self.post(student, detail, INPUTS).status_code, 409)
        self.assertRedirects(self.post(student, reverse("sign-out"), {}), reverse("home"))
        self.assertRedirects(student.get(reverse("student-history")), reverse("sign-in"), fetch_redirect_response=False)

    def test_concurrent_approval_creates_one_reservation(self):
        resource = {**RESOURCE, "name": "Second sample spectrometer"}
        slug = "concurrent-spectrometer"
        self.store.collection("equipment").document(slug).set(resource)
        data = {**INPUTS, "project_title": "Concurrent fixture", "starts_at": START, "deadline": DEADLINE}
        users = [self.create_user("student")[0] for _ in range(2)]
        requests = [self.store.submit(uid, slug, data) for uid in users]

        def decide(request_id):
            try:
                self.store.decide(self.faculty[0], request_id, "approved", "")
            except BookingConflict:
                return "conflict"
            return "approved"

        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(decide, requests))
        self.assertCountEqual(decisions, ["approved", "conflict"])
        reservations = self.store.upcoming_reservations(slug, now=NOW)
        self.assertEqual(len(reservations), 1)
        loser = requests[decisions.index("conflict")]
        self.store.decide(self.faculty[0], loser, "rejected", "Interval reserved by another request.")
        snapshot = self.store.collection("booking_requests").document(loser).get()
        self.assertEqual(snapshot.get("status"), "rejected")

    def test_additional_capability_cannot_strip_original_requirements(self):
        preferred = "multi-capability-equipment"
        alternative = "narrow-capability-equipment"
        self.store.collection("equipment").document(preferred).set({
            **RESOURCE, "capabilities": ["Spectroscopy", "UV"],
        })
        self.store.collection("equipment").document(alternative).set({
            **RESOURCE, "capabilities": ["Spectroscopy"],
        })
        student_uid = self.create_user("student")[0]
        request_id = self.store.submit(student_uid, preferred, {
            **INPUTS, "starts_at": START, "deadline": DEADLINE,
            "earliest_start": START, "latest_end": DEADLINE, "required_capabilities": ["spectroscopy"],
        })
        self.assertEqual(self.store.history(student_uid)[0]["required_capabilities"], ["spectroscopy", "uv"])
        with self.assertRaises(BookingConflict):
            self.store.decide(self.faculty[0], request_id, "approved", "", (alternative, START))
        self.assertFalse(self.store.upcoming_reservations(alternative, now=NOW))

    def test_approve_and_reject_race_on_same_request_commits_one_decision(self):
        slug = "decision-race"
        self.store.collection("equipment").document(slug).set(RESOURCE)
        uid = self.create_user("student")[0]
        request_id = self.store.submit(uid, slug, {**INPUTS, "starts_at": START, "deadline": DEADLINE})

        def attempt(decision):
            try:
                self.store.decide(self.faculty[0], request_id, decision, "Test decision.")
            except BookingConflict:
                return "conflict"
            return decision

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(attempt, ("approved", "rejected")))
        self.assertEqual(results.count("conflict"), 1)
        winner = next(result for result in results if result != "conflict")
        self.assertEqual(self.store.history(uid)[0]["status"], winner)
        self.assertEqual(len(self.store.upcoming_reservations(slug, now=NOW)), int(winner == "approved"))

    def test_equivalent_unit_approval_and_eligibility_recheck(self):
        original = "original-spectral-unit"
        equivalent = "equivalent-spectral-unit"
        for slug, name in ((original, "Original unit"), (equivalent, "Equivalent unit")):
            self.store.collection("equipment").document(slug).set({**RESOURCE, "name": name})
        self.store.collection("equipment").document(equivalent).update({"access": "restricted"})
        student = self.sign_in(self.other_student)
        faculty = self.sign_in(self.faculty, expected="faculty")
        from_date = INPUTS["starts_at"]
        to_date = INPUTS["deadline"]
        submitted = self.post(student, reverse("resource-detail", args=[original]), {
            **INPUTS, "project_title": "Alternative slot project",
            "earliest_start": from_date, "latest_end": to_date, "required_capabilities": "Spectroscopy",
        })
        self.assertRedirects(submitted, reverse("student-history"))
        item = next(row for row in self.store.history(self.other_student[0]) if row["resource_id"] == original)
        self.assertContains(faculty.get(reverse("faculty")), "Recommended slots")
        decision_url = reverse("faculty-decision", args=[item["id"]])
        slot = f"{equivalent}|{START.isoformat()}"
        self.assertEqual(self.post(faculty, decision_url, {"decision": "approved", "slot": slot}).status_code, 409)

        self.store.provision(self.other_student[0], "student", [], ["sample-optics"])
        self.store.collection("equipment").document(original).update({"active": False})
        self.assertContains(faculty.get(reverse("faculty")), "Equivalent unit")
        self.assertRedirects(self.post(faculty, decision_url, {"decision": "approved", "slot": slot}), reverse("faculty"))
        self.assertContains(student.get(reverse("student-history")), "Equivalent unit")
        self.assertEqual(len(self.store.upcoming_reservations(equivalent, now=NOW)), 1)
        self.assertFalse(self.store.upcoming_reservations(original, now=NOW))

    def test_simultaneous_approvals_on_different_units_cannot_double_book_student(self):
        uid, _, _ = self.create_user("student")
        resources = ("cross-unit-a", "cross-unit-b")
        data = {**INPUTS, "starts_at": START, "deadline": DEADLINE}
        requests = []
        for slug in resources:
            self.store.collection("equipment").document(slug).set(RESOURCE)
            requests.append(self.store.submit(uid, slug, data))

        def decide(request_id):
            try:
                self.store.decide(self.faculty[0], request_id, "approved", "")
            except BookingConflict:
                return "pending"
            return "approved"

        with ThreadPoolExecutor(max_workers=2) as pool:
            decisions = list(pool.map(decide, requests))
        self.assertCountEqual(decisions, ["approved", "pending"])
        reserved = sum(len(self.store.upcoming_reservations(slug, now=NOW)) for slug in resources)
        self.assertEqual(reserved, 1)


    @override_settings(GEMINI_API_KEY="server-only-test-key")
    def test_recommendation_explanation_is_scoped_and_cannot_change_booking(self):
        slug = "explanation-fixture"
        self.store.collection("equipment").document(slug).set(RESOURCE)
        login = self.create_user("student")
        pending = self.store.submit(login[0], slug, {
            **INPUTS, "project_title": "Explain this request", "starts_at": START, "deadline": DEADLINE,
        })
        student = self.sign_in(login)
        faculty = self.sign_in(self.faculty, expected="faculty")
        unassigned = self.sign_in(self.unassigned, expected="faculty")
        url = reverse("faculty-explanation", args=[pending])
        with patch("portal.gemini.select_highlights", return_value=("deadline", "duration")) as select:
            self.assertEqual(faculty.get(url).status_code, 405)
            self.assertEqual(faculty.post(url).status_code, 403)
            self.assertEqual(self.post(student, url, {}).status_code, 403)
            self.assertEqual(self.post(unassigned, url, {}).status_code, 404)
            self.assertNotContains(faculty.get(reverse("faculty")), "Gemini selected")
            select.assert_not_called()
            result = self.post(faculty, url, {"decision": "approved", "slot": "invented-equipment"})
            self.assertContains(result, "Gemini selected the highlights.")
            self.assertContains(result, "The interval ends by the project")
            self.assertNotContains(result, "server-only-test-key")
            select.assert_called_once()
        with patch("portal.gemini.select_highlights", return_value=None):
            fallback = self.post(faculty, url, {})
            self.assertContains(fallback, "Rules-based explanation.")
        self.assertEqual(self.store.history(login[0])[0]["status"], "pending")
        self.assertEqual(self.store.upcoming_reservations(slug, now=NOW), [])

    def test_faculty_dashboard_reports_states_and_persists_rejection(self):
        lab = "isolated-report-lab"
        slug = "report-equipment"
        self.store.collection("labs").document(lab).set({
            "name": "Report Lab", "department": "Sample Engineering", "location": "Demo location",
        })
        unit = {
            **RESOURCE, "name": "Report Unit", "lab_id": lab, "lab_name": "Report Lab",
            "windows": [{"weekday": day, "opens_at": "08:00", "closes_at": "17:00"} for day in range(7)],
        }
        self.store.collection("equipment").document(slug).set(unit)
        faculty_login = self.create_user("faculty", lab_ids=[lab])
        student_uid = self.create_user("student")[0]
        other_uid = self.create_user("student")[0]
        other_faculty = self.sign_in(self.faculty, expected="faculty")
        faculty = self.sign_in(faculty_login, expected="faculty")
        dashboard = reverse("faculty")
        empty = faculty.get(dashboard)
        self.assertContains(empty, "No pending requests")
        self.assertContains(empty, "No reservations in this view")
        self.assertContains(empty, "No decisions have been recorded")

        def submit(uid, start, project):
            return self.store.submit(uid, slug, {
                **INPUTS, "project_title": project, "starts_at": start, "deadline": start + timedelta(hours=3),
            })

        with patch("django.utils.timezone.now", return_value=NOW - timedelta(days=2)):
            for start, title in (
                (NOW - timedelta(days=1) + timedelta(hours=1), "Ended project"),
                (NOW - timedelta(minutes=30), "Current project"),
            ):
                request_id = submit(student_uid, start, title)
                self.store.decide(faculty_login[0], request_id, "approved", "")
        upcoming = submit(student_uid, START, "Future project")
        pending = submit(other_uid, START, "Conflicting project")
        self.store.decide(faculty_login[0], upcoming, "approved", "")

        response = faculty.get(dashboard)
        self.assertContains(response, "Another confirmed reservation overlaps on this equipment.")
        self.assertContains(response, "No feasible recommendation")
        self.assertContains(response, "Past 7 days")
        self.assertEqual(
            {row["project"]: row["state"] for row in response.context["reservations"]},
            {"Future project": "Upcoming", "Current project": "In progress", "Ended project": "Ended"},
        )
        self.assertEqual(response.context["usage"][0]["booked_hours"], 1.5)
        self.assertEqual(response.context["usage"][0]["available_hours"], 63)
        self.assertContains(response, "2.4%")
        long_period = faculty.get(dashboard, {"days": "30"})
        self.assertContains(long_period, "Past 30 days")
        self.assertEqual(long_period.context["usage"][0]["available_hours"], 270)
        self.assertNotContains(other_faculty.get(dashboard), "Conflicting project")
        self.assertNotContains(other_faculty.get(dashboard), "Report Unit")

        url = reverse("faculty-decision", args=[pending]) + "?days=30"
        self.assertEqual(self.post(faculty, url, {"decision": "approved"}).status_code, 409)
        missing = self.post(faculty, url, {"decision": "rejected", "reason": " "})
        self.assertContains(missing, "Enter a reason", status_code=400)
        self.assertEqual(self.store.history(other_uid)[0]["status"], "pending")
        reason = "The only acceptable interval has already been booked."
        rejected = self.post(faculty, url, {"decision": "rejected", "reason": reason})
        self.assertRedirects(rejected, dashboard + "?days=30", fetch_redirect_response=False)
        confirmation = faculty.get(rejected.url)
        self.assertContains(confirmation, "Request rejected and reason recorded.")
        self.assertContains(confirmation, reason)
        self.assertContains(confirmation, "Rejected")
        self.assertNotContains(faculty.get(rejected.url), "Request rejected and reason recorded.")
        self.assertEqual(self.store.history(other_uid)[0]["decision_reason"], reason)

    def test_stale_approval_rechecks_maintenance_and_reports_error(self):
        slug = "maintenance-recheck"
        self.store.collection("equipment").document(slug).set(RESOURCE)
        student_uid = self.create_user("student")[0]
        pending = self.store.submit(student_uid, slug, {
            **INPUTS, "project_title": "Maintenance conflict", "starts_at": START, "deadline": DEADLINE,
        })
        faculty = self.sign_in(self.faculty, expected="faculty")
        dashboard = reverse("faculty")
        self.assertContains(faculty.get(dashboard), "Approve selected slot")
        self.store.collection("equipment").document(slug).update({
            "blocks": [{"starts_at": START, "ends_at": END}],
        })
        stale = self.post(faculty, reverse("faculty-decision", args=[pending]), {
            "decision": "approved", "slot": f"{slug}|{START.isoformat()}",
        })
        self.assertContains(stale, "The request is still pending", status_code=409)
        self.assertContains(stale, "A maintenance or closure block overlaps the requested interval.", status_code=409)
        self.assertEqual(self.store.upcoming_reservations(slug, now=NOW), [])
        with patch.object(self.store, "lab_records", side_effect=ServiceUnavailable("Firestore offline")):
            self.assertContains(faculty.get(dashboard), "LabLink is temporarily unavailable", status_code=503)


class SchedulingTests(SimpleTestCase):
    def setUp(self):
        self.unit = {**RESOURCE, "id": "scope", "capabilities": ["Spectroscopy", "UV"]}
        self.other = {**self.unit, "id": "equivalent", "capabilities": [" spectroscopy "]}
        self.student = {"id": "s", "role": "student"}
        self.request = {
            "id": "r", "student_uid": "s", "resource_id": "scope", "lab_id": RESOURCE["lab_id"],
            "starts_at": START, "duration_minutes": 60, "deadline": DEADLINE,
            "required_capabilities": ["Spectroscopy"], "submitted_at": NOW - timedelta(hours=1),
        }

    def test_hard_constraints_discard_expired_inactive_ineligible_and_mismatched_candidates(self):
        invalid = [
            ({**self.request, "deadline": NOW}, self.unit, self.student),
            ({**self.request, "deadline": START + timedelta(minutes=30)}, self.unit, self.student),
            ({**self.request, "duration_minutes": 22}, self.unit, self.student),
            ({**self.request, "required_capabilities": ["Thermography"]}, self.unit, self.student),
            (self.request, {**self.unit, "active": False}, self.student),
            (self.request, {**self.unit, "lab_id": "another-lab"}, self.student),
            (self.request, {**self.unit, "access": "restricted"}, self.student),
            (self.request, {**self.unit, "windows": []}, self.student),
            (self.request, {**self.unit, "blocks": [{"starts_at": START, "ends_at": END}]}, self.student),
            (self.request, self.unit, {"id": "s", "role": "faculty"}),
        ]
        for request, resource, student in invalid:
            with self.subTest(request=request, resource=resource, student=student):
                self.assertEqual(candidates(request, [resource], student, {}, [], NOW), [])
        restricted = {**self.unit, "access": "restricted"}
        eligible_student = {**self.student, "eligible_lab_ids": [self.unit["lab_id"]]}
        self.assertEqual(
            candidates(self.request, [restricted], eligible_student, {}, [], NOW),
            [Slot("scope", START, END)],
        )

    def test_tampered_approval_timestamp_cannot_overflow_duration_arithmetic(self):
        edge = datetime(9999, 12, 31, 23, 45, tzinfo=UTC)
        request = {**self.request, "deadline": edge + timedelta(minutes=14)}
        self.assertFalse(slot_is_feasible(request, self.unit, self.student, edge, [], [], NOW))

    def test_alternative_respects_student_window_and_collisions(self):
        flexible = {**self.request, "earliest_start": START, "latest_end": END + timedelta(minutes=15)}
        confirmed = {"starts_at": START, "ends_at": START + timedelta(minutes=15)}
        options = candidates(flexible, [self.other, self.unit], self.student, {"scope": [confirmed]}, [], NOW)
        self.assertEqual(options[:2], [
            Slot("scope", START + timedelta(minutes=15), END + timedelta(minutes=15)),
            Slot("equivalent", START, END),
        ])
        self.assertEqual(candidates(flexible, [self.other, self.unit], self.student, {}, [confirmed], NOW), [
            Slot("scope", START + timedelta(minutes=15), END + timedelta(minutes=15)),
            Slot("equivalent", START + timedelta(minutes=15), END + timedelta(minutes=15)),
        ])
        self.assertEqual(candidates(self.request, [self.other], self.student, {}, [], NOW), [])

    def test_tie_uses_request_id_and_proposals_do_not_collide(self):
        other = {**self.request, "id": "a", "student_uid": "other"}
        accounts = {"s": self.student, "other": {**self.student, "id": "other"}}
        results = recommend([self.request, other], [self.unit], accounts, {}, {}, now=NOW)
        self.assertEqual([item.request_id for item in results], ["a", "r"])
        self.assertEqual(results[0].slots, (Slot("scope", START, END),))
        self.assertEqual(results[1].slots, ())
        self.assertEqual(results, recommend([other, self.request], [self.unit], accounts, {}, {}, now=NOW))

    def test_aged_feasible_request_overtakes_more_urgent_new_request(self):
        urgent = {**self.request, "id": "fresh", "student_uid": "other", "deadline": END}
        aged = {**self.request, "submitted_at": NOW - timedelta(hours=48), "deadline": NOW + timedelta(days=6)}
        impossible = {**aged, "id": "expired", "deadline": NOW, "submitted_at": NOW - timedelta(days=3)}
        accounts = {"s": self.student, "other": {**self.student, "id": "other"}}
        results = recommend([urgent, impossible, aged], [self.unit], accounts, {}, {}, now=NOW)
        self.assertEqual([item.request_id for item in results], ["r", "fresh", "expired"])
        self.assertLess(results[0].score, results[1].score)
        self.assertTrue(results[0].slots)
        self.assertIsNone(results[2].score)

    def test_usage_fairness_changes_priority_and_score_has_documented_components(self):
        other = {**self.request, "id": "a", "student_uid": "other"}
        accounts = {"s": self.student, "other": {**self.student, "id": "other"}}
        histories = {"other": [{
            "status": "approved", "reservation_id": "old", "starts_at": NOW - timedelta(hours=3), "ends_at": NOW,
        }]}
        results = recommend([other, self.request], [self.unit], accounts, {}, histories, now=NOW)
        self.assertEqual([item.request_id for item in results], ["r", "a"])
        self.assertEqual(results[0].score - results[1].score, 6)
        slots = [Slot("scope", START, END)]
        self.assertEqual(score(self.request, slots, 0, NOW), 100)
        self.assertEqual(score(self.request, slots, 450, NOW), 85)


class ReportingTests(SimpleTestCase):
    def test_utilisation_clips_and_merges_confirmed_time_and_excludes_closures(self):
        resource = {**RESOURCE, "blocks": [{"starts_at": NOW + timedelta(hours=2), "ends_at": NOW + timedelta(hours=3)}]}
        report_end = NOW + timedelta(hours=5)
        reservations = [
            {"starts_at": NOW - timedelta(hours=1), "ends_at": START},
            {"starts_at": START + timedelta(minutes=30), "ends_at": NOW + timedelta(hours=3, minutes=30)},
            {"starts_at": NOW + timedelta(hours=3), "ends_at": NOW + timedelta(hours=4)},
            {"starts_at": report_end, "ends_at": report_end + timedelta(hours=1)},
        ]
        result = equipment_usage(resource, reservations, start=NOW, end=report_end)
        self.assertEqual(result, {"available_hours": 4, "booked_hours": 2.5, "percent": 62.5})
        empty = equipment_usage({**resource, "windows": []}, reservations, start=NOW, end=report_end)
        self.assertEqual(empty, {"available_hours": 0, "booked_hours": 0, "percent": None})

    def test_report_period_and_state_boundaries(self):
        self.assertEqual(report_period("30", NOW)["start"], NOW - timedelta(days=30))
        self.assertEqual(report_period("invalid", NOW)["days"], 7)
        self.assertEqual(reservation_state(START, END, NOW), "Upcoming")
        self.assertEqual(reservation_state(START, END, START), "In progress")
        self.assertEqual(reservation_state(START, END, END), "Ended")
