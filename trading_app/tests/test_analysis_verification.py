from datetime import timedelta
from decimal import Decimal
from contextlib import ExitStack, contextmanager
import secrets
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import MagicMock, patch

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework.test import APIClient

from trading_app.models import RiskConfig, TradingSignal, User
from trading_app.services.dev_verification_auth import DEV_USERNAME_PREFIX
from trading_app.strategies.base import StrategyResult


def _live_candles(granularity=300, count=200, age_seconds=10):
    now = timezone.now()
    return [SimpleNamespace(
        open=Decimal('101.0'), high=Decimal('102.0'), low=Decimal('100.0'),
        close=Decimal('101.5'), volume=Decimal('25'),
        timestamp=now - timedelta(seconds=age_seconds + (count - index - 1) * granularity),
        granularity=granularity,
    ) for index in range(count)]


def _dict_candles(count=200):
    now = int(timezone.now().timestamp())
    return [{
        'time': now - (count - index - 1) * 300,
        'open': 101.0, 'high': 102.0, 'low': 100.0, 'close': 101.5,
        'volume': 25,
    } for index in range(count)]


class AnalysisVerificationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            email='verification@example.com', username='verification-user',
            password='Testpass123', is_staff=True, selected_market='Volatility 75 Index',
            subscription_bypass=True,
        )
        self.risk = RiskConfig.objects.create(
            user=self.user, auto_execute=True, trading_enabled=True,
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def _bullish_vote(self, name):
        return StrategyResult(name, 'bullish', 0.9, ('confirmed fixture vote',), (), {}, 0.1)

    def _live_adapter(self):
        adapter = MagicMock()
        adapter._connected = True
        adapter.get_candles.side_effect = lambda symbol, granularity, count: [
            SimpleNamespace(
                symbol=symbol, open=Decimal('101.0'), high=Decimal('102.0'),
                low=Decimal('100.0'), close=Decimal('101.5'), volume=Decimal('25'),
                timestamp=timezone.now() - timedelta(seconds=10), granularity=granularity,
            ) for _ in range(count)
        ]
        adapter.subscribe_ticks.side_effect = lambda symbol, callback: callback(SimpleNamespace(
            symbol=symbol, timestamp=timezone.now(),
        ))
        return adapter

    @contextmanager
    def _pipeline_fixtures(self, adapter, choch_bias='bullish', strategy_bias='bullish'):
        with ExitStack() as stack:
            stack.enter_context(patch('trading_app.api_views._resolve_adapter', return_value=adapter))
            evaluators = {}
            for module, name in (
                ('support_resistance', 'Support/Resistance'), ('momentum', 'Momentum'),
                ('ict', 'ICT'), ('fibonacci', 'Fibonacci'), ('choch', 'CHoCH'),
            ):
                evaluator = stack.enter_context(patch(f'trading_app.strategies.{module}.evaluate'))
                evaluator.return_value = StrategyResult(
                    name, choch_bias if module == 'choch' else strategy_bias, 0.9,
                    (f'{name} fixture vote',), (), {}, 0.1,
                )
                evaluators[name] = evaluator
            analyze = stack.enter_context(patch('trading_app.trading_bot.technical_analyzer.analyze_technical'))
            analyze.return_value = SimpleNamespace(
                bias=strategy_bias, confidence=0.9, rationale='Read-only fixture analysis',
                rsi=65, sma_short=101.4, sma_long=101.0, atr=0.5,
                stop_loss=100.5 if strategy_bias == 'bullish' else 102.5,
                take_profit=103.5 if strategy_bias == 'bullish' else 99.5,
            )
            stack.enter_context(patch('trading_app.ml.inference.load_latest_model', return_value=None))
            consensus = stack.enter_context(patch(
                'trading_app.strategies.consensus.combine',
                wraps=__import__('trading_app.strategies.consensus', fromlist=['combine']).combine,
            ))
            execute_adapter = stack.enter_context(patch('trading_app.api_views._execute_via_adapter'))
            execute_trade = stack.enter_context(patch('trading_app.api_views._execute_trade_impl'))
            place_order = stack.enter_context(patch('trading_app.trading_bot.deriv_adapter.DerivAdapter.place_order'))
            risk_config = stack.enter_context(patch.object(RiskConfig, 'get_for_user', wraps=RiskConfig.get_for_user))
            yield {
                'evaluators': evaluators,
                'analyze': analyze,
                'consensus': consensus,
                'execute_adapter': execute_adapter,
                'execute_trade': execute_trade,
                'place_order': place_order,
                'risk_config': risk_config,
            }

    def _post_verification(self, persist=False):
        return self.client.post(reverse('api_analysis_verification'), {
            'symbol': self.user.selected_market, 'granularity': 300, 'persist': persist,
        }, format='json')

    def _assert_execution_not_reached(self, fixtures):
        fixtures['execute_adapter'].assert_not_called()
        fixtures['execute_trade'].assert_not_called()
        fixtures['place_order'].assert_not_called()
        fixtures['risk_config'].assert_not_called()

    @override_settings(DEBUG=False, TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_dev_session_rejected_when_debug_is_false(self):
        response = self.client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username__startswith=DEV_USERNAME_PREFIX).exists())

    @override_settings(DEBUG=True, TRADING_ANALYSIS_VERIFICATION_ENABLED=False)
    def test_dev_session_rejected_when_verification_flag_is_false(self):
        response = self.client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username__startswith=DEV_USERNAME_PREFIX).exists())

    @override_settings(DEBUG=True, TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_dev_session_does_not_overwrite_existing_user_collision(self):
        collision = User.objects.create_user(
            email=f'{DEV_USERNAME_PREFIX}collision@dev-verification.invalid',
            username=f'{DEV_USERNAME_PREFIX}collision',
            password=secrets.token_urlsafe(24),
            is_staff=False,
            selected_market='EUR/USD',
        )
        original_password_hash = collision.password
        original_values = (collision.is_staff, collision.is_superuser, collision.selected_market)
        with patch('trading_app.services.dev_verification_auth.uuid.uuid4', return_value=SimpleNamespace(hex='collision')):
            response = self.client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 409)
        collision.refresh_from_db()
        self.assertEqual(collision.password, original_password_hash)
        self.assertEqual(
            (collision.is_staff, collision.is_superuser, collision.selected_market),
            original_values,
        )

    @override_settings(DEBUG=True, TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_dev_session_identity_is_minimum_privilege_and_cleans_up(self):
        client = APIClient()
        response = client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 201, response.content)
        created = User.objects.get(username__startswith=DEV_USERNAME_PREFIX)
        self.assertTrue(created.is_staff)
        self.assertFalse(created.is_superuser)
        self.assertTrue(created.is_active)
        self.assertFalse(created.has_usable_password())
        self.assertFalse(created.subscription_bypass)
        self.assertEqual(created.selected_market, 'Volatility 75 Index')
        self.assertEqual(created.broker, '')
        self.assertEqual(created.broker_api_key, '')
        self.assertEqual(created.broker_api_secret, '')
        self.assertEqual(created.broker_account_id, '')
        self.assertNotEqual(created.pk, self.user.pk)
        cleanup = client.post(reverse('api_dev_verification_session_cleanup'), HTTP_HOST='localhost')
        self.assertEqual(cleanup.status_code, 200, cleanup.content)
        self.assertFalse(User.objects.filter(pk=created.pk).exists())

    @override_settings(DEBUG=True, TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_dev_auth_session_reaches_verification_and_is_scoped(self):
        session_client = APIClient()
        issued = session_client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(issued.status_code, 201, issued.content)
        self.assertIn('sessionid', session_client.cookies)
        temporary_user = User.objects.get(username__startswith=DEV_USERNAME_PREFIX)
        adapter = self._live_adapter()

        with self._pipeline_fixtures(adapter) as fixtures:
            response = session_client.post(reverse('api_analysis_verification'), {
                'symbol': 'Volatility 75 Index', 'granularity': 300, 'persist': False,
            }, format='json', HTTP_HOST='localhost')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['analysis']['verification_mode'], 'read_only')
        self.assertFalse(response.json()['persisted'])
        self.assertFalse(TradingSignal.objects.filter(user=temporary_user).exists())
        self._assert_execution_not_reached(fixtures)
        denied = session_client.get(reverse('api_subscription'), HTTP_HOST='localhost')
        self.assertEqual(denied.status_code, 403)
        cleaned = session_client.post(reverse('api_dev_verification_session_cleanup'), HTTP_HOST='localhost')
        self.assertEqual(cleaned.status_code, 200)
        self.assertFalse(User.objects.filter(pk=temporary_user.pk).exists())

    @override_settings(DEBUG=True, TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_dev_session_issuer_rejects_non_loopback_client(self):
        client = APIClient(REMOTE_ADDR='192.0.2.10')
        response = client.post(reverse('api_dev_verification_session'), HTTP_HOST='localhost')
        self.assertEqual(response.status_code, 404)
        self.assertFalse(User.objects.filter(username__startswith=DEV_USERNAME_PREFIX).exists())

    @override_settings(
        TRADING_ANALYSIS_VERIFICATION_ENABLED=True,
        MIN_STRATEGY_VOTES=3,
        STRATEGY_CONSENSUS_THRESHOLD=0.7,
    )
    def test_complete_read_only_pipeline_returns_preview_without_persisting(self):
        adapter = self._live_adapter()
        with self._pipeline_fixtures(adapter) as fixtures:
            response = self._post_verification(persist=False)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['analysis']['data_source'], 'live_deriv')
        self.assertEqual(body['analysis']['verification_mode'], 'read_only')
        self.assertEqual(body['analysis']['bias'], 'bullish')
        self.assertGreater(body['analysis']['confidence'], 0)
        self.assertEqual(body['analysis']['consensus']['directional_votes'], 6)
        self.assertEqual(body['analysis']['consensus']['buy_votes'], 6)
        self.assertEqual(body['analysis']['consensus']['sell_votes'], 0)
        strategy_names = {item['name'] for item in body['analysis']['strategies']}
        self.assertTrue({
            'Fibonacci', 'ICT', 'Momentum', 'Support/Resistance', 'CHoCH', 'Technical/SMC',
        } <= strategy_names)
        for evaluator in fixtures['evaluators'].values():
            evaluator.assert_called_once()
        self.assertEqual(fixtures['analyze'].call_count, 1)
        fixtures['consensus'].assert_called_once()
        choch_evaluate = fixtures['evaluators']['CHoCH']
        choch_result = next(item for item in body['analysis']['strategies'] if item['name'] == 'CHoCH')
        self.assertEqual(choch_result['bias'], 'bullish')
        choch_context = choch_evaluate.call_args.args[0]
        self.assertEqual(set(choch_context.multi_timeframe), {'H4', 'M15', 'M5', 'M1'})
        self.assertEqual(
            {key: len(value) for key, value in choch_context.multi_timeframe.items()},
            {'H4': 200, 'M15': 200, 'M5': 200, 'M1': 200},
        )
        signal_data = body['signal']
        self.assertEqual(body['decision'], 'BUY')
        self.assertEqual(signal_data['signal_type'], 'BUY')
        self.assertEqual(Decimal(signal_data['entry_price']), Decimal('101.50000'))
        self.assertEqual(Decimal(signal_data['stop_loss']), Decimal('100.50000'))
        self.assertEqual(Decimal(signal_data['take_profit']), Decimal('103.50000'))
        self.assertEqual(signal_data['reasoning'], 'Read-only fixture analysis')
        self.assertFalse(body['persisted'])
        self.assertIsNone(signal_data['id'])
        self.assertIn('total_analysis_ms', body['analysis']['timings'])
        self.assertGreaterEqual(body['analysis_duration_ms'], 0)
        self.assertEqual(response['X-Analysis-Duration-Ms'], str(body['analysis_duration_ms']))
        adapter.connect.assert_called_once_with({})
        adapter.subscribe_ticks.assert_called_once()
        self.assertEqual(adapter.get_candles.call_count, 4)
        self.assertCountEqual([call.args[1] for call in adapter.get_candles.call_args_list], [14400, 900, 300, 60])
        adapter.disconnect.assert_called_once()
        self.assertFalse(TradingSignal.objects.filter(user=self.user).exists())
        self._assert_execution_not_reached(fixtures)
        self.risk.refresh_from_db()
        self.assertTrue(self.risk.auto_execute)
        self.assertTrue(self.risk.trading_enabled)

    @override_settings(
        TRADING_ANALYSIS_VERIFICATION_ENABLED=True,
        MIN_STRATEGY_VOTES=3,
        STRATEGY_CONSENSUS_THRESHOLD=0.7,
    )
    def test_persist_true_saves_signal_fields_without_execution(self):
        adapter = self._live_adapter()
        with self._pipeline_fixtures(adapter) as fixtures:
            response = self._post_verification(persist=True)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        signal_data = body['signal']
        signal = TradingSignal.objects.get(pk=signal_data['id'])
        self.assertTrue(body['persisted'])
        self.assertEqual(signal.symbol, self.user.selected_market)
        self.assertEqual(signal.signal_type, 'BUY')
        self.assertEqual(signal.confidence, body['analysis']['confidence'])
        self.assertEqual(signal.entry_price, Decimal('101.50000'))
        self.assertEqual(signal.stop_loss, Decimal('100.50000'))
        self.assertEqual(signal.take_profit, Decimal('103.50000'))
        self.assertEqual(signal.reasoning, 'Read-only fixture analysis')
        self.assertEqual(signal.outcome, 'PENDING')
        self.assertFalse(signal.is_executed)
        self._assert_execution_not_reached(fixtures)

    @override_settings(
        TRADING_ANALYSIS_VERIFICATION_ENABLED=True,
        MIN_STRATEGY_VOTES=3,
        STRATEGY_CONSENSUS_THRESHOLD=0.7,
    )
    def test_persist_true_is_safe_when_execution_settings_are_enabled(self):
        self.risk.auto_execute = True
        self.risk.trading_enabled = True
        self.risk.save(update_fields=['auto_execute', 'trading_enabled'])
        adapter = self._live_adapter()

        with self._pipeline_fixtures(adapter) as fixtures:
            response = self._post_verification(persist=True)

        self.assertEqual(response.status_code, 200, response.content)
        signal = TradingSignal.objects.get(pk=response.json()['signal']['id'])
        self.assertEqual(signal.outcome, 'PENDING')
        self.assertFalse(signal.is_executed)
        self._assert_execution_not_reached(fixtures)
        self.risk.refresh_from_db()
        self.assertTrue(self.risk.auto_execute)
        self.assertTrue(self.risk.trading_enabled)

    def test_verification_live_fetch_uses_one_adapter_and_reuses_selected_frame(self):
        from trading_app.api_views import _fetch_verification_candles
        from trading_app.api_views import deriv_symbol_for_market

        adapter = MagicMock()
        adapter._connected = True
        adapter.get_candles.side_effect = lambda symbol, granularity, count: [
            SimpleNamespace(
                symbol=symbol, open=Decimal('101'), high=Decimal('102'), low=Decimal('100'),
                close=Decimal('101.5'), volume=Decimal('25'),
                timestamp=timezone.now() - timedelta(seconds=10), granularity=granularity,
            ) for _ in range(count)
        ]
        adapter.subscribe_ticks.side_effect = lambda symbol, callback: callback(SimpleNamespace(
            symbol=symbol, timestamp=timezone.now(),
        ))
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertIsNone(error)
        self.assertEqual(len(raw), 200)
        self.assertEqual(set(mtf), {'H4', 'M15', 'M5', 'M1'})
        self.assertEqual(adapter.get_candles.call_count, 4)
        requested = [call.args[1] for call in adapter.get_candles.call_args_list]
        self.assertCountEqual(requested, [14400, 900, 300, 60])
        adapter.connect.assert_called_once_with({})
        adapter.disconnect.assert_called_once()

    def test_verification_rejects_stale_data(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = MagicMock()
        adapter._connected = True
        adapter.get_candles.side_effect = lambda symbol, granularity, count: [
            SimpleNamespace(
                symbol=symbol, open=Decimal('101'), high=Decimal('102'), low=Decimal('100'),
                close=Decimal('101.5'), volume=Decimal('25'),
                timestamp=timezone.now() - timedelta(seconds=granularity * 2 + 180),
                granularity=granularity,
            ) for _ in range(count)
        ]
        adapter.subscribe_ticks.side_effect = lambda symbol, callback: callback(SimpleNamespace(
            symbol=symbol, timestamp=timezone.now(),
        ))
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('stale', error.lower())
        adapter.disconnect.assert_called_once()

    def test_verification_rejects_candles_for_another_market(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = self._live_adapter()
        adapter.get_candles.side_effect = lambda symbol, granularity, count: [
            SimpleNamespace(
                symbol='frxGBPUSD', open=Decimal('1'), high=Decimal('2'),
                low=Decimal('0.5'), close=Decimal('1.5'), volume=Decimal('1'),
                timestamp=timezone.now(), granularity=granularity,
            )
        ]
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, 'EUR/USD', 300)
        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('market mismatch', error.lower())

    def test_verification_ignores_wrong_symbol_and_stale_ticks(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = self._live_adapter()
        def send_bad_tick(symbol, callback):
            callback(SimpleNamespace(symbol='frxGBPUSD', timestamp=timezone.now()))
            callback(SimpleNamespace(symbol=symbol, timestamp=timezone.now() - timedelta(seconds=120)))
        adapter.subscribe_ticks.side_effect = send_bad_tick
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)
        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('no current deriv tick', error.lower())
        adapter.get_candles.assert_not_called()

    def test_verification_rejects_closed_market(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = MagicMock()
        adapter._connected = True
        adapter.subscribe_ticks.side_effect = RuntimeError('This market is presently closed.')
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('market closed', error.lower())

    def test_verification_fails_closed_when_a_required_timeframe_is_missing(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = MagicMock()
        adapter._connected = True
        adapter.get_candles.side_effect = [
            _live_candles(14400), RuntimeError('timeout'),
        ]
        adapter.subscribe_ticks.side_effect = lambda symbol, callback: callback(SimpleNamespace(
            symbol=symbol, timestamp=timezone.now(),
        ))
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('unavailable', error.lower())

    def test_verification_rejects_missing_current_tick(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = self._live_adapter()
        adapter.subscribe_ticks.side_effect = None
        adapter.subscribe_ticks.return_value = None
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter):
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('no current deriv tick', error.lower())
        adapter.get_candles.assert_not_called()
        adapter.disconnect.assert_called_once()

    def test_verification_never_uses_simulated_market_as_live_fallback(self):
        from trading_app.api_views import _fetch_verification_candles

        adapter = self._live_adapter()
        adapter.subscribe_ticks.side_effect = RuntimeError('no current tick')
        with patch('trading_app.api_views._resolve_adapter', return_value=adapter), \
                patch('trading_app.trading_bot.simulated_market.SimulatedMarket.get_candles') as simulated_candles:
            raw, mtf, error = _fetch_verification_candles(self.user, self.user.selected_market, 300)

        self.assertEqual(raw, [])
        self.assertEqual(mtf, {})
        self.assertIn('unavailable', error.lower())
        simulated_candles.assert_not_called()
        adapter.get_candles.assert_not_called()

    @override_settings(TRADING_ANALYSIS_VERIFICATION_ENABLED=True)
    def test_verification_route_is_staff_only(self):
        self.user.is_staff = False
        self.user.save(update_fields=['is_staff'])
        response = self.client.post(reverse('api_analysis_verification'), {}, format='json')
        self.assertEqual(response.status_code, 403)

    @override_settings(
        TRADING_ANALYSIS_VERIFICATION_ENABLED=True,
        MIN_STRATEGY_VOTES=3,
        STRATEGY_CONSENSUS_THRESHOLD=0.7,
    )
    def test_test_only_session_auth_reaches_internal_verification_endpoint(self):
        test_user = User.objects.create_user(
            email='verification-session-test@example.com',
            username='verification-session-test',
            password=secrets.token_urlsafe(32),
            is_staff=True,
            selected_market='Volatility 75 Index',
            subscription_bypass=True,
        )
        test_risk = RiskConfig.objects.create(
            user=test_user, auto_execute=True, trading_enabled=True,
        )
        client = APIClient()
        client.force_login(test_user)
        adapter = self._live_adapter()

        with self._pipeline_fixtures(adapter) as fixtures:
            response = client.post(reverse('api_analysis_verification'), {
                'symbol': 'Volatility 75 Index', 'granularity': 300, 'persist': False,
            }, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['analysis']['verification_mode'], 'read_only')
        self.assertEqual(response.json()['analysis']['data_source'], 'live_deriv')
        self.assertFalse(response.json()['persisted'])
        self.assertIsNone(response.json()['signal']['id'])
        self.assertTrue(test_user.is_staff)
        self.assertTrue(test_user.has_usable_password())
        self.assertFalse(TradingSignal.objects.filter(user=test_user).exists())
        self._assert_execution_not_reached(fixtures)
        test_risk.refresh_from_db()
        self.assertTrue(test_risk.auto_execute)
        self.assertTrue(test_risk.trading_enabled)

    @override_settings(TRADING_ANALYSIS_VERIFICATION_ENABLED=False)
    def test_verification_route_is_disabled_by_default(self):
        response = self.client.post(reverse('api_analysis_verification'), {}, format='json')
        self.assertEqual(response.status_code, 404)

    @patch('trading_app.api_views._execute_via_adapter')
    @patch('trading_app.api_views._fetch_chart_candles')
    @patch('trading_app.strategies.choch.fetch_multi_timeframe_candles')
    @patch('trading_app.ml.inference.load_latest_model', return_value=None)
    @patch('trading_app.trading_bot.technical_analyzer.analyze_technical')
    @patch('trading_app.strategies.choch.evaluate')
    @patch('trading_app.strategies.fibonacci.evaluate')
    @patch('trading_app.strategies.ict.evaluate')
    @patch('trading_app.strategies.momentum.evaluate')
    @patch('trading_app.strategies.support_resistance.evaluate')
    def test_normal_analysis_never_executes_even_when_execution_settings_are_enabled(
        self, sr_evaluate, momentum_evaluate, ict_evaluate, fib_evaluate,
        choch_evaluate, analyze, _model, _mtf, fetch_candles, execute,
    ):
        from django.core.cache import cache

        self.user.is_staff = False
        self.user.save(update_fields=['is_staff'])
        cache.set(f'bot_status_{self.user.id}', {'status': 'running', 'market': self.user.selected_market})
        fetch_candles.return_value = _live_candles()
        candle_dicts = _dict_candles()
        for evaluator, name in (
            (sr_evaluate, 'Support/Resistance'), (momentum_evaluate, 'Momentum'),
            (ict_evaluate, 'ICT'), (fib_evaluate, 'Fibonacci'), (choch_evaluate, 'CHoCH'),
        ):
            evaluator.return_value = self._bullish_vote(name)
        analyze.return_value = SimpleNamespace(
            bias='bullish', confidence=0.9, rationale='normal path test',
            rsi=65, sma_short=101.4, sma_long=101.0, atr=0.5,
            stop_loss=100.5, take_profit=103.5,
        )
        with patch('trading_app.api_views._candles_to_dicts', return_value=candle_dicts):
            response = self.client.post(reverse('api_analyze_signal'), {
                'symbol': self.user.selected_market, 'granularity': 300,
            }, format='json')

        self.assertEqual(response.status_code, 200, response.content)
        execute.assert_not_called()
        self.risk.refresh_from_db()
        self.assertTrue(self.risk.auto_execute)
        self.assertTrue(self.risk.trading_enabled)

    @override_settings(
        TRADING_ANALYSIS_VERIFICATION_ENABLED=True,
        MIN_STRATEGY_VOTES=3,
        STRATEGY_CONSENSUS_THRESHOLD=0.7,
    )
    def test_single_opposing_strategy_returns_wait(self):
        adapter = self._live_adapter()
        with self._pipeline_fixtures(adapter, choch_bias='bearish'):
            response = self._post_verification(persist=False)

        self.assertEqual(response.status_code, 200, response.content)
        body = response.json()
        self.assertEqual(body['decision'], 'WAIT')
        self.assertIsNone(body['signal'])
        self.assertGreater(body['wait_seconds_remaining'], 0)
        self.assertEqual(body['analysis']['consensus']['reason'], 'No consensus')
        self.assertFalse(self.user.analysis_wait_cycles.exists())
        self.assertEqual(
            (timezone.datetime.fromisoformat(body['wait_expires_at']) - timezone.datetime.fromisoformat(body['wait_started_at'])).total_seconds(),
            180,
        )
        status_before_refresh = self.client.get(reverse('api_analysis_wait_status'), {
            'symbol': self.user.selected_market, 'granularity': 300,
        }).json()
        status_after_refresh = self.client.get(reverse('api_analysis_wait_status'), {
            'symbol': self.user.selected_market, 'granularity': 300,
        }).json()
        self.assertEqual(status_before_refresh['wait_expires_at'], status_after_refresh['wait_expires_at'])

    def test_production_wait_cycle_persists_and_restarts_after_expiry(self):
        from datetime import timedelta
        from trading_app.models import AnalysisWaitCycle
        from trading_app.api_views import _wait_response

        cycle = AnalysisWaitCycle.objects.create(
            user=self.user, symbol=self.user.selected_market, granularity=300,
            wait_started_at=timezone.now() - timedelta(seconds=181),
            wait_expires_at=timezone.now() - timedelta(seconds=1),
        )
        response = _wait_response(self.user, self.user.selected_market, 300, 'No confluence')
        self.assertEqual(response.data['decision'], 'WAIT')
        cycle.refresh_from_db()
        self.assertGreater(cycle.wait_started_at, timezone.now() - timedelta(seconds=10))
        self.assertEqual((cycle.wait_expires_at - cycle.wait_started_at).total_seconds(), 180)
        status = self.client.get(reverse('api_analysis_wait_status'), {
            'symbol': self.user.selected_market, 'granularity': 300,
        }).json()
        self.assertEqual(status['wait_expires_at'], cycle.wait_expires_at.isoformat())

    def test_expired_wait_status_triggers_fresh_analysis_and_new_wait_cycle(self):
        from datetime import timedelta
        from django.core.cache import cache
        from trading_app.models import AnalysisWaitCycle

        self.user.subscription_bypass = True
        self.user.save(update_fields=['subscription_bypass'])
        cache.set(f'bot_status_{self.user.pk}', {'status': 'running', 'market': self.user.selected_market})
        expired_at = timezone.now() - timedelta(seconds=1)
        AnalysisWaitCycle.objects.create(
            user=self.user, symbol=self.user.selected_market, granularity=300,
            wait_started_at=expired_at - timedelta(seconds=180), wait_expires_at=expired_at,
        )
        adapter = self._live_adapter()
        with self._pipeline_fixtures(adapter, choch_bias='bearish'):
            response = self.client.get(reverse('api_analysis_wait_status'), {
                'symbol': self.user.selected_market, 'granularity': 300,
            })

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['decision'], 'WAIT')
        cycle = AnalysisWaitCycle.objects.get(user=self.user, symbol=self.user.selected_market, granularity=300)
        self.assertGreater(cycle.wait_started_at, expired_at)
        self.assertEqual((cycle.wait_expires_at - cycle.wait_started_at).total_seconds(), 180)

    def test_expired_wait_does_not_choose_a_direction_without_fresh_confluence(self):
        from datetime import timedelta
        from django.core.cache import cache
        from trading_app.models import AnalysisWaitCycle

        cache.set(f'bot_status_{self.user.pk}', {'status': 'running', 'market': self.user.selected_market})
        AnalysisWaitCycle.objects.create(
            user=self.user, symbol=self.user.selected_market, granularity=300,
            wait_started_at=timezone.now() - timedelta(seconds=181),
            wait_expires_at=timezone.now() - timedelta(seconds=1),
        )
        adapter = self._live_adapter()
        with self._pipeline_fixtures(adapter, choch_bias='bearish'):
            response = self.client.get(reverse('api_analysis_wait_status'), {
                'symbol': self.user.selected_market, 'granularity': 300,
            })
        self.assertEqual(response.json()['decision'], 'WAIT')
        self.assertIsNone(response.json()['signal'])

    def test_expired_wait_can_return_fresh_bullish_or_bearish_confluence(self):
        from datetime import timedelta
        from django.core.cache import cache
        from trading_app.models import AnalysisWaitCycle

        for direction in ('bullish', 'bearish'):
            with self.subTest(direction=direction):
                AnalysisWaitCycle.objects.update_or_create(
                    user=self.user, symbol=self.user.selected_market, granularity=300,
                    defaults={
                        'wait_started_at': timezone.now() - timedelta(seconds=181),
                        'wait_expires_at': timezone.now() - timedelta(seconds=1),
                    },
                )
                cache.set(f'bot_status_{self.user.pk}', {'status': 'running', 'market': self.user.selected_market})
                adapter = self._live_adapter()
                with self._pipeline_fixtures(
                    adapter, choch_bias=direction, strategy_bias=direction,
                ):
                    with patch('trading_app.api_views._predict_from_existing_candles', return_value=None):
                        response = self.client.get(reverse('api_analysis_wait_status'), {
                            'symbol': self.user.selected_market, 'granularity': 300,
                        })
                expected = 'BUY' if direction == 'bullish' else 'SELL'
                self.assertEqual(response.json()['decision'], expected)
                AnalysisWaitCycle.objects.filter(user=self.user).delete()

    def test_ml_direction_alone_cannot_override_conflicting_strategies(self):
        adapter = self._live_adapter()
        bearish_ml = SimpleNamespace(
            bias='bearish', confidence=0.99, model_info='fixture',
            features={}, probability=0.01, horizon=4,
        )
        with self._pipeline_fixtures(adapter, choch_bias='bearish'):
            with patch('trading_app.api_views._predict_from_existing_candles', return_value=bearish_ml):
                response = self._post_verification(persist=False)
        self.assertEqual(response.json()['decision'], 'WAIT')
        self.assertIsNone(response.json()['signal'])