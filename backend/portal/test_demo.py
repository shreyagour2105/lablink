from unittest.mock import patch

from django.conf import settings
from django.test import Client, SimpleTestCase, override_settings
from django.urls import reverse

from .demo import COOKIE
from .demo_scheduling import initial_rows, scenario


@override_settings(FIREBASE_PROJECT_ID="", FIREBASE_WEB_API_KEY="", AUTH_COOKIE_SECURE=True)
class DemoTests(SimpleTestCase):
    def setUp(self):
        self.client = Client(enforce_csrf_checks=True)
        self.client.get(reverse("demo"), secure=True)

    def post(self, route, data, *args):
        return self.client.post(
            reverse(route, args=args), data, secure=True,
            HTTP_X_CSRFTOKEN=self.client.cookies[settings.CSRF_COOKIE_NAME].value,
            HTTP_REFERER="https://testserver/demo/",
        )

    def choose(self, role):
        response = self.post("demo-enter", {"role": role})
        self.assertRedirects(response, reverse(f"demo-{role}"), fetch_redirect_response=False)
        return response

    def test_demo_roles_cannot_cross_production_or_demo_boundaries(self):
        self.assertContains(self.client.get(reverse("demo"), secure=True), "Continue as Student")
        self.assertContains(self.client.get(reverse("demo"), secure=True), "Continue as Faculty")
        self.assertEqual(self.client.post(reverse("demo-enter"), {"role": "faculty"}, secure=True).status_code, 403)
        self.assertEqual(self.post("demo-enter", {"role": "admin"}).status_code, 400)
        cookie = self.choose("student").cookies[COOKIE]
        self.assertTrue(cookie["secure"] and cookie["httponly"])
        self.assertEqual(cookie["path"], "/demo/")
        self.assertEqual(self.client.get(reverse("demo-faculty"), secure=True).status_code, 403)
        self.assertEqual(self.post("demo-decide", {"decision": "approved"}, 0).status_code, 403)
        self.assertRedirects(self.client.get(reverse("faculty"), secure=True), reverse("sign-in"),
                             fetch_redirect_response=False)
        self.choose("faculty")
        self.assertEqual(self.post("demo-submit", {}).status_code, 403)
        self.client.cookies[COOKIE] = "tampered"
        self.assertEqual(self.post("demo-decide", {"decision": "approved"}, 0).status_code, 403)

    @patch("portal.views.get_store", side_effect=AssertionError("demo called real Firestore"))
    @patch("portal.firebase_auth.firebase_app", side_effect=AssertionError("demo called Firebase Auth"))
    def test_search_and_simulated_booking_decisions_stay_in_browser(self, firebase, store):
        self.choose("student")
        catalogue = self.client.get(reverse("demo-student"), {"q": "signal"}, secure=True)
        self.assertContains(catalogue, "Digital Oscilloscope")
        self.assertNotContains(catalogue, "GPU Workstation")
        booking = {
            "unit": "printer", "title": "Visitor prototype", "purpose": "Check the first print.",
            "start": "09:00", "end": "12:00", "deadline": "12:00", "minutes": "60",
            "capability": "Prototype fabrication",
        }
        self.assertEqual(self.post("demo-submit", {**booking, "unit": "real-unit"}).status_code, 400)
        self.assertEqual(self.post("demo-submit", {**booking, "capability": "GPU computing"}).status_code, 409)
        self.assertEqual(self.post("demo-submit", booking).status_code, 302)
        self.choose("faculty")
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "540"}, 0).status_code, 302)
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "540"}, 1).status_code, 409)
        self.assertEqual(self.post("demo-decide", {"decision": "rejected"}, 1).status_code, 302)
        self.choose("student")
        history = self.client.get(reverse("demo-student"), secure=True)
        self.assertContains(history, "Simulated approved")
        self.assertContains(history, "Simulated rejected")
        self.assertContains(history, "Available: 10:00–12:00.")
        self.assertEqual(self.post("demo-submit", {**booking, "deadline": "10:00"}).status_code, 409)
        fresh = Client()
        fresh.post(reverse("demo-enter"), {"role": "faculty"}, secure=True)
        self.assertNotContains(fresh.get(reverse("demo-faculty"), secure=True), "Visitor prototype")
        firebase.assert_not_called()
        store.assert_not_called()


    def test_demo_override_and_rescheduling_preserve_confirmed_bookings(self):
        self.choose("faculty")
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "555"}, 0).status_code, 409)
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "600"}, 0).status_code, 409)
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "660"}, 1).status_code, 302)
        self.assertEqual(self.post("demo-decide", {"decision": "reschedule requested"}, 1).status_code, 409)
        self.assertEqual(self.post("demo-decide", {"decision": "reschedule requested"}, 0).status_code, 302)
        dashboard = self.client.get(reverse("demo-faculty"), secure=True)
        self.assertContains(dashboard, "11:00–12:00")
        self.assertContains(dashboard, "1.00 / 3.00 h")
        self.choose("student")
        history = self.client.get(reverse("demo-student"), secure=True)
        self.assertContains(history, "Simulated reschedule requested")

    def test_seeded_conflict_recommends_urgent_and_fair_non_overlapping_slots(self):
        _, rows = scenario(initial_rows())
        self.assertEqual(rows[0]["project"], "Prototype enclosure")
        self.assertEqual(rows[0]["recommended_label"], "09:00–10:00")
        self.assertEqual(rows[0]["prior"], 0)
        self.assertEqual(rows[1]["recommended_label"], "10:00–11:00")
        self.assertEqual(rows[1]["prior"], 90)
        self.assertIn("Prototype enclosure", rows[1]["competing"])


    def test_signed_state_survives_a_new_client_and_mutations_require_csrf(self):
        self.choose("faculty")
        decision = reverse("demo-decide", args=[0])
        self.assertEqual(self.client.post(decision, {"decision": "approved", "start": "540"}, secure=True).status_code, 403)
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "540"}, 0).status_code, 302)
        new_request_client = Client()
        new_request_client.cookies[COOKIE] = self.client.cookies[COOKIE].value
        page = new_request_client.get(reverse("demo-faculty"), secure=True)
        self.assertContains(page, "1.00 / 3.00 h")
        self.assertContains(page, "Reserved on demo day: 09:00–10:00")
        self.assertNotContains(self.client.get(reverse("demo"), secure=True), "Sign in with a campus account")

    def test_landing_shows_seeded_allocations_and_direct_demo_link(self):
        page = self.client.get(reverse("home"), secure=True)
        self.assertContains(page, 'href="/demo/">Try Interactive Demo')
        self.assertContains(page, "Prototype enclosure")
        self.assertContains(page, "Robot sensor mount")
        self.assertContains(page, "09:00–10:00")
        self.assertContains(page, "10:00–11:00")

    def test_student_dashboard_updates_metrics_bookings_and_activity(self):
        self.choose("student")
        url = reverse("demo-student")
        initial = self.client.get(url, secure=True)
        self.assertContains(initial, "Student Dashboard")
        self.assertContains(initial, "No upcoming reservations.")
        self.assertContains(initial, "see activity from this demo session")
        self.assertEqual(dict(initial.context["dashboard"]["metrics"]), {
            "Available Equipment": 3, "Pending Requests": 2,
            "Approved Bookings": 1, "Upcoming Reservations": 0,
        })
        filtered = self.client.get(url, {"department": "Mechanical Engineering", "request": "1"}, secure=True)
        self.assertEqual(dict(filtered.context["dashboard"]["metrics"])["Available Equipment"], 3)
        self.assertEqual(filtered.context["request_unit"], "printer")
        self.assertContains(filtered, "<details open>")

        payload = {
            "unit": "printer", "title": "Dashboard fixture", "purpose": "Print a test bracket.",
            "start": "09:00", "end": "12:00", "deadline": "12:00", "minutes": "60",
            "capability": "Prototype fabrication",
        }
        self.assertEqual(self.client.post(reverse("demo-submit"), payload, secure=True).status_code, 403)
        self.assertEqual(self.post("demo-submit", payload).status_code, 302)
        submitted = self.client.get(url, secure=True)
        self.assertEqual(dict(submitted.context["dashboard"]["metrics"])["Pending Requests"], 3)
        self.assertContains(submitted, "Request submitted")

        self.choose("faculty")
        self.assertEqual(self.client.get(url, secure=True).status_code, 403)
        self.assertEqual(self.post("demo-decide", {"decision": "approved", "start": "660"}, 3).status_code, 302)
        self.assertEqual(self.post("demo-decide", {"decision": "reschedule requested"}, 1).status_code, 302)
        self.choose("student")

        updated = self.client.get(url, secure=True)
        dashboard = updated.context["dashboard"]
        self.assertEqual(dict(dashboard["metrics"]), {
            "Available Equipment": 3, "Pending Requests": 1,
            "Approved Bookings": 2, "Upcoming Reservations": 1,
        })
        self.assertEqual([row["project"] for row in dashboard["upcoming"]], ["Dashboard fixture"])
        self.assertEqual(dashboard["upcoming"][0]["allocated_label"], "11:00–12:00")
        self.assertContains(updated, "Faculty approved the request")
        self.assertContains(updated, "Faculty requested rescheduling")
        self.assertEqual(dashboard["activity"][0][0]["project"], "Robot sensor mount")

    @patch("portal.demo.initial_rows", return_value=[])
    def test_dashboard_project_and_recommendation_empty_states(self, _):
        self.choose("student")
        page = self.client.get(reverse("demo-student"), secure=True)
        self.assertContains(page, "No projects yet.")
        self.assertContains(page, "No pending requests to recommend slots for.")
        self.assertContains(page, "No upcoming reservations.")
