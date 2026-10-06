from datetime import timedelta
import secrets
import sys
from unittest.mock import patch

from django.core import mail
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from trading_app.models import AdminAuditLog, PaymentTransaction, Subscription, SystemEvent, User
from trading_app.services.admin_access import ADMIN_EMAIL


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class OwnerAdminSecurityTests(TestCase):
    def setUp(self):
        cache.clear()
        mail.outbox.clear()
        self.admin = User.objects.create_user(
            email=ADMIN_EMAIL, username='owner', password=secrets.token_urlsafe(24),
            is_active=True, is_staff=True, is_superuser=True, two_factor_enabled=True,
        )
        self.trader = User.objects.create_user(
            email='trader@example.org', username='trader', password=secrets.token_urlsafe(24),
            is_staff=True, is_superuser=True, two_factor_enabled=True,
        )

    def tearDown(self):
        cache.clear()

    def _verified_admin_client(self):
        client = self.client
        client.force_login(self.admin)
        session = client.session
        session['2fa_verified'] = True
        session['admin_2fa_verified'] = True
        session.save()
        return client

    def test_owner_login_is_otp_gated_and_admin_dashboard_loads_after_verification(self):
        response = self.client.get(reverse('owner_admin:owner_dashboard'))
        self.assertRedirects(response, f"{reverse('login')}?next=/admin/overview/", fetch_redirect_response=False)
        password = secrets.token_urlsafe(24)
        self.admin.set_password(password)
        self.admin.save(update_fields=['password'])
        login_response = self.client.post(reverse('login') + '?next=/admin/overview/', {
            'username': ADMIN_EMAIL, 'password': password, 'next': '/admin/overview/',
        })
        self.assertRedirects(login_response, reverse('verify_2fa'))
        self.assertNotIn('_auth_user_id', self.client.session)
        otp = mail.outbox[-1].body.split('code is: ')[1].splitlines()[0]
        verified = self.client.post(reverse('verify_2fa'), {'otp_code': otp})
        self.assertRedirects(verified, '/admin/overview/')
        self.assertEqual(self.client.get('/admin/overview/').status_code, 200)

    def test_owner_is_otp_gated_even_if_two_factor_flag_was_cleared(self):
        self.admin.two_factor_enabled = False
        self.admin.save(update_fields=['two_factor_enabled'])
        password = secrets.token_urlsafe(24)
        self.admin.set_password(password)
        self.admin.save(update_fields=['password'])
        response = self.client.post(reverse('login'), {
            'username': ADMIN_EMAIL, 'password': password,
        })
        self.assertRedirects(response, reverse('verify_2fa'))
        self.assertNotIn('_auth_user_id', self.client.session)
        self.assertEqual(mail.outbox[-1].to, [ADMIN_EMAIL])

    def test_non_owner_staff_and_superuser_cannot_access_dashboard_or_admin_api(self):
        client = APIClient()
        client.force_authenticate(self.trader)
        self.assertEqual(client.get('/api/admin/overview/').status_code, 403)
        self.assertEqual(client.get('/api/models/latest/').status_code, 403)
        self.client.force_login(self.trader)
        session = self.client.session
        session['2fa_verified'] = True
        session.save()
        self.assertEqual(self.client.get('/admin/overview/').status_code, 403)

    def test_anonymous_user_cannot_access_admin_api(self):
        response = APIClient().get('/api/admin/overview/')
        self.assertIn(response.status_code, (401, 403))
        self.assertIn(APIClient().get('/api/models/latest/').status_code, (401, 403))

    def test_owner_without_2fa_session_is_denied_dashboard_and_api(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get('/admin/overview/').status_code, 302)
        self.assertEqual(APIClient().get('/api/admin/overview/').status_code, 403)

    def test_overview_omits_secrets_and_shows_real_subscription_and_payment_state(self):
        subscription = Subscription.objects.create(
            user=self.trader, tier='BASIC', is_active=True,
            expires_at=timezone.now() + timedelta(days=4),
        )
        PaymentTransaction.objects.create(
            user=self.trader, subscription=subscription, tier='BASIC', amount_kes=1500,
            phone_number='254700000000', status=PaymentTransaction.STATUS_PENDING,
        )
        SystemEvent.objects.create(event_type=SystemEvent.EVENT_FEED_FAILED, market='EUR/USD')
        SystemEvent.objects.create(event_type=SystemEvent.EVENT_ANALYSIS_FAILED, market='BTC/USD')
        response = self._verified_admin_client().get('/api/admin/overview/')
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        trader = next(row for row in payload['users'] if row['id'] == self.trader.pk)
        self.assertEqual(trader['subscription_status'], 'subscribed')
        self.assertEqual(payload['payments'][0]['status'], PaymentTransaction.STATUS_PENDING)
        self.assertEqual(payload['system']['deriv_data'], 'unavailable')
        self.assertEqual(payload['system']['failed_analysis_requests'], 1)
        self.assertEqual(len(payload['system_events']), 2)
        self.assertNotIn("'password':", str(payload).lower())
        self.assertNotIn('not-for-output', str(payload))
        self.assertNotIn('broker_api_key', str(payload))
        self.assertNotIn('broker_api_secret', str(payload))
        self.assertNotIn('EMAIL_HOST_PASSWORD', str(payload))

    def test_admin_user_search_and_pagination_are_server_bounded(self):
        for index in range(30):
            User.objects.create_user(
                email=f'page-{index}@example.org', username=f'page-{index}',
                password=secrets.token_urlsafe(18), first_name='Page',
            )
        client = self._verified_admin_client()
        page_one = client.get('/api/admin/overview/?page=1&page_size=10')
        self.assertEqual(page_one.status_code, 200)
        self.assertEqual(len(page_one.json()['users']), 10)
        self.assertEqual(page_one.json()['user_pagination']['total'], 32)
        page_two = client.get('/api/admin/overview/?page=2&page_size=10')
        self.assertEqual(page_two.json()['user_pagination']['page'], 2)
        searched = client.get('/api/admin/overview/?search=page-7%40example.org')
        self.assertEqual(len(searched.json()['users']), 1)
        self.assertEqual(searched.json()['users'][0]['email'], 'page-7@example.org')
        capped = client.get('/api/admin/overview/?page_size=10000')
        self.assertEqual(capped.json()['user_pagination']['page_size'], 100)

    def test_user_detail_is_admin_only_and_omits_credentials(self):
        url = f'/api/admin/users/{self.trader.pk}/'
        anonymous = APIClient().get(url)
        self.assertIn(anonymous.status_code, (401, 403))
        ordinary = APIClient()
        ordinary.force_authenticate(self.trader)
        self.assertEqual(ordinary.get(url).status_code, 403)
        response = self._verified_admin_client().get(url)
        self.assertEqual(response.status_code, 200)
        detail = response.json()
        self.assertEqual(detail['account']['email'], self.trader.email)
        self.assertIn('payments', detail)
        self.assertIn('active_sessions', detail)
        self.assertNotIn('password', str(detail).lower())
        self.assertNotIn('broker_api_key', str(detail))
        self.assertNotIn('broker_api_secret', str(detail))

    def test_user_admin_form_does_not_render_password_or_broker_credentials(self):
        client = self._verified_admin_client()
        url = reverse('owner_admin:trading_app_user_change', args=[self.trader.pk])
        response = client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, self.trader.password)
        self.assertNotContains(response, 'broker_api_key')
        self.assertNotContains(response, 'broker_api_secret')

    def test_admin_actions_are_server_authorized_and_cannot_change_payment_status(self):
        url = f'/api/admin/users/{self.trader.pk}/access/'
        anonymous = APIClient().post(url, {'subscription_bypass': True}, format='json')
        self.assertIn(anonymous.status_code, (401, 403))
        trader_client = APIClient()
        trader_client.force_authenticate(self.trader)
        self.assertEqual(trader_client.post(url, {'subscription_bypass': True}, format='json').status_code, 403)
        admin_client = self._verified_admin_client()
        self.assertEqual(admin_client.post(url, {'is_active': False}, content_type='application/json').status_code, 200)
        self.assertTrue(AdminAuditLog.objects.filter(
            administrator=self.admin, affected_user=self.trader, action='is_active_changed',
            previous_state={'is_active': True}, new_state={'is_active': False},
        ).exists())
        forbidden = admin_client.post(url, {'payment_status': 'SUCCESS'}, content_type='application/json')
        self.assertEqual(forbidden.status_code, 400)
        self_denied = admin_client.post(
            f'/api/admin/users/{self.admin.pk}/access/',
            {'is_staff': False}, content_type='application/json',
        )
        self.assertEqual(self_denied.status_code, 400)

    def test_admin_can_change_and_audit_independent_bot_bypass(self):
        response = self._verified_admin_client().post(
            f'/api/admin/users/{self.trader.pk}/access/',
            {'bot_bypass': True}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.trader.refresh_from_db()
        self.assertTrue(self.trader.bot_bypass)
        audit = AdminAuditLog.objects.get(
            affected_user=self.trader, action='bot_bypass_changed',
        )
        self.assertEqual(audit.administrator, self.admin)
        self.assertEqual(audit.previous_state, {'bot_bypass': False})
        self.assertEqual(audit.new_state, {'bot_bypass': True})

    def test_bypass_enable_and_disable_are_individually_audited(self):
        client = self._verified_admin_client()
        url = f'/api/admin/users/{self.trader.pk}/access/'
        for field in ('subscription_bypass', 'bot_bypass'):
            for enabled in (True, False):
                response = client.post(url, {field: enabled}, content_type='application/json')
                self.assertEqual(response.status_code, 200)
                record = AdminAuditLog.objects.get(
                    affected_user=self.trader,
                    action=f'{field}_changed',
                    new_state={field: enabled},
                )
                self.assertEqual(record.administrator, self.admin)
                self.assertIsNotNone(record.created_at.tzinfo)

    def test_access_audit_does_not_include_secrets(self):
        self._verified_admin_client().post(
            f'/api/admin/users/{self.trader.pk}/access/',
            {'subscription_bypass': True}, content_type='application/json',
            HTTP_USER_AGENT='audit-safe-test',
        )
        record = AdminAuditLog.objects.get(affected_user=self.trader)
        self.assertNotIn('password', str(record.previous_state).lower())
        self.assertNotIn('otp', str(record.new_state).lower())
        self.assertNotIn('secret', str(record))

    def test_admin_can_revoke_user_sessions_and_remember_me_tokens(self):
        from django.contrib.sessions.models import Session
        from trading_app.models import RememberMeToken

        session_client = self.client_class()
        session_client.force_login(self.trader)
        user_session_key = session_client.session.session_key
        RememberMeToken.objects.create(
            user=self.trader, token='opaque-test-token',
            expires_at=timezone.now() + timedelta(days=1),
        )
        admin_client = self._verified_admin_client()
        response = admin_client.post(
            f'/api/admin/users/{self.trader.pk}/access/',
            {'revoke_sessions': True}, content_type='application/json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Session.objects.filter(session_key=user_session_key).exists())
        self.assertFalse(RememberMeToken.objects.filter(user=self.trader).exists())

    def test_admin_logout_invalidates_session(self):
        client = self._verified_admin_client()
        response = client.post(reverse('logout'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', client.session)
        self.assertEqual(client.get('/api/admin/overview/').status_code, 403)

    def test_builtin_admin_login_cannot_bypass_application_2fa(self):
        response = self.client.post(reverse('owner_admin:login'), {
            'username': ADMIN_EMAIL, 'password': 'OwnerPass123!',
        })
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('_auth_user_id', self.client.session)

    @patch('trading_app.management.commands.setup_owner_admin.getpass')
    def test_setup_command_converges_duplicate_email_accounts_to_one_admin(self, _password_prompt):
        password = secrets.token_urlsafe(24)
        _password_prompt.side_effect = [password, password]
        duplicate = User.objects.create_user(
            email=ADMIN_EMAIL.upper(), username='owner-duplicate', password=secrets.token_urlsafe(24),
            is_staff=True, is_superuser=True,
        )
        from django.core.management import call_command
        call_command('setup_owner_admin', stdout=None)
        admins = User.objects.filter(email__iexact=ADMIN_EMAIL, is_staff=True, is_superuser=True)
        self.assertEqual(admins.count(), 1)
        self.assertFalse(User.objects.get(pk=duplicate.pk).is_staff)
        owner = User.objects.get(email=ADMIN_EMAIL)
        self.assertTrue(owner.check_password(password))
        self.assertNotEqual(owner.password, password)
        self.assertEqual(_password_prompt.call_count, 2)
        self.assertEqual(_password_prompt.call_args_list[0].args[0], 'New administrator password: ')
        self.assertEqual(_password_prompt.call_args_list[1].args[0], 'Confirm administrator password: ')
        self.assertIs(_password_prompt.call_args_list[0].kwargs['stream'], _password_prompt.call_args_list[1].kwargs['stream'])
        self.assertNotIn(password, _password_prompt.call_args_list[0].args[0])

    @patch('trading_app.scheduler.BackgroundScheduler.start')
    def test_owner_setup_management_command_does_not_start_background_scheduler(self, start_scheduler):
        from trading_app.apps import TradingAppConfig

        with patch.object(sys, 'argv', ['manage.py', 'setup_owner_admin']):
            TradingAppConfig('trading_app', __import__('trading_app')).ready()

        start_scheduler.assert_not_called()

    def test_admin_login_rejects_external_redirect_target(self):
        client = self._verified_admin_client()
        response = client.get(reverse('owner_admin:login'), {'next': 'https://example.org'})
        self.assertRedirects(response, reverse('owner_admin:owner_dashboard'))