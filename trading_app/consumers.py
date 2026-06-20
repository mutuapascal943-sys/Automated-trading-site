import json
import logging

from channels.generic.websocket import AsyncWebsocketConsumer
from channels.layers import get_channel_layer

from .services.ticker_bridge import TickerBridge

logger = logging.getLogger(__name__)


class MarketConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        symbol = self.scope['url_route']['kwargs']['symbol']
        normalised = symbol.replace('-', '/')
        self.symbol = normalised
        self.group_name = f'market_{symbol}'

        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

        await self.send(text_data=json.dumps({
            'type': 'connected',
            'symbol': normalised,
            'message': f'Connected to {normalised} market stream',
        }))

        TickerBridge.ensure_subscription(normalised)

    async def disconnect(self, code):
        await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        data = json.loads(text_data)
        msg_type = data.get('type')

        if msg_type == 'unsubscribe':
            TickerBridge.remove_subscription(self.symbol, self.channel_name)

    async def tick(self, event):
        await self.send(text_data=json.dumps(event['data']))

    async def candle(self, event):
        await self.send(text_data=json.dumps(event['data']))
