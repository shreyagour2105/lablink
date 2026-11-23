from django.test import SimpleTestCase

from lablink import vercel_settings


class VercelHostTests(SimpleTestCase):
    def test_production_alias_is_accepted_and_unrelated_hosts_are_rejected(self):
        with self.settings(ALLOWED_HOSTS=vercel_settings.ALLOWED_HOSTS):
            for host, expected in (
                ("shreyagour2105-lablink-yjmg-shreya-3f2c.vercel.app", 200),
                ("unrelated.vercel.app", 400),
                ("shreyagour2105-lablink-yjmg-shreya-3f2c.vercel.app.example.com", 400),
            ):
                with self.subTest(host=host):
                    response = self.client.get("/", secure=True, HTTP_HOST=host)
                    self.assertEqual(response.status_code, expected)
