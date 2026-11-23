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
