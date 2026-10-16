import asyncio
import json
from decimal import Decimal
from datetime import datetime, timezone
from unittest.mock import patch
from django.test import TestCase, Client
from django.contrib.auth import get_user_model
from django.urls import reverse
from trading_app.trading_bot.symbol_map import load_symbol_map
from trading_app.ml.symbols import deriv_symbol_for
from trading_app.trading_bot.deriv_adapter import DerivAdapter
from trading_app.services.cache_service import CacheService

User = get_user_model()


class MarketFeedAndAdapterTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user(username="testtrader", email="trader@test.com", password="password123")
        self.client = Client()

    def test_symbol_mapping_to_deriv(self):
        sm = load_symbol_map()
        self.assertEqual(sm.to_broker("EURUSD", "deriv"), "frxEURUSD")
        self.assertEqual(sm.to_broker("BTCUSD", "deriv"), "cryBTCUSD")
        self.assertEqual(sm.to_broker("XAUUSD", "deriv"), "frxXAUUSD")
        self.assertEqual(deriv_symbol_for("EURUSD"), "frxEURUSD")
        self.assertEqual(deriv_symbol_for("BTCUSD"), "cryBTCUSD")

    def test_tick_validation_drops_invalid_ticks(self):
        adapter = DerivAdapter(app_id="test")
        adapter._connected = True
        received = []
        with patch.object(adapter, '_call_api', return_value=object()), \
             patch.object(adapter, '_await_future', return_value={}):
            adapter.subscribe_ticks("frxEURUSD", lambda t: received.append(t))

        # Valid tick
        asyncio.run(adapter._handle_message(json.dumps({
            "msg_type": "tick",
            "tick": {"symbol": "frxEURUSD", "quote": 1.1234, "epoch": 1700000000}
        })))
        self.assertEqual(len(received), 1)

        # Invalid tick: non-numeric quote
        asyncio.run(adapter._handle_message(json.dumps({
            "msg_type": "tick",
            "tick": {"symbol": "frxEURUSD", "quote": "invalid", "epoch": 1700000001}
        })))
        self.assertEqual(len(received), 1)  # dropped

        # Invalid tick: decreasing epoch
        asyncio.run(adapter._handle_message(json.dumps({
            "msg_type": "tick",
            "tick": {"symbol": "frxEURUSD", "quote": 1.1235, "epoch": 1699999999}
        })))
        self.assertEqual(len(received), 1)  # dropped

    def test_cache_candles_retention_limit(self):
        candles = [{"open": 1.0, "high": 1.1, "low": 0.9, "close": 1.05, "volume": 100, "time": i} for i in range(600)]
        CacheService.set_candles("EURUSD", 300, candles)
        cached = CacheService.get_candles("EURUSD", 300)
        self.assertLessEqual(len(cached), 500)

    def test_market_health_endpoint(self):
        self.client.force_login(self.user)
        response = self.client.get(reverse('api_market_health'), {'symbol': 'EUR/USD', 'granularity': 300})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn('status', data)
        self.assertIn('rejection_reasons', data)
        self.assertIn('connection', data)
