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

    def test_trial_access_ends_after_fifty_actionable_signals(self):
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        TradingSignal.objects.bulk_create([
            TradingSignal(
                user=self.user, symbol='EUR/USD', signal_type='BUY',
                confidence=70, source='SYSTEM',
            )
            for _ in range(50)
        ])
        access = subscription_status(self.user)
        self.assertFalse(access['has_access'])
        self.assertEqual(access['status'], 'trial_limit_reached')
        self.assertEqual(access['signals_used'], 50)
        self.assertEqual(access['signals_remaining'], 0)

        sr_vote = StrategyResult('Support/Resistance', 'bullish', 0.76, (), (), {}, 0.1)
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        TradingSignal.objects.bulk_create([
            TradingSignal(
                user=self.user, symbol='EUR/USD', signal_type='BUY',
                confidence=70, source='SYSTEM',
            )
            for _ in range(50)
        ])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': 'EUR/USD'})
        response = self.client.post('/api/analyze-signal/', {'symbol': 'EUR/USD'}, format='json')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(TradingSignal.objects.filter(user=self.user).count(), 50)

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

    @patch('trading_app.trading_bot.technical_analyzer.analyze_technical')
    @patch('trading_app.api_views._candles_to_dicts')
    @patch('trading_app.api_views._fetch_chart_candles')
    def test_analysis_response_includes_server_timing(
        self, fetch_candles, convert_candles, analyze,
    ):
        from django.urls import reverse

        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': 'EUR/USD'})
        fetch_candles.return_value = [object()]
        convert_candles.return_value = [{
            'time': int(timezone.now().timestamp()),
            'open': 1.1, 'high': 1.12, 'low': 1.09, 'close': 1.11, 'volume': 1,
        }]
        analyze.return_value = SimpleNamespace(
            bias='bullish', confidence=0.8, rationale='test setup', rsi=65,
            sma_short=1.1, sma_long=1.09, atr=0.01,
            stop_loss=1.09, take_profit=1.15,
        )
        convert_candles.return_value = [{
            'time': int(timezone.now().timestamp()) - (199 - i) * 900,
            'open': 1.1 + i * 0.001, 'high': 1.101 + i * 0.001,
            'low': 1.099 + i * 0.001, 'close': 1.1 + i * 0.001,
            'volume': 100,
        } for i in range(200)]

        response = self.client.post(
            reverse('api_analyze_signal'), {
                'symbol': 'EUR/USD', 'granularity': 900,
            }, format='json',
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(fetch_candles.call_count, 1)
        self.assertEqual(fetch_candles.call_args.kwargs['granularity'], 900)
        self.assertEqual(response.json()['analysis']['granularity'], 900)
        self.assertGreaterEqual(response.json()['analysis_duration_ms'], 0)
        self.assertEqual(
            response['X-Analysis-Duration-Ms'],
            str(response.json()['analysis_duration_ms']),
        )
        self.assertEqual(response.json()['signal']['signal_type'], 'BUY')
        self.assertIn('consensus', response.json()['analysis'])
        self.assertIn('timings', response.json()['analysis'])

    @patch('trading_app.ml.inference.load_latest_model', return_value=None)
    @patch('trading_app.api_views._fetch_chart_candles')
    def test_analysis_without_consensus_returns_neutral_no_signal(self, fetch_candles, _model):
        from django.urls import reverse
        self.user.trial_started_at = timezone.now()
        self.user.save(update_fields=['trial_started_at'])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': 'EUR/USD'})
        raw_candles = [SimpleNamespace(
            open=1.1, high=1.12, low=1.09, close=1.11, volume=1,
            timestamp=timezone.now(),
        ) for _ in range(200)]
        fetch_candles.return_value = raw_candles

        with patch('trading_app.trading_bot.technical_analyzer.analyze_technical') as analyze:
            analyze.return_value = SimpleNamespace(
                bias='neutral', confidence=0.0, rationale='no setup', rsi=50,
                sma_short=1.1, sma_long=1.1, atr=0.01,
                stop_loss=None, take_profit=None,
            )
            with patch('trading_app.api_views._candles_to_dicts', return_value=[{
                'time': int(timezone.now().timestamp()) + i * 300,
                'open': 1.1, 'high': 1.12, 'low': 1.09, 'close': 1.11,
                'volume': 1,
            } for i in range(200)]):
                response = self.client.post(reverse('api_analyze_signal'), {
                    'symbol': 'EUR/USD', 'granularity': 300,
                }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()['signal'])
        self.assertEqual(response.json()['message'], 'No consensus')

    @patch('trading_app.ml.inference.load_latest_model', return_value=None)
    @patch('trading_app.api_views._fetch_chart_candles')
    def test_fast_consensus_skips_unneeded_ml(self, fetch_candles, model_loader):
        from django.urls import reverse
        from trading_app.strategies.base import StrategyResult

        self.user.trial_started_at = timezone.now()
        self.user.selected_market = 'EUR/USD'
        self.user.save(update_fields=['trial_started_at', 'selected_market'])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': 'EUR/USD'})
        raw_candles = [SimpleNamespace(
            open=1.1, high=1.12, low=1.09, close=1.11, volume=100,
            timestamp=timezone.now(),
        ) for _ in range(200)]
        fetch_candles.return_value = raw_candles
        candle_dicts = [{
            'time': int(timezone.now().timestamp()) - (199 - i) * 300,
            'open': 1.1, 'high': 1.12, 'low': 1.09, 'close': 1.11,
            'volume': 100,
        } for i in range(200)]
        technical = SimpleNamespace(
            bias='bullish', confidence=0.8, rationale='technical bullish', rsi=60,
            sma_short=1.11, sma_long=1.10, atr=0.01,
            stop_loss=1.09, take_profit=1.15,
        )
        bullish_vote = StrategyResult('Momentum', 'bullish', 0.8, (), (), {}, 0.1)
        sr_vote = StrategyResult('Support/Resistance', 'bullish', 0.76, (), (), {}, 0.1)
        fib_vote = StrategyResult('Fibonacci', 'bullish', 0.74, (), (), {}, 0.1)
        ict_vote = StrategyResult('ICT', 'bullish', 0.78, (), (), {}, 0.1)

        with patch('trading_app.api_views._candles_to_dicts', return_value=candle_dicts), \
                patch('trading_app.trading_bot.technical_analyzer.analyze_technical', return_value=technical), \
                patch('trading_app.strategies.momentum.evaluate', return_value=bullish_vote), \
            patch('trading_app.strategies.support_resistance.evaluate', return_value=sr_vote), \
            patch('trading_app.strategies.fibonacci.evaluate', return_value=fib_vote), \
            patch('trading_app.strategies.ict.evaluate', return_value=ict_vote):
            response = self.client.post(reverse('api_analyze_signal'), {
                'symbol': 'EUR/USD', 'granularity': 300,
            }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['analysis']['fast_path'])
        self.assertEqual(response.json()['signal']['signal_type'], 'BUY')
        model_loader.assert_not_called()

    @patch('trading_app.ml.inference.load_latest_model', return_value=None)
    @patch('trading_app.api_views._fetch_chart_candles')
    def test_null_level_pending_signal_does_not_block_new_valid_signal(self, fetch_candles, _model):
        from django.urls import reverse

        self.user.trial_started_at = timezone.now()
        self.user.selected_market = 'EUR/USD'
        self.user.save(update_fields=['trial_started_at', 'selected_market'])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': 'EUR/USD'})
        TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=60,
            reasoning='Historical row with missing levels', source='SYSTEM',
        )
        fetch_candles.return_value = [SimpleNamespace(
            open=1.10, high=1.12, low=1.09, close=1.11, volume=100,
            timestamp=timezone.now(),
        ) for _ in range(200)]
        candle_dicts = [{
            'time': int(timezone.now().timestamp()) - (199 - i) * 900,
            'open': 1.10, 'high': 1.12, 'low': 1.09, 'close': 1.11,
            'volume': 100,
        } for i in range(200)]

        with patch('trading_app.api_views._candles_to_dicts', return_value=candle_dicts), \
                patch('trading_app.trading_bot.technical_analyzer.analyze_technical') as analyze:
            analyze.return_value = SimpleNamespace(
                bias='bullish', confidence=0.85, rationale='test setup', rsi=65,
                sma_short=1.1, sma_long=1.09, atr=0.01,
                stop_loss=1.09, take_profit=1.15,
            )
            response = self.client.post(reverse('api_analyze_signal'), {
                'symbol': 'EUR/USD', 'granularity': 900,
            }, format='json')

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.json()['signal'])
        new_signal = TradingSignal.objects.get(pk=response.json()['signal']['id'])
        self.assertIsNotNone(new_signal.entry_price)
        self.assertIsNotNone(new_signal.stop_loss)
        self.assertIsNotNone(new_signal.take_profit)

    def test_trade_execution_remains_disabled(self):
        response = self.client.post('/api/trades/execute/', {
            'symbol': 'EUR/USD', 'action': 'BUY', 'volume': 1,
        }, format='json')
        self.assertEqual(response.status_code, 410)
        self.assertFalse(response.json()['execution_enabled'])

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
            timestamp=signal.created_at - timedelta(minutes=5),
            low=1.099, high=1.101,
        ), SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.108, high=1.112,
        )]

        self.assertEqual(resolve_pending_signal_outcomes(), 1)
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'WIN')
        self.assertEqual(str(signal.outcome_price), '1.11000')
        self.assertIsNotNone(signal.outcome_resolved_at)

    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_signal_resolution_requests_history_for_signal_age(self, adapter_factory, _credentials):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
            entry_price='1.10000', stop_loss='1.09500', take_profit='1.11000',
        )
        old_created_at = timezone.now() - timedelta(days=3)
        TradingSignal.objects.filter(pk=signal.pk).update(created_at=old_created_at)
        signal.refresh_from_db()
        self.user.paper_mode = False
        self.user.save(update_fields=['paper_mode'])
        adapter_factory.return_value.get_candles.return_value = [SimpleNamespace(
            timestamp=signal.created_at - timedelta(minutes=5),
            low=1.099, high=1.101,
        ), SimpleNamespace(
            timestamp=signal.created_at - timedelta(minutes=5),
            low=1.099, high=1.101,
        ), SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.100, high=1.105,
        )]

        self.assertEqual(resolve_pending_signal_outcomes(), 0)
        request = adapter_factory.return_value.get_candles.call_args
        self.assertEqual(request.args[:2], ('frxEURUSD', 300))
        self.assertGreater(request.args[2], 200)
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'PENDING')

    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_signal_stays_pending_when_returned_history_starts_after_signal(
        self, adapter_factory, _credentials,
    ):
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

        self.assertEqual(resolve_pending_signal_outcomes(), 0)
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'PENDING')
        self.assertIsNone(signal.outcome_price)
        self.assertIsNone(signal.outcome_resolved_at)

    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_missing_entry_or_levels_stay_pending_without_market_request(self, adapter_factory):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
        )

        self.assertEqual(resolve_pending_signal_outcomes(), 0)
        adapter_factory.assert_not_called()
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'PENDING')
        self.assertIsNone(signal.outcome_price)
        self.assertIsNone(signal.outcome_resolved_at)

    @patch('trading_app.tasks.time.sleep')
    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_signal_outcome_waits_for_adapter_connection(
        self, adapter_factory, _credentials, sleep,
    ):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
            entry_price='1.10000', stop_loss='1.09500', take_profit='1.11000',
        )
        self.user.paper_mode = False
        self.user.save(update_fields=['paper_mode'])
        adapter = adapter_factory.return_value
        adapter._connected = False

        def become_connected(_interval):
            adapter._connected = True

        sleep.side_effect = become_connected
        adapter.get_candles.return_value = [SimpleNamespace(
            timestamp=signal.created_at - timedelta(minutes=5),
            low=1.099, high=1.101,
        ), SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.108, high=1.112,
        )]

        self.assertEqual(resolve_pending_signal_outcomes(), 1)
        adapter.get_candles.assert_called_once_with('frxEURUSD', 300, 200)
        sleep.assert_called_once_with(0.05)
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'WIN')

    @patch('trading_app.tasks.time.sleep')
    @patch('trading_app.tasks.time.monotonic', side_effect=[10.0, 16.0])
    @patch('trading_app.tasks.build_credentials', return_value={})
    @patch('trading_app.tasks.get_adapter_for_broker')
    def test_signal_stays_pending_if_adapter_does_not_connect(
        self, adapter_factory, _credentials, _monotonic, _sleep,
    ):
        from trading_app.tasks import resolve_pending_signal_outcomes

        signal = TradingSignal.objects.create(
            user=self.user, symbol='EUR/USD', signal_type='BUY', confidence=75,
            entry_price='1.10000', stop_loss='1.09500', take_profit='1.11000',
        )
        self.user.paper_mode = False
        self.user.save(update_fields=['paper_mode'])
        adapter_factory.return_value._connected = False

        self.assertEqual(resolve_pending_signal_outcomes(), 0)
        adapter_factory.return_value.get_candles.assert_not_called()
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'PENDING')
        self.assertIsNone(signal.outcome_price)

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
            timestamp=signal.created_at - timedelta(minutes=5),
            low=1.099, high=1.101,
        ), SimpleNamespace(
            timestamp=signal.created_at + timedelta(minutes=5),
            low=1.094, high=1.111,
        )]

        resolve_pending_signal_outcomes()
        signal.refresh_from_db()
        self.assertEqual(signal.outcome, 'LOSS')
        self.assertEqual(str(signal.outcome_price), '1.09500')
