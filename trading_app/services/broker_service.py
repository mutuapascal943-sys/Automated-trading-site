import json
import logging
import hashlib
import hmac
import time
import requests
from decimal import Decimal
from typing import Optional
from decouple import config

logger = logging.getLogger(__name__)


class BrokerService:
    def __init__(self, api_key: str = '', api_secret: str = '', endpoint: str = '', account_id: str = ''):
        self.api_key = api_key or config('TRADING_API_KEY', default='')
        self.api_secret = api_secret or config('TRADING_API_SECRET', default='')
        self.endpoint = endpoint or config('TRADING_API_ENDPOINT', default='')
        self.account_id = account_id or config('TRADING_ACCOUNT_ID', default='')

    def is_configured(self) -> bool:
        return bool(self.api_key and self.endpoint and not self.api_key.startswith('your-'))

    def _sign_request(self, payload: dict) -> dict:
        timestamp = str(int(time.time() * 1000))
        message = timestamp + json.dumps(payload, sort_keys=True)
        signature = hmac.new(
            self.api_secret.encode(),
            message.encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            'Content-Type': 'application/json',
            'X-Api-Key': self.api_key,
            'X-Signature': signature,
            'X-Timestamp': timestamp,
        }

    def get_market_data(self, symbol: str) -> Optional[dict]:
        if not self.is_configured():
            return None
        try:
            payload = {
                'action': 'market_data',
                'symbol': symbol,
                'account_id': self.account_id,
            }
            headers = self._sign_request(payload)
            resp = requests.post(
                f'{self.endpoint}/v1/market',
                json=payload,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f'Failed to get market data for {symbol}: {e}')
            return None

    def execute_trade(self, symbol: str, action: str, volume: float, order_type: str = 'MARKET') -> Optional[dict]:
        if not self.is_configured():
            return None
        try:
            payload = {
                'action': 'trade',
                'symbol': symbol,
                'order_type': order_type,
                'volume': volume,
                'account_id': self.account_id,
            }
            if action.upper() == 'BUY':
                payload['side'] = 'buy'
            elif action.upper() == 'SELL':
                payload['side'] = 'sell'
            else:
                return None

            headers = self._sign_request(payload)
            resp = requests.post(
                f'{self.endpoint}/v1/order',
                json=payload,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f'Failed to execute trade: {e}')
            return None

    def get_account_balance(self) -> Optional[Decimal]:
        if not self.is_configured():
            return None
        try:
            payload = {
                'action': 'account_info',
                'account_id': self.account_id,
            }
            headers = self._sign_request(payload)
            resp = requests.post(
                f'{self.endpoint}/v1/account',
                json=payload,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return Decimal(str(data.get('balance', 0)))
        except Exception as e:
            logger.error(f'Failed to get account balance: {e}')
            return None

    def get_open_positions(self) -> list:
        if not self.is_configured():
            return []
        try:
            payload = {
                'action': 'open_positions',
                'account_id': self.account_id,
            }
            headers = self._sign_request(payload)
            resp = requests.post(
                f'{self.endpoint}/v1/positions',
                json=payload,
                headers=headers,
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return data.get('positions', [])
        except Exception as e:
            logger.error(f'Failed to get open positions: {e}')
            return []


broker_service = BrokerService()
