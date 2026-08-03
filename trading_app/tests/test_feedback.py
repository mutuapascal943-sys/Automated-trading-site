from datetime import datetime
from datetime import timezone as dt_tz
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.utils import timezone

from trading_app.models import PredictionRecord
from trading_app.tasks import _apply_prediction_outcome, _fetch_paper_prediction_candles
from trading_app.trading_bot.interface import Candle

User = get_user_model()


def _candle(epoch: int, close: float) -> Candle:
    d = Decimal(str(close))
    return Candle(
        symbol='EUR/USD', open=d, high=d, low=d, close=d,
        volume=Decimal('1'),
        timestamp=datetime.fromtimestamp(epoch, tz=dt_tz.utc),
        granularity=900,
    )


class FeedbackResolutionTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username='fb', password='x', paper_mode=True)

    def _pred(self, **kw):
        base = int(timezone.now().timestamp())
        defaults = dict(user=self.user, symbol='EUR/USD', granularity=900, horizon=4,
                        bias='bullish', confidence=0.6, probability=0.6, candle_time=base)
        defaults.update(kw)
        return PredictionRecord.objects.create(**defaults)

    def test_apply_outcome_bullish_hit(self):
        base = int(timezone.now().timestamp())
        pred = self._pred(candle_time=base)
        candles = [_candle(base + i * 900, 1.10 if i < 4 else 1.11) for i in range(6)]
        self.assertTrue(_apply_prediction_outcome(pred, candles))
        pred.refresh_from_db()
        self.assertTrue(pred.resolved)
        self.assertEqual(pred.realized_target, 1)
        self.assertTrue(pred.prediction_correct)
        self.assertEqual(pred.entry_price, Decimal('1.10000'))
        self.assertEqual(pred.exit_price, Decimal('1.11000'))

    def test_apply_outcome_bullish_miss(self):
        base = int(timezone.now().timestamp())
        pred = self._pred(candle_time=base)
        candles = [_candle(base + i * 900, 1.10 if i < 4 else 1.09) for i in range(6)]
        self.assertTrue(_apply_prediction_outcome(pred, candles))
        pred.refresh_from_db()
        self.assertEqual(pred.realized_target, 0)
        self.assertFalse(pred.prediction_correct)

    def test_apply_outcome_insufficient_data(self):
        base = int(timezone.now().timestamp())
        pred = self._pred(candle_time=base + 5000)  # no candle reaches candle_time
        candles = [_candle(base + i * 900, 1.10) for i in range(3)]
        self.assertFalse(_apply_prediction_outcome(pred, candles))

    def test_paper_candles_fetch(self):
        pred = self._pred()
        candles = _fetch_paper_prediction_candles(pred)
        self.assertGreater(len(candles), 0)

    def test_predictions_view_stats_and_history(self):
        from django.urls import reverse
        base = int(timezone.now().timestamp())
        hit = self._pred(bias='bullish', candle_time=base)
        self.assertTrue(_apply_prediction_outcome(hit, [_candle(base + i * 900, 1.10 if i < 4 else 1.11) for i in range(6)]))
        miss = self._pred(bias='bearish', candle_time=base + 50)
        self.assertTrue(_apply_prediction_outcome(miss, [_candle(base + 50 + i * 900, 1.10 if i < 4 else 1.11) for i in range(6)]))
        pending = self._pred(bias='bullish', candle_time=base + 100)

        self.client.force_login(self.user)
        resp = self.client.get(reverse('api_predictions'))
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data['stats']['correct_signals'], 1)
        self.assertEqual(data['stats']['missed_signals'], 1)
        self.assertEqual(data['stats']['pending'], 1)
        self.assertEqual(data['stats']['win_rate'], 50.0)
        self.assertEqual(len(data['history']), 3)
        statuses = [h['status'] for h in data['history']]
        self.assertIn('Correct', statuses)
        self.assertIn('Missed', statuses)
        self.assertIn('Pending', statuses)
        entry = next(h for h in data['history'] if h['status'] == 'Correct')
        self.assertEqual(entry['entry_price'], 1.1)
        self.assertEqual(entry['exit_price'], 1.11)
