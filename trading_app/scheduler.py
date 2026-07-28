import logging
import threading
import time
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)


class BackgroundScheduler:
    """Runs Celery-beat-style periodic tasks in a background thread when
    Celery + Redis are unavailable.  Each task is run synchronously
    (eagerly) on its own schedule."""

    _instance = None
    _lock = threading.Lock()
    _thread: threading.Thread | None = None
    _running = False

    # (interval_seconds, task_callable, name)
    _tasks: list[tuple[int, callable, str]] = []

    @classmethod
    def register(cls, interval: int, fn: callable, name: str) -> None:
        with cls._lock:
            cls._tasks.append((interval, fn, name))

    @classmethod
    def start(cls) -> None:
        with cls._lock:
            if cls._thread and cls._thread.is_alive():
                return
            cls._running = True
            cls._thread = threading.Thread(target=cls._loop, daemon=True, name="bg-scheduler")
            cls._thread.start()
            logger.info("BackgroundScheduler started with %d tasks", len(cls._tasks))

    @classmethod
    def stop(cls) -> None:
        cls._running = False

    @classmethod
    def _loop(cls) -> None:
        if not cls._tasks:
            logger.info("BackgroundScheduler: no tasks registered, exiting")
            return

        last_run: dict[str, datetime] = {}
        for _, _, name in cls._tasks:
            last_run[name] = datetime.min

        while cls._running:
            now = datetime.now()
            for interval, fn, name in cls._tasks:
                if now - last_run[name] >= timedelta(seconds=interval):
                    try:
                        logger.debug("BackgroundScheduler: running %s", name)
                        fn()
                    except Exception as e:
                        logger.error("BackgroundScheduler: %s failed: %s", name, e)
                    last_run[name] = now
            time.sleep(5)
