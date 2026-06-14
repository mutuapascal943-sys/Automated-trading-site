from celery import shared_task
from django.utils import timezone
from django.conf import settings
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
from .models import EmailOTP, Trade, User
import logging

logger = logging.getLogger(__name__)


@shared_task
def send_otp_email_task(user_id: int, purpose: str = '2fa') -> bool:
    try:
        user = User.objects.get(id=user_id)
        otp_code = generate_otp()
        expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)

        EmailOTP.objects.create(
            user=user,
            code=otp_code,
            purpose=purpose,
            expires_at=expires_at,
        )

        CacheService.set_otp(user_id, otp_code)
        return send_otp_email(user, otp_code)
    except User.DoesNotExist:
        logger.error(f'User {user_id} not found for OTP email')
        return False
    except Exception as e:
        logger.error(f'Failed to send OTP email to user {user_id}: {e}')
        return False


@shared_task
def cleanup_expired_otps():
    deleted, _ = EmailOTP.objects.filter(
        expires_at__lte=timezone.now(),
        is_used=False,
    ).delete()
    logger.info(f'Cleaned up {deleted} expired OTPs')
    return deleted


@shared_task
def sync_live_trades():
    from .services.broker_service import BrokerService
    broker = BrokerService()
    if not broker.is_configured():
        return

    open_trades = Trade.objects.filter(is_live=True, status='OPEN')
    for trade in open_trades:
        try:
            market_data = broker.get_market_data(trade.symbol)
            if market_data:
                CacheService.set_market_data(trade.symbol, market_data)
        except Exception as e:
            logger.error(f'Failed to sync trade {trade.id}: {e}')


@shared_task
def reset_daily_trade_counts():
    User.objects.update(daily_trades_count=0, last_trade_date=timezone.now().date())
    logger.info('Daily trade counts reset')
