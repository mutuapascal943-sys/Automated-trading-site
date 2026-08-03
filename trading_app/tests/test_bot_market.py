from django.test import TestCase
from django.urls import reverse
from django.contrib.auth import get_user_model
from rest_framework.test import APIClient


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

    def test_chart_candles_60s(self):
        r = self.client.get('/api/chart/candles/', {'symbol': 'BTC/USD', 'granularity': 60, 'count': 200})
        self.assertEqual(r.status_code, 200, r.content)
        data = r.json()
        candles = data.get('candles') or data.get('data') or []
        self.assertGreaterEqual(len(candles), 100)

    def test_bot_market_requires_auth(self):
        c = APIClient()
        r = c.get('/api/bot/market/')
        self.assertEqual(r.status_code, 403)
