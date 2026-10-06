from __future__ import annotations

import base64
import hashlib
import logging
import re
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse
from zoneinfo import ZoneInfo

import requests
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

SANDBOX_BASE_URL = 'https://sandbox.safaricom.co.ke'
PRODUCTION_BASE_URL = 'https://api.safaricom.co.ke'
REQUEST_TIMEOUT_SECONDS = 15
TOKEN_CACHE_KEY = 'daraja:oauth:access-token'
_TOKEN_LOCK = threading.Lock()
PHONE_RE = re.compile(r'^(?:2547\d{8}|2541\d{8})$')


class DarajaError(Exception):
    """Sanitized Daraja integration error safe to surface to application code."""


class DarajaTimeoutError(DarajaError):
    """Request timed out after dispatch may have begun; provider outcome is uncertain."""


@dataclass(frozen=True)
class DarajaConfig:
    consumer_key: str
    consumer_secret: str
    shortcode: str
    passkey: str
    callback_url: str
    environment: str
    basic_amount_kes: int
    transaction_type: str

    @classmethod
    def from_settings(cls) -> 'DarajaConfig':
        environment = str(settings.DARAJA_ENVIRONMENT).lower()
        if environment not in {'sandbox', 'production'}:
            raise DarajaError('Daraja environment configuration is invalid.')
        return cls(
            consumer_key=settings.DARAJA_CONSUMER_KEY,
            consumer_secret=settings.DARAJA_CONSUMER_SECRET,
            shortcode=settings.DARAJA_SHORTCODE,
            passkey=settings.DARAJA_PASSKEY,
            callback_url=settings.DARAJA_CALLBACK_URL,
            environment=environment,
            basic_amount_kes=settings.DARAJA_BASIC_AMOUNT_KES,
            transaction_type=settings.DARAJA_TRANSACTION_TYPE,
        )

    @property
    def base_url(self) -> str:
        return SANDBOX_BASE_URL if self.environment == 'sandbox' else PRODUCTION_BASE_URL

    def validate(self, require_product_price: bool = False) -> None:
        if not all((self.consumer_key, self.consumer_secret, self.shortcode, self.passkey, self.callback_url)):
            raise DarajaError('Daraja is not fully configured.')
        if require_product_price and self.basic_amount_kes <= 0:
            raise DarajaError('The server-side BASIC subscription KES price is not configured.')
        if self.transaction_type not in {'CustomerPayBillOnline', 'CustomerBuyGoodsOnline'}:
            raise DarajaError('Daraja transaction type configuration is invalid.')
        if self.environment == 'production' and urlparse(self.callback_url).scheme != 'https':
            raise DarajaError('Production Daraja callback URL must use HTTPS.')

    @property
    def token_cache_key(self) -> str:
        identity = hashlib.sha256(self.consumer_key.encode()).hexdigest()[:16]
        return f'{TOKEN_CACHE_KEY}:{self.environment}:{identity}'


def _sanitize_error(operation: str, exc: Exception) -> DarajaError:
    logger.warning('Daraja %s request failed (%s).', operation, type(exc).__name__)
    return DarajaError(f'Daraja {operation} is temporarily unavailable.')


def get_access_token(session=requests) -> str:
    """Return a cached Daraja OAuth token without logging credential material."""
    config = DarajaConfig.from_settings()
    config.validate()
    cache_key = config.token_cache_key
    cached = cache.get(cache_key)
    if cached:
        return cached

    with _TOKEN_LOCK:
        cached = cache.get(cache_key)
        if cached:
            return cached
        try:
            response = session.get(
                f'{config.base_url}/oauth/v1/generate?grant_type=client_credentials',
                auth=(config.consumer_key, config.consumer_secret),
                timeout=REQUEST_TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            body = response.json()
            token = body.get('access_token')
            expires_in = int(body.get('expires_in', 3600))
            if not token or expires_in <= 60:
                raise DarajaError('Daraja returned an invalid OAuth response.')
            cache.set(cache_key, token, timeout=expires_in - 60)
            return token
        except DarajaError:
            raise
        except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
            raise _sanitize_error('OAuth', exc) from None


def normalize_phone_number(value: object) -> str:
    digits = re.sub(r'[^0-9]', '', str(value or ''))
    if digits.startswith('0') and len(digits) == 10:
        digits = '254' + digits[1:]
    elif digits.startswith('7') or digits.startswith('1'):
        digits = '254' + digits
    if not PHONE_RE.fullmatch(digits):
        raise DarajaError('Enter a valid Kenyan M-Pesa phone number.')
    return digits


def _timestamp(now: datetime | None = None) -> str:
    local_now = now or datetime.now(ZoneInfo('Africa/Nairobi'))
    if local_now.tzinfo is None:
        local_now = local_now.replace(tzinfo=ZoneInfo('Africa/Nairobi'))
    return local_now.astimezone(ZoneInfo('Africa/Nairobi')).strftime('%Y%m%d%H%M%S')


def _password(config: DarajaConfig, timestamp: str) -> str:
    return base64.b64encode(f'{config.shortcode}{config.passkey}{timestamp}'.encode()).decode()


def initiate_stk_push(
    phone_number: str,
    account_reference: str,
    transaction_desc: str,
    session=requests,
    now: datetime | None = None,
) -> dict:
    config = DarajaConfig.from_settings()
    config.validate(require_product_price=True)
    phone = normalize_phone_number(phone_number)
    timestamp = _timestamp(now)
    token = get_access_token(session=session)
    payload = {
        'BusinessShortCode': config.shortcode,
        'Password': _password(config, timestamp),
        'Timestamp': timestamp,
        'TransactionType': config.transaction_type,
        'Amount': config.basic_amount_kes,
        'PartyA': phone,
        'PartyB': config.shortcode,
        'PhoneNumber': phone,
        'CallBackURL': config.callback_url,
        'AccountReference': account_reference[:12],
        'TransactionDesc': transaction_desc[:13],
    }
    try:
        response = session.post(
            f'{config.base_url}/mpesa/stkpush/v1/processrequest',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        if not body.get('MerchantRequestID') or not body.get('CheckoutRequestID'):
            raise DarajaError('Daraja returned an invalid STK Push response.')
        return {
            'merchant_request_id': str(body['MerchantRequestID']),
            'checkout_request_id': str(body['CheckoutRequestID']),
            'customer_message': str(body.get('CustomerMessage', 'Payment prompt initiated.'))[:240],
        }
    except DarajaError:
        raise
    except requests.Timeout:
        logger.warning('Daraja STK Push request timed out; provider outcome is uncertain.')
        raise DarajaTimeoutError('Daraja payment request outcome is unknown. Do not retry until it is checked.') from None
    except Exception as exc:
        raise _sanitize_error('STK Push', exc) from None


def query_stk_status(checkout_request_id: str, session=requests, now: datetime | None = None) -> dict:
    """Ask Daraja for authoritative transaction status after callback receipt."""
    config = DarajaConfig.from_settings()
    config.validate()
    timestamp = _timestamp(now)
    token = get_access_token(session=session)
    payload = {
        'BusinessShortCode': config.shortcode,
        'Password': _password(config, timestamp),
        'Timestamp': timestamp,
        'CheckoutRequestID': checkout_request_id,
    }
    try:
        response = session.post(
            f'{config.base_url}/mpesa/stkpushquery/v1/query',
            json=payload,
            headers={'Authorization': f'Bearer {token}'},
            timeout=REQUEST_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        body = response.json()
        if 'ResultCode' not in body:
            raise DarajaError('Daraja returned an invalid transaction status response.')
        return {
            'result_code': str(body['ResultCode']),
            'result_description': str(body.get('ResultDesc', ''))[:255],
        }
    except DarajaError:
        raise
    except (requests.RequestException, ValueError, TypeError, KeyError) as exc:
        raise _sanitize_error('transaction verification', exc) from None
