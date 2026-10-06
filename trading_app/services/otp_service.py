from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta
from enum import Enum

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from trading_app.models import EmailOTP

MAX_VERIFY_ATTEMPTS = 5
OTP_DIGEST_ALGORITHM = 'sha256'


class OTPResult(str, Enum):
    VERIFIED = 'verified'
    INVALID = 'incorrect'
    EXPIRED = 'expired'
    RATE_LIMITED = 'too_many_attempts'
    USED = 'already_used'


def _digest(user_id: int, purpose: str, code: str) -> str:
    payload = f'{user_id}:{purpose}:{code}'.encode()
    return hmac.new(settings.SECRET_KEY.encode(), payload, hashlib.sha256).hexdigest()


def generate_otp(length: int = 6) -> str:
    if length < 6 or length > 10:
        raise ValueError('OTP length must be between six and ten digits.')
    return ''.join(secrets.choice('0123456789') for _ in range(length))


def issue_otp(user, purpose: str) -> tuple[EmailOTP, str]:
    valid_purposes = {choice[0] for choice in EmailOTP._meta.get_field('purpose').choices}
    if purpose not in valid_purposes:
        raise ValueError('Unsupported OTP purpose.')
    code = generate_otp()
    now = timezone.now()
    with transaction.atomic():
        EmailOTP.objects.select_for_update().filter(
            user=user, purpose=purpose, is_used=False,
        ).update(is_used=True)
        otp = EmailOTP.objects.create(
            user=user,
            code=_digest(user.pk, purpose, code),
            purpose=purpose,
            expires_at=now + timedelta(seconds=settings.OTP_EXPIRY_SECONDS),
        )
    cache.delete(_attempt_cache_key(user.pk, purpose))
    return otp, code


def consume_otp(user, purpose: str, code: str) -> OTPResult:
    if verification_locked(user.pk, purpose):
        return OTPResult.RATE_LIMITED
    if not code or len(code) != 6 or not code.isdigit():
        _record_failed_attempt(user.pk, purpose)
        return OTPResult.INVALID

    with transaction.atomic():
        otp = EmailOTP.objects.select_for_update().filter(
            user=user, purpose=purpose, is_used=False,
        ).order_by('-created_at').first()
        if otp is None:
            if EmailOTP.objects.filter(user=user, purpose=purpose).exists():
                _record_failed_attempt(user.pk, purpose)
                return OTPResult.USED
            return OTPResult.INVALID
        if otp.failed_attempts >= MAX_VERIFY_ATTEMPTS:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            _record_failed_attempt(user.pk, purpose)
            return OTPResult.RATE_LIMITED
        if otp.expires_at <= timezone.now():
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            _record_failed_attempt(user.pk, purpose)
            return OTPResult.EXPIRED

        expected = _digest(user.pk, purpose, code)
        if not hmac.compare_digest(otp.code, expected):
            otp.failed_attempts += 1
            if otp.failed_attempts >= MAX_VERIFY_ATTEMPTS:
                otp.is_used = True
            otp.save(update_fields=['failed_attempts', 'is_used'])
            _record_failed_attempt(user.pk, purpose)
            if otp.failed_attempts >= MAX_VERIFY_ATTEMPTS:
                return OTPResult.RATE_LIMITED
            return OTPResult.INVALID

        otp.is_used = True
        otp.save(update_fields=['is_used'])
        cache.delete(_attempt_cache_key(user.pk, purpose))
        return OTPResult.VERIFIED


def invalidate_otp(otp: EmailOTP) -> None:
    EmailOTP.objects.filter(pk=otp.pk, is_used=False).update(is_used=True)


def _attempt_cache_key(user_id: int, purpose: str) -> str:
    return f'otp_verify_attempts_{user_id}_{purpose}'


def _record_failed_attempt(user_id: int, purpose: str) -> None:
    key = _attempt_cache_key(user_id, purpose)
    cache.add(key, 0, timeout=settings.OTP_EXPIRY_SECONDS)
    attempts = cache.incr(key)
    if attempts >= MAX_VERIFY_ATTEMPTS:
        cache.set(key, attempts, timeout=settings.OTP_EXPIRY_SECONDS)


def cache_otp_marker(user_id: int, purpose: str, timeout: int | None = None) -> None:
    """Store only a non-secret purpose marker for compatibility with old flows."""
    cache.set(f'otp_marker_{user_id}_{purpose}', True, timeout=timeout or settings.OTP_EXPIRY_SECONDS)


def clear_otp_marker(user_id: int, purpose: str | None = None) -> None:
    if purpose:
        cache.delete(f'otp_marker_{user_id}_{purpose}')
        return
    for known_purpose in ('2fa', 'password_reset', 'email_verify'):
        cache.delete(f'otp_marker_{user_id}_{known_purpose}')


def verification_locked(user_id: int, purpose: str) -> bool:
    return cache.get(_attempt_cache_key(user_id, purpose), 0) >= MAX_VERIFY_ATTEMPTS


def can_request_otp(user_id: int, purpose: str, scope: str = 'issue') -> bool:
    limit = 3
    window = 60 if scope == 'resend' else 300
    key = f'otp_request_{scope}_{user_id}_{purpose}'
    attempts = cache.get(key, 0)
    if attempts >= limit:
        return False
    cache.set(key, attempts + 1, timeout=window)
    return True


def clear_otp_state(user_id: int, purpose: str) -> None:
    cache.delete(_attempt_cache_key(user_id, purpose))
    cache.delete(f'otp_request_issue_{user_id}_{purpose}')
    cache.delete(f'otp_request_resend_{user_id}_{purpose}')
