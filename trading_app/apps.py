import logging
import os
import sys
from pathlib import Path
from django.apps import AppConfig
from django.conf import settings

logger = logging.getLogger(__name__)


class TradingAppConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'trading_app'

    def ready(self):
        if settings.BROKER_REACHABLE:
            return
        if 'test' in sys.argv or 'testserver' in sys.argv or 'TESTING' in os.environ:
            return
        invoked_from_manage_py = Path(sys.argv[0]).name.lower() in {'manage.py', 'django-admin', 'django-admin.py'}
        if invoked_from_manage_py and len(sys.argv) > 1 and sys.argv[1] != 'runserver':
            return

        try:
            from .scheduler import BackgroundScheduler
            from .tasks import (run_bot_cycle, cleanup_expired_otps, resolve_pending_predictions,
                                retrain_models_with_feedback, resolve_pending_signal_outcomes,
                                check_expired_wait_cycles)

            BackgroundScheduler.register(60, run_bot_cycle, "run_bot_cycle")
            BackgroundScheduler.register(5, check_expired_wait_cycles, "check_expired_wait_cycles")
            BackgroundScheduler.register(3600, cleanup_expired_otps, "cleanup_expired_otps")
            BackgroundScheduler.register(300, resolve_pending_predictions, "resolve_pending_predictions")
            BackgroundScheduler.register(300, resolve_pending_signal_outcomes, "resolve_pending_signal_outcomes")
            BackgroundScheduler.register(900, retrain_models_with_feedback, "retrain_models_with_feedback")
            BackgroundScheduler.start()
            logger.info("Background scheduler started (no Redis/Celery broker available)")
        except Exception as e:
            logger.error("Failed to start background scheduler: %s", e)
