import logging
import os
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class TradingAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading_app'

    def ready(self):
        import sys
        if settings.BROKER_REACHABLE:
            return
        if 'test' in sys.argv or 'testserver' in sys.argv or 'TESTING' in os.environ:
            return

        try:
            from .scheduler import BackgroundScheduler
            from .tasks import (run_bot_cycle, cleanup_expired_otps, resolve_pending_predictions,
                                retrain_models_with_feedback, resolve_pending_signal_outcomes)

            BackgroundScheduler.register(60, run_bot_cycle, "run_bot_cycle")
            BackgroundScheduler.register(3600, cleanup_expired_otps, "cleanup_expired_otps")
            BackgroundScheduler.register(300, resolve_pending_predictions, "resolve_pending_predictions")
            BackgroundScheduler.register(300, resolve_pending_signal_outcomes, "resolve_pending_signal_outcomes")
            BackgroundScheduler.register(900, retrain_models_with_feedback, "retrain_models_with_feedback")
            BackgroundScheduler.start()
            logger.info("Background scheduler started (no Redis/Celery broker available)")
        except Exception as e:
            logger.error("Failed to start background scheduler: %s", e)
