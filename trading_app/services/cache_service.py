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

    @staticmethod
    def set_otp(user_id: int, otp_code: str) -> None:
        key = f'{CacheService.OTP_PREFIX}{user_id}'
        cache.set(key, otp_code, timeout=settings.OTP_EXPIRY_SECONDS)

    @staticmethod
    def get_otp(user_id: int) -> str | None:
        key = f'{CacheService.OTP_PREFIX}{user_id}'
        return cache.get(key)

    @staticmethod
    def delete_otp(user_id: int) -> None:
        key = f'{CacheService.OTP_PREFIX}{user_id}'
        cache.delete(key)

    @staticmethod
    def verify_otp(user_id: int, code: str) -> bool:
        stored = CacheService.get_otp(user_id)
        if stored and stored == code:
            CacheService.delete_otp(user_id)
            return True
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
