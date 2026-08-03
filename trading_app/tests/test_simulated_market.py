from django.test import SimpleTestCase

from trading_app.trading_bot.simulated_market import BASE_PRICES, SimulatedMarket


class SimulatedMarketTests(SimpleTestCase):
    def test_prices_stay_realistic_around_base(self):
        p = float(SimulatedMarket.current_price("EUR/USD"))
        self.assertGreater(p, 0.5)
        self.assertLess(p, 2.0)

    def test_ticks_advance(self):
        before = float(SimulatedMarket.current_price("EUR/USD"))
        t1 = SimulatedMarket.next_tick("EUR/USD")
        t2 = SimulatedMarket.next_tick("EUR/USD")
        self.assertIsNotNone(t1)
        self.assertIsNotNone(t2)
        after = float(SimulatedMarket.current_price("EUR/USD"))
        self.assertNotEqual(before, after)
        self.assertNotEqual(float(t1.close), float(t2.close))

    def test_deterministic_across_calls(self):
        SimulatedMarket._series = {}
        first = float(SimulatedMarket.current_price("EUR/USD"))
        SimulatedMarket._series = {}
        second = float(SimulatedMarket.current_price("EUR/USD"))
        self.assertEqual(first, second)

    def test_candles_end_near_current_price(self):
        candles = SimulatedMarket.get_candles("AUD/USD", 60, 5)
        self.assertEqual(len(candles), 5)
        self.assertGreater(float(candles[-1].close), 0.1)
        self.assertLess(float(candles[-1].close), 10.0)

    def test_all_panel_symbols_have_base_prices(self):
        for sym in BASE_PRICES:
            self.assertGreater(float(SimulatedMarket.current_price(sym)), 0.0)
