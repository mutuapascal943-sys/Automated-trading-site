from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase, override_settings
from django.utils import timezone
from django.core.cache import cache
from rest_framework.test import APIClient

from trading_app.models import Subscription, TradingSignal, User
from trading_app.services.subscription_access import subscription_status


class SubscriptionAccessTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='access@example.com', username='access-user', password='Testpass123',
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)
        cache.delete(f'bot_status_{self.user.id}')

    def test_new_trial_has_access_for_24_hours(self):
        self.user.trial_started_at = timezone.now() - timedelta(hours=23)
        self.user.save(update_fields=['trial_started_at'])
        self.assertTrue(subscription_status(self.user)['has_access'])
        self.assertEqual(subscription_status(self.user)['status'], 'trial')

    def test_new_users_get_trial_start_automatically(self):
        new_user = User.objects.create_user(
            email='new-trial@example.com', username='new-trial', password='Testpass123',
        )
        self.assertIsNotNone(new_user.trial_started_at)
        self.assertTrue(subscription_status(new_user)['has_access'])

    def test_expired_trial_is_blocked(self):
        self.user.trial_started_at = timezone.now() - timedelta(hours=25)
        self.user.save(update_fields=['trial_started_at'])
        self.assertFalse(subscription_status(self.user)['has_access'])

    def test_expired_user_can_load_dashboard_and_subscription_status(self):
        from django.urls import reverse

        self.user.trial_started_at = timezone.now() - timedelta(days=2)
        self.user.save(update_fields=['trial_started_at'])
        dashboard = self.client.get(reverse('api_dashboard_stats'))
        subscription = self.client.get(reverse('api_subscription'))
        self.assertEqual(dashboard.status_code, 200)
        self.assertFalse(dashboard.json()['subscription']['has_access'])
        self.assertEqual(subscription.status_code, 200)
        self.assertEqual(subscription.json()['access']['status'], 'expired')

    def test_paid_subscription_has_access_until_expiry(self):
        Subscription.objects.create(
            user=self.user, tier='BASIC', is_active=True,
            expires_at=timezone.now() + timedelta(days=30),
        )
        self.assertEqual(subscription_status(self.user)['status'], 'subscribed')

    def test_admin_bypass_grants_access(self):
        self.user.subscription_bypass = True
        self.user.save(update_fields=['subscription_bypass'])
        self.assertEqual(subscription_status(self.user)['status'], 'admin_bypass')

    def test_admin_revocation_overrides_trial_and_bypass(self):
        self.user.trial_started_at = timezone.now()
        self.user.subscription_bypass = True
        self.user.subscription_access_denied = True
        self.user.save(update_fields=[
            'trial_started_at', 'subscription_bypass', 'subscription_access_denied',
        ])
        self.assertEqual(subscription_status(self.user)['status'], 'admin_denied')

    def test_expired_user_cannot_start_bot(self):
        self.user.trial_started_at = timezone.now() - timedelta(days=2)
        self.user.save(update_fields=['trial_started_at'])
        response = self.client.post('/api/bot/control/', {'action': 'start'}, format='json')
        self.assertEqual(response.status_code, 403)

    def test_active_trial_can_start_and_stop_bot(self):
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        start = self.client.post('/api/bot/control/', {'action': 'start'}, format='json')
        self.assertEqual(start.status_code, 200)
        self.assertEqual(start.json()['status'], 'running')
        results = self.client.post('/api/bot/control/', {'action': 'results'}, format='json')
        self.assertEqual(results.json()['status'], 'results')
        running = self.client.post('/api/bot/control/', {'action': 'running'}, format='json')
        self.assertEqual(running.json()['status'], 'running')
        stop = self.client.post('/api/bot/control/', {'action': 'stop'}, format='json')
        self.assertEqual(stop.json()['status'], 'idle')

    def test_start_bot_persists_selected_market(self):
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        response = self.client.post(
            '/api/bot/control/',
            {'action': 'start', 'market': 'XAU/USD'},
            format='json',
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {'status': 'running', 'market': 'XAU/USD'})
        self.user.refresh_from_db()
        self.assertEqual(self.user.selected_market, 'XAU/USD')

    def test_duplicate_bot_start_is_rejected(self):
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        self.client.post('/api/bot/control/', {'action': 'start'}, format='json')
        duplicate = self.client.post('/api/bot/control/', {'action': 'start'}, format='json')
        self.assertEqual(duplicate.status_code, 409)

    def test_market_selection_rejects_unsupported_market(self):
        response = self.client.post('/api/bot/market/', {'market': 'UNKNOWN/PAIR'}, format='json')
        self.assertEqual(response.status_code, 400)

    def test_expired_user_cannot_generate_signals(self):
        self.user.trial_started_at = timezone.now() - timedelta(days=2)
        self.user.save(update_fields=['trial_started_at'])
        response = self.client.post('/api/analyze-signal/', {'symbol': 'EUR/USD'}, format='json')
        self.assertEqual(response.status_code, 403)

    @override_settings(MOCK_SUBSCRIPTIONS_ENABLED=True)
    def test_mock_checkout_grants_thirty_day_access(self):
        response = self.client.post('/api/subscription/checkout/', {'tier': 'BASIC'}, format='json')
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['access_granted'])
        self.assertEqual(response.json()['status'], 'mock_activated')
        self.user.refresh_from_db()
        subscription = self.user.subscription
        self.assertEqual(subscription.tier, 'BASIC')
        self.assertGreater(subscription.expires_at, timezone.now() + timedelta(days=29))

    @override_settings(MOCK_SUBSCRIPTIONS_ENABLED=False)
    def test_checkout_requires_provider_when_mock_is_disabled(self):
        response = self.client.post('/api/subscription/checkout/', {'tier': 'BASIC'}, format='json')
        self.assertEqual(response.status_code, 503)
        self.assertFalse(response.json()['access_granted'])
        self.assertFalse(Subscription.objects.filter(user=self.user).exists())

    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_signal_outcome_resolves_hit_take_profit(self, adapter_factory, _credentials):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
            entry_price='1.10000', stop_loss='1.09500', take_profit='1.11000',
        )
        self.user.paper_mode = False
        self.user.save(update_fields=['paper_mode'])
        adapter = adapter_factory.return_value
        adapter.get_candles.return_value = [SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.108, high=1.112,
        )]

        self.assertEqual(resolve_pending_signal_outcomes(), 1)
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'WIN')
        self.assertEqual(str(signal.outcome_price), '1.11000')

    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_stop_loss_wins_if_one_candle_touches_both_levels(self, adapter_factory, _credentials):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
            entry_price='1.10000', stop_loss='1.09500', take_profit='1.11000',
        )
        self.user.paper_mode = False
        self.user.save(update_fields=['paper_mode'])
        adapter_factory.return_value.get_candles.return_value = [SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.094, high=1.111,
        )]

        resolve_pending_signal_outcomes()
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'LOSS')
        self.assertEqual(str(signal.outcome_price), '1.09500')
