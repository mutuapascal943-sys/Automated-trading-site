from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import MagicMock, patch

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient

from trading_app.trading_bot.interface import Candle


class BotMarketAPITests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username='smoketest', password='pass1234', paper_mode=True
        )
        self.client = APIClient()
        self.client.force_authenticate(user=self.user)

    def test_get_default_market(self):
        r = self.client.get('/api/bot/market/')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['market'], 'EUR/USD')

    def test_post_and_get_market(self):
        r = self.client.post('/api/bot/market/', {'market': 'BTC/USD'}, format='json')
        self.assertEqual(r.status_code, 200, r.content)
        self.assertEqual(r.json()['market'], 'BTC/USD')
        r = self.client.get('/api/bot/market/')
        self.assertEqual(r.json()['market'], 'BTC/USD')
        self.user.refresh_from_db()
        self.assertEqual(self.user.selected_market, 'BTC/USD')

    @patch('trading_app.api_views._fetch_chart_candles_with_source')
    def test_chart_candles_60s(self, fetch_candles):
        now = datetime.now(timezone.utc)
        fetch_candles.return_value = ([Candle(
            symbol='R_BTC', open=Decimal('60000'), high=Decimal('60100'),
            low=Decimal('59900'), close=Decimal('60050'), volume=Decimal('10'),
            timestamp=now, granularity=60,
        )], 'live')
        r = self.client.get('/api/chart/candles/', {'symbol': 'BTC/USD', 'granularity': 60, 'count': 200})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        candles = data.get('candles') or data.get('data') or []
        self.assertEqual(data['source'], 'live')
        self.assertEqual(len(candles), 1)

    @patch('trading_app.api_views.CacheService.set_candles')
    @patch('trading_app.api_views.CacheService.get_candles', return_value=None)
    @patch('trading_app.api_views.build_credentials', return_value={'token': 'test-token'})
    @patch('trading_app.api_views._resolve_adapter')
    def test_chart_uses_deriv_symbols_for_paper_analysis(
        self, resolve_adapter, _credentials, _get_candles, _set_candles
    ):
        adapter = MagicMock()
        adapter.get_candles.return_value = [Candle(
            symbol='frxEURUSD', open=Decimal('1.08'), high=Decimal('1.09'),
            low=Decimal('1.07'), close=Decimal('1.085'), volume=Decimal('10'),
            timestamp=datetime.now(timezone.utc), granularity=3600,
        )]
        resolve_adapter.return_value = adapter

        response = self.client.get('/api/chart/candles/', {
            'symbol': 'EUR/USD', 'granularity': 3600, 'count': 200,
        })

        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.json()['source'], 'live')
        adapter.get_candles.assert_called_once_with('frxEURUSD', 3600, 200)
        adapter.disconnect.assert_called_once()

    def test_chart_rejects_unsupported_timeframe(self):
        response = self.client.get('/api/chart/candles/', {
            'symbol': 'EUR/USD', 'granularity': 123,
        })
        self.assertEqual(response.status_code, 400)

    @patch('trading_app.api_views.CacheService.get_candles', return_value=None)
    @patch('trading_app.api_views._resolve_adapter')
    def test_chart_reports_unavailable_without_deriv_data(self, resolve_adapter, _get_candles):
        adapter = resolve_adapter.return_value
        adapter.get_candles.side_effect = TimeoutError('Deriv unavailable')

        response = self.client.get('/api/chart/candles/', {
            'symbol': 'EUR/USD', 'granularity': 900, 'count': 200,
        })

        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json()['error'], 'Live market data unavailable')
        self.assertEqual(response.json()['candles'], [])

    def test_admin_login_is_available_from_auth_screens(self):
        login_page = self.client.get(reverse('login'))
        self.assertEqual(login_page.status_code, 200)
        self.assertContains(login_page, reverse('owner_admin:login'))

        admin_page = self.client.get(reverse('owner_admin:login'))
        self.assertEqual(admin_page.status_code, 302)
        self.assertIn(reverse('login'), admin_page['Location'])

    def test_bot_market_requires_auth(self):
        c = APIClient()
        r = c.get('/api/bot/market/')
        self.assertEqual(r.status_code, 403)
