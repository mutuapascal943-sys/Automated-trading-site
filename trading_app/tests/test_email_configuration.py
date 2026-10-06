from django.test import SimpleTestCase, override_settings

from trading_app.email_config import build_email_settings
from trading_app.services.email_diagnostics import email_configuration_status


class EmailConfigurationTests(SimpleTestCase):
    def test_production_selector_always_chooses_smtp_with_env_values(self):
        configured = build_email_settings(
            False, host='smtp.example.org', port=587, use_tls=True,
            username='mail-user', password='secret-value',
            from_email='security@example.org', file_backend=True,
        )
        self.assertEqual(configured['EMAIL_BACKEND'], 'django.core.mail.backends.smtp.EmailBackend')
        self.assertEqual(configured['EMAIL_HOST'], 'smtp.example.org')
        self.assertEqual(configured['EMAIL_PORT'], 587)
        self.assertTrue(configured['EMAIL_USE_TLS'])
        self.assertEqual(configured['EMAIL_HOST_USER'], 'mail-user')
        self.assertEqual(configured['EMAIL_HOST_PASSWORD'], 'secret-value')
        self.assertEqual(configured['DEFAULT_FROM_EMAIL'], 'security@example.org')

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST='smtp.example.org',
        EMAIL_PORT=587,
        EMAIL_USE_TLS=True,
        EMAIL_HOST_USER='mail-user',
        EMAIL_HOST_PASSWORD='not-for-output',
        DEFAULT_FROM_EMAIL='security@example.org',
    )
    def test_production_uses_configured_smtp_and_reports_readiness_without_secrets(self):
        from django.conf import settings

        self.assertEqual(settings.EMAIL_BACKEND, 'django.core.mail.backends.smtp.EmailBackend')
        self.assertEqual(settings.EMAIL_HOST, 'smtp.example.org')
        self.assertEqual(settings.EMAIL_PORT, 587)
        self.assertTrue(settings.EMAIL_USE_TLS)
        self.assertEqual(settings.EMAIL_HOST_USER, 'mail-user')
        self.assertEqual(settings.EMAIL_HOST_PASSWORD, 'not-for-output')
        self.assertEqual(settings.DEFAULT_FROM_EMAIL, 'security@example.org')
        status = email_configuration_status()
        self.assertTrue(status['ready'])
        self.assertNotIn('EMAIL_HOST_PASSWORD', status)
        self.assertNotIn('not-for-output', repr(status))

    @override_settings(
        DEBUG=False,
        EMAIL_BACKEND='django.core.mail.backends.smtp.EmailBackend',
        EMAIL_HOST_USER='',
        EMAIL_HOST_PASSWORD='',
    )
    def test_production_never_falls_back_to_console_when_smtp_credentials_are_missing(self):
        from django.conf import settings

        status = email_configuration_status()
        self.assertEqual(settings.EMAIL_BACKEND, 'django.core.mail.backends.smtp.EmailBackend')
        self.assertFalse(status['ready'])