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