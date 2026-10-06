from django.core.cache import cache
from django.conf import settings


class CacheService:
    OTP_PREFIX = 'otp_'
    RATE_LIMIT_PREFIX = 'ratelimit_'
    MARKET_DATA_PREFIX = 'market_'
    LLM_CACHE_PREFIX = 'llm_'

    @staticmethod
    def set(key: str, value, timeout: int = 300) -> None:
        cache.set(key, value, timeout=timeout)

    @staticmethod
    def get(key: str, default=None):
        return cache.get(key, default)

    @staticmethod
    def delete(key: str) -> None:
        cache.delete(key)

    CANDLE_CACHE_PREFIX = 'candles_'
    CANDLE_CACHE_TIMEOUT = 60

    @staticmethod
    def get_candles(symbol: str, granularity: int) -> list | None:
        key = f'{CacheService.CANDLE_CACHE_PREFIX}{symbol}_{granularity}'
        return cache.get(key)

    @staticmethod
    def set_candles(symbol: str, granularity: int, candles: list, timeout: int | None = None) -> None:
        key = f'{CacheService.CANDLE_CACHE_PREFIX}{symbol}_{granularity}'
        cache.set(key, candles, timeout=timeout or CacheService.CANDLE_CACHE_TIMEOUT)

    @staticmethod
    def set_otp(user_id: int, otp_code: str, purpose: str = '2fa') -> None:
        from trading_app.services.otp_service import cache_otp_marker

        cache_otp_marker(user_id, purpose)

    @staticmethod
    def get_otp(user_id: int, purpose: str = '2fa') -> str | None:
        # Raw OTP values are deliberately never cached; this legacy method remains empty.
        return None

    @staticmethod
    def delete_otp(user_id: int, purpose: str | None = None) -> None:
        from trading_app.services.otp_service import clear_otp_marker

        clear_otp_marker(user_id, purpose)

    @staticmethod
    def verify_otp(user_id: int, code: str) -> bool:
        return False

    @staticmethod
    def check_rate_limit(key: str, max_attempts: int = 5, window: int = 300) -> bool:
        cache_key = f'{CacheService.RATE_LIMIT_PREFIX}{key}'
        attempts = cache.get(cache_key, 0)
        if attempts >= max_attempts:
            return False
        cache.set(cache_key, attempts + 1, timeout=window)
        return True

    @staticmethod
    def reset_rate_limit(key: str) -> None:
        cache_key = f'{CacheService.RATE_LIMIT_PREFIX}{key}'
        cache.delete(cache_key)

    @staticmethod
    def get_market_data(symbol: str) -> dict | None:
        key = f'{CacheService.MARKET_DATA_PREFIX}{symbol}'
        return cache.get(key)

    @staticmethod
    def set_market_data(symbol: str, data: dict, timeout: int = 30) -> None:
        key = f'{CacheService.MARKET_DATA_PREFIX}{symbol}'
        cache.set(key, data, timeout=timeout)

    @staticmethod
    def get_llm_cache(query_hash: str) -> str | None:
        key = f'{CacheService.LLM_CACHE_PREFIX}{query_hash}'
        return cache.get(key)

    @staticmethod
    def set_llm_cache(query_hash: str, response: str, timeout: int = 3600) -> None:
        key = f'{CacheService.LLM_CACHE_PREFIX}{query_hash}'
        cache.set(key, response, timeout=timeout)


cache_service = CacheService()
