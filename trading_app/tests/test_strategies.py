from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from threading import Event, Lock
from unittest import TestCase
from unittest.mock import patch

from trading_app.strategies.base import CandleContext, StrategyResult, prepare_context
from trading_app.strategies.consensus import combine
from trading_app.strategies.momentum import evaluate as evaluate_momentum
from trading_app.strategies.support_resistance import evaluate as evaluate_support_resistance
from trading_app.strategies.fibonacci import evaluate as evaluate_fibonacci
from trading_app.strategies.ict import evaluate as evaluate_ict
from trading_app.strategies.choch import (
    check_news_window,
    evaluate as evaluate_choch,
    fetch_multi_timeframe_candles,
)
from trading_app.strategies.runner import run_parallel


def _bars(closes, *, volumes=None, start=1_700_000_000, interval=300):
    volumes = volumes or [100.0] * len(closes)
    result = []
    for index, (close, volume) in enumerate(zip(closes, volumes)):
        close = float(close)
        result.append({
            'time': start + index * interval,
            'open': close - 0.1,
            'high': close + 0.2,
            'low': close - 0.2,
            'close': close,
            'volume': float(volume),
        })
    return result


class SourceStrategyTests(TestCase):
    def test_momentum_evaluator_reports_neutral_with_insufficient_bars(self):
        result = evaluate_momentum(prepare_context(_bars([1, 2, 3])))
        self.assertEqual(result.bias, 'neutral')
        self.assertIn('insufficient_candles', result.conditions)

    def test_momentum_source_rule_rsi_above_50_is_bullish(self):
        closes = [100 + i for i in range(60)]
        result = evaluate_momentum(prepare_context(_bars(closes)))
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('rsi_above_50', result.conditions)

    def test_momentum_breakout_requires_source_volume_confirmation(self):
        closes = [100.0] * 55 + [102.0]
        volumes = [100.0] * 55 + [160.0]
        result = evaluate_momentum(prepare_context(_bars(closes, volumes=volumes)))
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('resistance_breakout_volume_1_5x', result.conditions)

    def test_support_resistance_bounce_rule_and_no_zone_neutral(self):
        context = CandleContext(
            candles=[], featured=[],
            current={'close': 100.2, 'high': 100.5, 'low': 99.9, 'volume': 100},
            previous={'close': 99.8, 'high': 100, 'low': 99.4, 'volume': 100},
            support=100.0, resistance=110.0,
            support_touches=3, resistance_touches=1,
            zone_tolerance=0.5, preprocessing_ms=0.1,
        )
        result = evaluate_support_resistance(context)
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('repeated_support_tests', result.conditions)
        neutral = evaluate_support_resistance(CandleContext(
            [], [], {'close': 105, 'high': 105, 'low': 104, 'volume': 1},
            {'close': 104, 'high': 105, 'low': 104, 'volume': 1},
            None, None, 0, 0, 0.1, 0.1,
        ))
        self.assertEqual(neutral.bias, 'neutral')

    def test_support_resistance_breakout_requires_volume_and_momentum(self):
        context = CandleContext(
            candles=[{'volume': 100}] * 20 + [{'volume': 200}],
            featured=[],
            current={'close': 111.0, 'high': 111.2, 'low': 110.5, 'volume': 200},
            previous={'close': 109.5, 'high': 110.0, 'low': 109.0, 'volume': 100},
            support=90.0, resistance=110.0,
            support_touches=1, resistance_touches=3,
            zone_tolerance=0.25, preprocessing_ms=0.1,
        )
        result = evaluate_support_resistance(context)
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('volume_confirmed', result.conditions)

    def test_fibonacci_retracement_uses_recent_swing_and_confirmation(self):
        candles = _bars([100 + index for index in range(30)] + [128, 126, 124, 122, 120, 118, 116, 115])
        context = prepare_context(candles)
        result = evaluate_fibonacci(context)
        self.assertIn(result.bias, ('bullish', 'neutral'))
        self.assertIn('retracement_0.618', result.levels)
        self.assertIn('retracement_0.786', result.levels)

    def test_fibonacci_symmetric_downtrend_produces_bearish_or_neutral_not_buy(self):
        candles = _bars([130 - index for index in range(30)] + [102, 104, 106, 108, 110, 112, 114, 115])
        result = evaluate_fibonacci(prepare_context(candles))
        self.assertIn(result.bias, ('bearish', 'neutral'))

    def test_ict_requires_sweep_displacement_and_structure_confirmation(self):
        candles = _bars([100 + ((i % 8) * 0.2) for i in range(55)])
        for index in range(40, 49):
            price = 100.0
            candles[index] = {
                'time': candles[index]['time'], 'open': price - 0.1, 'high': price + 0.2,
                'low': price - 0.2, 'close': price, 'volume': 100,
            }
        candles[-6] = {
            'time': candles[-6]['time'], 'open': 100.0, 'high': 100.1,
            'low': 97.0, 'close': 100.0, 'volume': 150,
        }
        candles[-5] = {
            'time': candles[-5]['time'], 'open': 100.0, 'high': 100.05,
            'low': 99.8, 'close': 100.0, 'volume': 100,
        }
        for index in range(51, 54):
            price = 100.0
            candles[index] = {
                'time': candles[index]['time'], 'open': price - 0.1, 'high': price + 0.2,
                'low': price - 0.2, 'close': price, 'volume': 100,
            }
        candles[-1] = {
            'time': candles[-1]['time'], 'open': 100.1, 'high': 104.0,
            'low': 100.0, 'close': 103.9, 'volume': 200,
        }
        result = evaluate_ict(prepare_context(candles))
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('single_candle_htf_sweep', result.conditions)
        self.assertIn('ltf_choch_confirmed', result.conditions)

    def test_ict_without_following_structure_confirmation_is_neutral(self):
        candles = _bars([100.0] * 55)
        candles[-6] = {
            'time': candles[-6]['time'], 'open': 100.0, 'high': 100.1,
            'low': 97.0, 'close': 100.0, 'volume': 150,
        }
        candles[-1] = {
            'time': candles[-1]['time'], 'open': 100.0, 'high': 100.1,
            'low': 99.8, 'close': 100.0, 'volume': 100,
        }
        result = evaluate_ict(prepare_context(candles))
        self.assertEqual(result.bias, 'neutral')

    def test_ict_buy_side_sweep_can_confirm_bearish(self):
        candles = _bars([100.0] * 55)
        candles[-4] = {
            'time': candles[-4]['time'], 'open': 100.0, 'high': 104.0,
            'low': 99.9, 'close': 100.0, 'volume': 150,
        }
        candles[-3] = {
            'time': candles[-3]['time'], 'open': 100.0, 'high': 100.05,
            'low': 99.95, 'close': 100.0, 'volume': 100,
        }
        for index in range(45, 49):
            price = 100.0
            candles[index] = {
                'time': candles[index]['time'], 'open': price + 0.1,
                'high': price + 0.2, 'low': price - 0.2,
                'close': price, 'volume': 100,
            }
        candles[-1] = {
            'time': candles[-1]['time'], 'open': 99.9, 'high': 100.0,
            'low': 96.0, 'close': 96.1, 'volume': 200,
        }
        result = evaluate_ict(prepare_context(candles))
        self.assertEqual(result.bias, 'bearish')
        self.assertIn('bearish_displacement', result.conditions)
        self.assertIn('ltf_choch_confirmed', result.conditions)

    def test_choch_bullish_context_and_m1_confirmation(self):
        m1 = _bars([101.0, 101.1, 101.2, 101.3, 101.5, 101.7, 101.9, 102.2, 102.4, 102.8], interval=60)
        candles = _bars([100.0, 100.6, 101.3, 101.9, 102.1, 102.8, 103.2, 103.7, 104.0, 104.4], interval=300)
        context = prepare_context(candles)
        context.multi_timeframe = {'H4': candles, 'M15': candles, 'M5': candles, 'M1': m1}
        result = evaluate_choch(context)
        self.assertEqual(result.bias, 'bullish')
        self.assertIn('h4_context_bullish', result.conditions)
        self.assertIn('m1_confirmation', result.conditions)

    def test_choch_bearish_context_and_zone_flip(self):
        m1 = _bars([100.8, 100.7, 100.5, 100.3, 100.1, 99.9, 99.6, 99.4, 99.2, 99.0], interval=60)
        candles = _bars([108.0, 107.2, 106.5, 105.8, 104.9, 104.1, 103.2, 102.8, 101.8, 101.0], interval=300)
        context = prepare_context(candles)
        context.multi_timeframe = {'H4': candles, 'M15': candles, 'M5': candles, 'M1': m1}
        result = evaluate_choch(context)
        self.assertEqual(result.bias, 'bearish')
        self.assertIn('supply_demand_flip', result.conditions)

    def test_choch_rejects_limit_entry_from_unmitigated_zone(self):
        candles = _bars([100.0, 100.2, 100.4, 100.8, 101.0, 100.9, 100.6, 100.1, 99.8, 99.6], interval=300)
        context = prepare_context(candles)
        context.multi_timeframe = {'H4': candles, 'M15': candles, 'M5': candles, 'M1': candles}
        result = evaluate_choch(context)
        self.assertEqual(result.bias, 'neutral')
        self.assertIn('unmitigated_zone_limit_entry', result.conditions)

    def test_choch_news_filter_reports_unavailable_without_provider(self):
        status = check_news_window('EUR/USD', 'M1')
        self.assertEqual(status['status'], 'unavailable')
        self.assertIn('unavailable', status['message'].lower())

    def test_choch_fetch_multi_timeframe_candles_uses_requested_timeframes(self):
        with patch('trading_app.strategies.choch._request_timeframe_candles', side_effect=[
            [{'close': 1.0}], [{'close': 2.0}], [{'close': 3.0}], [{'close': 4.0}],
        ]):
            data = fetch_multi_timeframe_candles('EUR/USD', (14400, 900, 300, 60), user=None)
        self.assertEqual(set(data), {'H4', 'M15', 'M5', 'M1'})
        self.assertEqual(data['M1'][0]['close'], 4.0)


class ConsensusTests(TestCase):
    def _result(self, name, bias):
        return StrategyResult(name, bias, 0.8, (), (), {}, 0.1)

    def test_consensus_counts_only_directional_votes(self):
        result = combine([
            self._result('a', 'bullish'), self._result('b', 'bullish'),
            self._result('c', 'bullish'), self._result('d', 'neutral'),
        ], threshold=0.70, minimum_votes=3)
        self.assertEqual(result.bias, 'bullish')
        self.assertEqual((result.buy_votes, result.sell_votes, result.total_directional_votes), (3, 0, 3))

    def test_insufficient_or_conflicting_consensus_is_neutral(self):
        results = [self._result('a', 'bullish'), self._result('b', 'bearish'), self._result('c', 'neutral')]
        result = combine(results, threshold=0.70, minimum_votes=2)
        self.assertEqual(result.bias, 'neutral')
        self.assertEqual(result.reason, 'No consensus')
        low_votes = combine([self._result('only', 'bullish')], threshold=0.70, minimum_votes=3)
        self.assertEqual(low_votes.bias, 'neutral')

    def test_runner_runs_independent_evaluators_concurrently_and_shares_context(self):
        context = object()
        entered = []
        lock = Lock()
        release = Event()

        def evaluator(name):
            def run(received_context):
                self.assertIs(received_context, context)
                with lock:
                    entered.append(name)
                release.wait(timeout=1)
                return self._result(name, 'bullish')
            return run

        def release_when_both_entered():
            deadline = time.monotonic() + 1
            while time.monotonic() < deadline:
                with lock:
                    if len(entered) == 2:
                        release.set()
                        return
                time.sleep(0.005)

        from threading import Thread
        releaser = Thread(target=release_when_both_entered)
        releaser.start()
        results, timings = run_parallel(context, {'first': evaluator('first'), 'second': evaluator('second')})
        releaser.join(timeout=1)
        self.assertEqual(set(entered), {'first', 'second'})
        self.assertEqual(len(results), 2)
        self.assertEqual(set(timings), {'first', 'second'})


class CandleReuseTests(TestCase):
    def test_preprocessing_reuses_input_and_adds_indicator_context(self):
        candles = _bars([100 + index for index in range(60)])
        context = prepare_context(candles)
        self.assertIs(context.candles, candles)
        self.assertIn('rsi_14', context.current)
        self.assertGreaterEqual(context.preprocessing_ms, 0)