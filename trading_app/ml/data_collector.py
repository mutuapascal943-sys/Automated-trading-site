"""
Fetch OHLCV candles from Deriv API with pagination.

Deriv's ticks_history endpoint returns up to ~5000 candles per request.
This module pages backward through time to collect years of history.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from decimal import Decimal

logger = logging.getLogger(__name__)

GRANULARITY_MAP: dict[int, str] = {
    60: "1m", 300: "5m", 900: "15m", 1800: "30m",
    3600: "1h", 14400: "4h", 86400: "1d",
}

# Deriv limits ~5000 candles per request
MAX_CANDLES_PER_REQUEST = 4990
# Rate limit: be polite
REQUEST_DELAY = 0.5


def collect_candles(
    symbol: str,
    granularity: int = 900,
    years: float = 2.0,
    app_id: str = "1089",
    token: str | None = None,
) -> list[dict]:
    """
    Collect OHLCV candles from Deriv going back `years` years.

    Returns list of dicts: {open, high, low, close, volume, time}
    sorted oldest-first.
    """
    try:
        import websockets
    except ImportError:
        raise ImportError("websockets package required for Deriv data collection")

    deriv_gran = GRANULARITY_MAP.get(granularity)
    if deriv_gran is None:
        raise ValueError(f"Unsupported granularity: {granularity}s")

    seconds_per_candle = granularity
    total_candles = int((years * 365.25 * 86400) / seconds_per_candle)
    logger.info(
        f"Collecting {total_candles} candles for {symbol} ({deriv_gran}) "
        f"spanning {years:.1f} years"
    )

    import asyncio
    all_candles = []
    end_epoch = int(time.time())

    loop = asyncio.new_event_loop()
    try:
        all_candles = loop.run_until_complete(
            _fetch_all(symbol, granularity, total_candles, end_epoch, app_id, token)
        )
    finally:
        loop.close()

    all_candles.sort(key=lambda c: c["time"])
    logger.info(f"Collected {len(all_candles)} candles for {symbol}")
    return all_candles


async def _fetch_all(
    symbol: str,
    granularity: int,
    total_needed: int,
    end_epoch: int,
    app_id: str,
    token: str | None,
) -> list[dict]:
    """Paginate backward through Deriv ticks_history."""
    import websockets

    url = f"wss://ws.deriv.com/websockets/v3?app_id={app_id}"
    headers = {}
    if token:
        headers["Authorization"] = token

    all_candles = []
    current_end = end_epoch
    collected = 0
    req_id = 0

    async with websockets.connect(url, additional_headers=headers) as ws:
        while collected < total_needed:
            count = min(MAX_CANDLES_PER_REQUEST, total_needed - collected)
            req_id += 1

            await ws.send(json.dumps({
                "ticks_history": symbol,
                "adjust_start_time": 1,
                "start": 1,
                "end": current_end,
                "style": "candles",
                "granularity": granularity,
                "count": count,
                "req_id": req_id,
            }))

            response = None
            for _ in range(30):
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
                msg = json.loads(raw)
                if msg.get("req_id") == req_id:
                    response = msg
                    break

            if response is None:
                logger.warning("No response for request %d, stopping", req_id)
                break

            if "error" in response:
                logger.error("Deriv API error: %s", response["error"])
                break

            candles = response.get("candles", [])
            if not candles:
                break

            for entry in candles:
                all_candles.append({
                    "open": float(entry["open"]),
                    "high": float(entry["high"]),
                    "low": float(entry["low"]),
                    "close": float(entry["close"]),
                    "volume": float(entry.get("volume", 0)),
                    "time": entry["epoch"],
                })

            collected += len(candles)

            oldest_epoch = candles[0]["epoch"]
            if oldest_epoch >= current_end:
                break
            current_end = oldest_epoch - 1

            logger.info(
                f"  Fetched {collected}/{total_needed} candles, "
                f"oldest: {datetime.fromtimestamp(oldest_epoch, tz=timezone.utc).isoformat()}"
            )

            await asyncio.sleep(REQUEST_DELAY)

    return all_candles


def collect_from_adapter(
    adapter,
    symbol: str,
    granularity: int = 900,
    years: float = 2.0,
) -> list[dict]:
    """
    Collect OHLCV using an already-connected BrokerAdapter.
    Pages backward by adjusting the start time.
    Falls back to the Deriv adapter's get_candles method.
    """
    seconds_per_candle = granularity
    total_candles = int((years * 365.25 * 86400) / seconds_per_candle)

    all_candles = []
    now = datetime.now(timezone.utc)
    end_epoch = int(now.timestamp())

    while len(all_candles) < total_candles:
        batch_count = min(MAX_CANDLES_PER_REQUEST, total_candles - len(all_candles))
        try:
            candles = adapter.get_candles(symbol, granularity, batch_count)
        except Exception as e:
            logger.warning(f"Adapter fetch failed: {e}")
            break

        if not candles:
            break

        for c in candles:
            all_candles.append({
                "open": float(c.open),
                "high": float(c.high),
                "low": float(c.low),
                "close": float(c.close),
                "volume": float(c.volume),
                "time": int(c.timestamp.timestamp()),
            })

        oldest = candles[0].timestamp.timestamp()
        if oldest >= end_epoch:
            break
        end_epoch = int(oldest) - 1

        logger.info(f"  Adapter: {len(all_candles)}/{total_candles} candles")
        time.sleep(REQUEST_DELAY)

    all_candles.sort(key=lambda c: c["time"])
    return all_candles


import json
