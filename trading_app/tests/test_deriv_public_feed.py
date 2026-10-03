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


class _FakeAdapterWebSocket:
    def __init__(self, adapter):
        self.adapter = adapter

    async def send(self, payload):
        request = json.loads(payload)
        await self.adapter._handle_message(json.dumps({
            'req_id': request['req_id'],
            'candles': [{
                'open': '1.1', 'high': '1.2', 'low': '1.0',
                'close': '1.15', 'volume': 10, 'epoch': 1_728_000_000,
            }],
        }))


class DerivPublicFeedTests(TestCase):
    def test_adapter_credentials_ignore_account_and_market_tokens(self):
        user = type('User', (), {
            'broker_api_key': 'account-key',
            'broker_api_secret': 'account-secret',
            'broker_account_id': 'account-id',
        })()

        self.assertEqual(build_credentials(user), {})

    def test_adapter_uses_public_websocket_url(self):
        adapter = DerivAdapter(app_id='test-app')

        self.assertEqual(
            adapter._ws_url,
            'wss://api.derivws.com/trading/v1/options/ws/public',
        )

    def test_ticker_forwards_public_tick_without_authorization(self):
        websocket = _FakeWebSocket()
        channel_layer = type('ChannelLayer', (), {'group_send': AsyncMock()})()

        with patch('trading_app.api_views.deriv_symbol_for_market', return_value='1HZ100V'), \
                patch('websockets.connect', return_value=websocket) as connect, \
                patch('trading_app.services.ticker_bridge.get_channel_layer', return_value=channel_layer):
            asyncio.run(TickerBridge._live_tick_loop('1HZ100V', {}, 'test-app'))

        connect.assert_called_once_with(
            'wss://api.derivws.com/trading/v1/options/ws/public',
            ping_interval=30,
            ping_timeout=10,
        )
        self.assertEqual(len(websocket.sent), 1)
        self.assertEqual(websocket.sent[0]['ticks'], '1HZ100V')
        self.assertNotIn('authorize', websocket.sent[0])
        channel_layer.group_send.assert_awaited_once()
        event = channel_layer.group_send.await_args.args[1]['data']
        self.assertEqual(event['type'], 'tick')
        self.assertEqual(event['price'], 1.23456)

    def test_candle_history_uses_public_connection_and_returns_live_candles(self):
        websocket = _FakeHistoryWebSocket()

        with patch('websockets.connect', return_value=websocket) as connect:
            candles = asyncio.run(_fetch_all(
                '1HZ100V', 60, 1, 1_728_000_100, 'test-app',
            ))

        connect.assert_called_once_with(
            'wss://api.derivws.com/trading/v1/options/ws/public',
        )
        self.assertEqual(len(candles), 1)
        self.assertEqual(candles[0]['close'], 1.15)
        self.assertEqual(websocket.sent[0]['ticks_history'], '1HZ100V')

    def test_get_candles_after_connect_uses_adapter_event_loop(self):
        adapter = DerivAdapter(app_id='test-app')
        websocket = _FakeAdapterWebSocket(adapter)
        adapter._loop = asyncio.new_event_loop()
        adapter._req_id = 1

        def run_adapter_loop():
            asyncio.set_event_loop(adapter._loop)
            adapter._loop.run_forever()

        with patch.object(adapter, '_run_event_loop', side_effect=run_adapter_loop):
            adapter.connect({})
            try:
                adapter._ws = websocket
                candles = adapter.get_candles('1HZ100V', 60, 1)
                self.assertEqual(len(candles), 1)
                self.assertEqual(str(candles[0].close), '1.15')
            finally:
                adapter._running = False
                adapter._loop.call_soon_threadsafe(adapter._loop.stop)
                adapter._thread.join(timeout=2)
                adapter._loop.close()