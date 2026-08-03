import json
import logging
import re

from channels.generic.websocket import AsyncWebsocketConsumer

from .services.ticker_bridge import TickerBridge

logger = logging.getLogger(__name__)


class MarketConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        symbol = self.scope['url_route']['kwargs']['symbol']
        normalised = symbol.replace('-', '/')
        self.symbol = normalised
        safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', normalised)
        self.group_name = f'market_{safe}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        user = self.scope.get('user')
        if user and user.is_authenticated:
            mode = 'live'
        else:
            user = None
            mode = 'paper'

        await self.send(text_data=json.dumps({
            'type': 'connected',
            'symbol': normalised,
            'message': f'Connected to {normalised} market stream ({mode})',
        }))

        TickerBridge.ensure_subscription(normalised, user=user)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        TickerBridge.remove_subscription(self.symbol, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
        except json.JSONDecodeError:
            return

        msg_type = data.get('type')

        if msg_type == 'unsubscribe':
            TickerBridge.remove_subscription(self.symbol, self.channel_name)
            await self.send(text_data=json.dumps({
                'type': 'unsubscribed',
                'symbol': self.symbol,
            }))

        elif msg_type == 'subscribe':
            new_symbol = data.get('symbol', '').replace('-', '/').strip()
            if not new_symbol:
                return

            if new_symbol != self.symbol:
                TickerBridge.remove_subscription(self.symbol, self.channel_name)

                old_safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', self.symbol)
                old_group = f'market_{old_safe}'
                await self.channel_layer.group_discard(old_group, self.channel_name)

                self.symbol = new_symbol
                new_safe = re.sub(r'[^a-zA-Z0-9_.-]', '_', new_symbol)
                self.group_name = f'market_{new_safe}'
                await self.channel_layer.group_add(self.group_name, self.channel_name)

                user = self.scope.get('user')
                TickerBridge.ensure_subscription(new_symbol, user=user if user and user.is_authenticated else None)

                await self.send(text_data=json.dumps({
                    'type': 'subscribed',
                    'symbol': new_symbol,
                    'message': f'Switched to {new_symbol} market stream',
                }))

        elif msg_type == 'ping':
            await self.send(text_data=json.dumps({'type': 'pong'}))

    async def tick(self, event):
        await self.send(text_data=json.dumps(event['data']))

    async def candle(self, event):
        await self.send(text_data=json.dumps(event['data']))
