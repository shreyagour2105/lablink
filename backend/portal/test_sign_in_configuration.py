from django.test import SimpleTestCase, override_settings
from django.urls import reverse


@override_settings(FIREBASE_AUTH_EMULATOR_HOST="")
class SignInConfigurationTests(SimpleTestCase):
    def test_configured_sign_in_renders_browser_configuration(self):
        with self.settings(FIREBASE_PROJECT_ID="fixture-project", FIREBASE_WEB_API_KEY="fixture-web-key"):
            response = self.client.get(reverse("sign-in"), secure=True)
        self.assertContains(response, 'id="sign-in"')
        self.assertContains(response, '"projectId": "fixture-project"')
        self.assertContains(response, '"apiKey": "fixture-web-key"')
        self.assertNotContains(response, "Sign-in configuration is missing.")
        self.assertIn("no-store", response.headers["Cache-Control"])

    def test_sign_in_requires_both_firebase_settings(self):
        for project, web_key in (("", "fixture-web-key"), ("fixture-project", ""), ("", "")):
            with self.subTest(project=project, web_key=web_key):
                with self.settings(FIREBASE_PROJECT_ID=project, FIREBASE_WEB_API_KEY=web_key):
                    response = self.client.get(reverse("sign-in"), secure=True)
                self.assertContains(response, "Sign-in configuration is missing.")
                self.assertNotContains(response, 'id="sign-in"')
