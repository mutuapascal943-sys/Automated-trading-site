from datetime import timedelta

from django.utils import timezone


TRIAL_DURATION = timedelta(hours=24)


def subscription_status(user) -> dict:
    now = timezone.now()
    if user.subscription_access_denied:
        return {
            'has_access': False,
            'status': 'admin_denied',
            'tier': 'DENIED',
            'expires_at': None,
            'seconds_remaining': 0,
        }
    if user.subscription_bypass:
        return {
            'has_access': True,
            'status': 'admin_bypass',
            'tier': 'ADMIN',
            'expires_at': None,
            'seconds_remaining': None,
        }

    subscription = getattr(user, 'subscription', None)
    if (
        subscription
        and subscription.is_active
        and subscription.tier != 'FREE'
        and subscription.expires_at
        and subscription.expires_at > now
    ):
        return {
            'has_access': True,
            'status': 'subscribed',
            'tier': subscription.tier,
            'expires_at': subscription.expires_at,
            'seconds_remaining': int((subscription.expires_at - now).total_seconds()),
        }

    trial_ends_at = user.trial_started_at + TRIAL_DURATION if user.trial_started_at else None
    if trial_ends_at and trial_ends_at > now:
        return {
            'has_access': True,
            'status': 'trial',
            'tier': 'FREE',
            'expires_at': trial_ends_at,
            'seconds_remaining': int((trial_ends_at - now).total_seconds()),
        }

    return {
        'has_access': False,
        'status': 'expired',
        'tier': subscription.tier if subscription else 'FREE',
        'expires_at': trial_ends_at or (subscription.expires_at if subscription else None),
        'seconds_remaining': 0,
    }