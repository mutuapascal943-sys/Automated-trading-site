import asyncio
import json
from unittest import TestCase
from unittest.mock import AsyncMock, patch

from trading_app.ml.data_collector import _fetch_all
from trading_app.services.adapter_resolver import build_credentials
from trading_app.services.ticker_bridge import TickerBridge
from trading_app.trading_bot.deriv_adapter import DerivAdapter


class _FakeWebSocket:
    def __init__(self):
        self.sent = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False

    async def send(self, payload):
        self.sent.append(json.loads(payload))

    async def recv(self):
        return json.dumps({'msg_type': 'tick', 'subscription': {'id': 'sub-1'}})

    async def __aiter__(self):
        yield json.dumps({
            'msg_type': 'tick',
            'tick': {'epoch': 1_728_000_000, 'quote': 1.23456},
        })


class _FakeHistoryWebSocket(_FakeWebSocket):
    async def recv(self):
        return json.dumps({
            'req_id': 1,
            'candles': [{
                'open': '1.1', 'high': '1.2', 'low': '1.0',
                'close': '1.15', 'volume': 10, 'epoch': 1_728_000_000,
            }],
        })


class DerivPublicFeedTests(TestCase):
    def test_adapter_credentials_ignore_account_and_market_tokens(self):
        user = type('User', (), {
            'broker_api_key': 'account-key',
            'broker_api_secret': 'account-secret',
            'broker_account_id': 'account-id',
        })()

        self.assertEqual(build_credentials(user), {})

    def test_adapter_uses_app_id_public_websocket_url(self):
        adapter = DerivAdapter(app_id='test-app')

        self.assertEqual(
            adapter._ws_url,
            'wss://ws.derivws.com/websockets/v3?app_id=test-app',
        )

    def test_ticker_forwards_public_tick_without_authorization(self):
        websocket = _FakeWebSocket()
        channel_layer = type('ChannelLayer', (), {'group_send': AsyncMock()})()

        with patch('websockets.connect', return_value=websocket) as connect, \
                patch('trading_app.services.ticker_bridge.get_channel_layer', return_value=channel_layer):
            asyncio.run(TickerBridge._live_tick_loop('EUR/USD', {}, 'test-app'))

        connect.assert_called_once_with(
            'wss://ws.derivws.com/websockets/v3?app_id=test-app',
            ping_interval=30,
            ping_timeout=10,
        )
        self.assertEqual(len(websocket.sent), 1)
        self.assertEqual(websocket.sent[0]['ticks'], 'frxEURUSD')
        self.assertNotIn('authorize', websocket.sent[0])
        channel_layer.group_send.assert_awaited_once()
        event = channel_layer.group_send.await_args.args[1]['data']
        self.assertEqual(event['type'], 'tick')
        self.assertEqual(event['price'], 1.23456)

    def test_candle_history_uses_public_connection_and_returns_live_candles(self):
        websocket = _FakeHistoryWebSocket()

        with patch('websockets.connect', return_value=websocket) as connect:
            candles = asyncio.run(_fetch_all(
                'frxEURUSD', 60, 1, 1_728_000_100, 'test-app',
            ))

        connect.assert_called_once_with(
            'wss://ws.derivws.com/websockets/v3?app_id=test-app',
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]['close'], 1.15)
        self.assertEqual(websocket.sent[0]['ticks_history'], 'frxEURUSD')