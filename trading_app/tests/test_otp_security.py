from datetime import timedelta
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.core import signing
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from trading_app.models import EmailOTP, User
from trading_app.models import RememberMeToken
from trading_app.services.otp_service import OTPResult, consume_otp, issue_otp


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OTPEmailSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.user = User.objects.create_user(
            email='otp-security@example.com', username='otp-security', password='SecurePass123!',
        )

    def tearDown(self):
        cache.clear()

    def test_otp_is_hashed_purpose_bound_expiring_and_one_time(self):
        record, raw_code = issue_otp(self.user, 'password_reset')
        self.assertNotEqual(record.code, raw_code)
        self.assertEqual(len(record.code), 64)
        self.assertEqual(consume_otp(self.user, '2fa', raw_code), OTPResult.INVALID)
        self.assertEqual(consume_otp(self.user, 'password_reset', raw_code), OTPResult.VERIFIED)
        self.assertEqual(consume_otp(self.user, 'password_reset', raw_code), OTPResult.USED)

    def test_five_wrong_codes_lock_verification_until_reissue(self):
        record, correct_code = issue_otp(self.user, '2fa')
        for _ in range(4):
            result = consume_otp(self.user, '2fa', '000000')
            self.assertEqual(result, OTPResult.INVALID)
        self.assertEqual(consume_otp(self.user, '2fa', '000000'), OTPResult.RATE_LIMITED)
        self.assertEqual(consume_otp(self.user, '2fa', correct_code), OTPResult.RATE_LIMITED)
        issue_otp(self.user, '2fa')
        self.assertFalse(EmailOTP.objects.get(pk=record.pk).is_used is False)

    def test_expired_code_is_rejected_and_marked_used(self):
        record, raw_code = issue_otp(self.user, '2fa')
        EmailOTP.objects.filter(pk=record.pk).update(expires_at=timezone.now() - timedelta(seconds=1))
        self.assertEqual(consume_otp(self.user, '2fa', raw_code), OTPResult.EXPIRED)
        record.refresh_from_db()
        self.assertTrue(record.is_used)

    def test_login_2fa_delivers_email_without_authenticating_until_verified(self):
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])
        client = self.client
        response = client.post(reverse('login'), {
            'username': self.user.email, 'password': 'SecurePass123!',
        })
        self.assertRedirects(response, reverse('verify_2fa'))
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(len(mail.outbox), 1)
        sent_code = mail.outbox[0].body.split('code is: ')[1].splitlines()[0]
        verified = client.post(reverse('verify_2fa'), {'otp_code': sent_code})
        self.assertRedirects(verified, reverse('dashboard'))
        self.assertEqual(str(client.session.get('_auth_user_id')), str(self.user.pk))
        self.assertTrue(client.session.get('2fa_verified'))
        record = EmailOTP.objects.filter(user=self.user, purpose='2fa').latest('created_at')
        self.assertTrue(record.is_used)
        replay = client.post(reverse('verify_2fa'), {'otp_code': sent_code})
        self.assertEqual(replay.status_code, 200)

    def test_remember_me_login_still_requires_email_2fa(self):
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])
        token = 'remember-me-test-token'
        RememberMeToken.objects.create(
            user=self.user, token=token, expires_at=timezone.now() + timedelta(days=1),
        )
        client = self.client
        client.cookies['remember_me'] = signing.get_cookie_signer(salt='remember_me').sign(token)
        response = client.get(reverse('auto_login'))
        self.assertRedirects(response, reverse('verify_2fa'))
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(client.session.get('pending_2fa_user_id'), self.user.pk)

    @patch('trading_app.views.send_otp_email', return_value=False)
    def test_email_delivery_failure_does_not_authenticate_or_leave_valid_code(self, _send):
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])
        response = self.client.post(reverse('login'), {
            'username': self.user.email, 'password': 'SecurePass123!',
        })
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('_auth_user_id', self.client.session)
        otp = EmailOTP.objects.filter(user=self.user, purpose='2fa').latest('created_at')
        self.assertTrue(otp.is_used)

    def test_resend_invalidates_previous_code(self):
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])
        self.client.post(reverse('login'), {'username': self.user.email, 'password': 'SecurePass123!'})
        old_record = EmailOTP.objects.filter(user=self.user, purpose='2fa').latest('created_at')
        old_code = mail.outbox[-1].body.split('code is: ')[1].splitlines()[0]
        self.client.post(reverse('resend_otp'))
        old_record.refresh_from_db()
        self.assertTrue(old_record.is_used)
        self.assertNotEqual(consume_otp(self.user, '2fa', old_code), OTPResult.VERIFIED)

    def test_authenticated_2fa_user_is_blocked_from_api_before_verification(self):
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])
        client = APIClient()
        client.force_login(self.user)
        response = client.get(reverse('api_subscription'))
        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()['status'], 'verification_required')


class TOTPSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = User.objects.create_user(
            email='totp-security@example.com', username='totp-security', password='SecurePass123!',
        )
        self.client = APIClient()

    def tearDown(self):
        cache.clear()

    def test_totp_setup_view_generates_qr_and_secret(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['2fa_verified'] = True
        session.save()

        response = self.client.get(reverse('setup_authenticator'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('qr_code_b64', response.context)
        self.assertIn('secret_key_b32', response.context)
        self.assertFalse(response.context['is_active'])

        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.get(user=self.user, name='default')
        self.assertFalse(device.confirmed)

    def test_totp_setup_view_confirms_valid_token(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['2fa_verified'] = True
        session.save()

        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=False)
        from django_otp.oath import TOTP
        import time
        totp = TOTP(key=bytes.fromhex(device.key), step=30, digits=6)
        totp.time = time.time()
        valid_code = str(totp.token()).zfill(6)

        response = self.client.post(reverse('setup_authenticator'), {'otp_code': valid_code})
        self.assertRedirects(response, reverse('profile_page'))

        device.refresh_from_db()
        self.assertTrue(device.confirmed)
        self.user.refresh_from_db()
        self.assertTrue(self.user.two_factor_enabled)

    def test_totp_setup_view_rejects_invalid_token(self):
        self.client.force_login(self.user)
        session = self.client.session
        session['2fa_verified'] = True
        session.save()

        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=False)
        response = self.client.post(reverse('setup_authenticator'), {'otp_code': '000000'})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(device.confirmed)

    def test_totp_login_verification_flow(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])

        response = self.client.post(reverse('login'), {
            'username': self.user.email, 'password': 'SecurePass123!',
        })
        self.assertRedirects(response, reverse('verify_2fa'))

        from django_otp.oath import TOTP
        import time
        totp = TOTP(key=bytes.fromhex(device.key), step=30, digits=6)
        totp.time = time.time()
        valid_code = str(totp.token()).zfill(6)

        verified = self.client.post(reverse('verify_2fa'), {'otp_code': valid_code})
        self.assertRedirects(verified, reverse('dashboard'))
        self.assertTrue(self.client.session.get('2fa_verified'))

    def test_totp_disable_requires_password(self):
        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.create(user=self.user, name='default', confirmed=True)
        self.user.two_factor_enabled = True
        self.user.save(update_fields=['two_factor_enabled'])

        self.client.force_login(self.user)
        session = self.client.session
        session['2fa_verified'] = True
        session.save()

        fail = self.client.post(reverse('disable_authenticator'), {'password': 'WrongPassword!'})
        self.assertTrue(TOTPDevice.objects.filter(user=self.user).exists())

        success = self.client.post(reverse('disable_authenticator'), {'password': 'SecurePass123!'})
        self.assertFalse(TOTPDevice.objects.filter(user=self.user).exists())

    def test_api_setup_totp_endpoint(self):
        self.client.force_login(self.user)
        get_res = self.client.get(reverse('api_setup_totp'))
        self.assertEqual(get_res.status_code, 200)
        self.assertEqual(get_res.json()['status'], 'unconfirmed')
        self.assertIn('provisioning_uri', get_res.json())
        self.assertIn('qr_code_base64', get_res.json())

        from django_otp.plugins.otp_totp.models import TOTPDevice
        device = TOTPDevice.objects.get(user=self.user, name='default')
        from django_otp.oath import TOTP
        import time
        totp = TOTP(key=bytes.fromhex(device.key), step=30, digits=6)
        totp.time = time.time()
        valid_code = str(totp.token()).zfill(6)

        post_res = self.client.post(reverse('api_setup_totp'), {'code': valid_code})
        self.assertEqual(post_res.status_code, 200)
        self.assertEqual(post_res.json()['status'], 'verified')
        device.refresh_from_db()
        self.assertTrue(device.confirmed)
