from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from trading_app.services.cache_service import CacheService

from .base import CandleContext, StrategyResult

TIMEFRAME_LABELS = {
    14400: 'H4',
    900: 'M15',
    300: 'M5',
    60: 'M1',
}


class NewsFilterUnavailable(RuntimeError):
    """Raised when a news provider has not been configured."""


def check_news_window(symbol: str, timeframe: str | int = 'M1') -> dict:
    """Return the status of a red-news filter.

    The project does not currently include an economic-calendar/news provider,
    so the safe default is to report the filter as unavailable rather than
    inventing a negative or positive news result.
    """
    provider = _get_news_provider()
    if provider is None:
        return {
            'status': 'unavailable',
            'provider': None,
            'symbol': symbol,
            'timeframe': str(timeframe),
            'message': 'Economic-news filtering is unavailable because no news provider is configured.',
        }
    try:
        result = provider.check(symbol=symbol, timeframe=str(timeframe))
    except NotImplementedError:
        return {
            'status': 'unavailable',
            'provider': provider.__class__.__name__,
            'symbol': symbol,
            'timeframe': str(timeframe),
            'message': 'Economic-news filtering is unavailable for this provider.',
        }
    if not isinstance(result, dict):
        return {
            'status': 'unavailable',
            'provider': provider.__class__.__name__,
            'symbol': symbol,
            'timeframe': str(timeframe),
            'message': 'News provider returned an unsupported response.',
        }
    status = str(result.get('status', 'unknown')).lower()
    if status in {'red', 'blocked'}:
        return {
            'status': 'red',
            'provider': provider.__class__.__name__,
            'symbol': symbol,
            'timeframe': str(timeframe),
            'message': 'Red-news window detected; CHoCH entry is rejected.',
        }
    return {
        'status': 'clear',
        'provider': provider.__class__.__name__,
        'symbol': symbol,
        'timeframe': str(timeframe),
        'message': 'News filter is clear or not applicable.',
    }


def _get_news_provider():
    return None


def fetch_multi_timeframe_candles(
    symbol: str,
    timeframes: tuple[int, ...],
    user=None,
    chart_candles: list[dict] | None = None,
    chart_granularity: int | None = None,
) -> dict[str, list[dict]]:
    """Fetch aligned higher/lower timeframe candles without inventing data.

    When a live user session is supplied, this function reuses one adapter session
    and reads each granularity through the project’s shared cache/live data path.
    The function does not open a separate websocket per timeframe.
    """
    ordered = tuple(timeframes or (14400, 900, 300, 60))
    labels = {granularity: TIMEFRAME_LABELS.get(granularity, str(granularity)) for granularity in ordered}

    if user is None:
        return {
            labels[granularity]: (
                chart_candles if granularity == chart_granularity and chart_candles is not None
                else _request_timeframe_candles(symbol, granularity, user=None)
            )
            for granularity in ordered
        }

    try:
        from trading_app.api_views import _resolve_adapter, build_credentials

        adapter = _resolve_adapter(user)
        adapter.connect(build_credentials(user))
        try:
            data: dict[str, list[dict]] = {}
            for granularity in ordered:
                if granularity == chart_granularity and chart_candles is not None:
                    data[labels[granularity]] = chart_candles
                else:
                    data[labels[granularity]] = _request_timeframe_candles(
                        symbol, granularity, user=user, adapter=adapter,
                    )
            return data
        finally:
            try:
                adapter.disconnect()
            except Exception:
                pass
    except Exception:
        return {
            labels[granularity]: (
                chart_candles if granularity == chart_granularity and chart_candles is not None else []
            )
            for granularity in ordered
        }


def _request_timeframe_candles(symbol: str, granularity: int, user=None, adapter=None) -> list[dict]:
    cache_key = f'live_{symbol}'
    cached = CacheService.get_candles(cache_key, granularity)
    if cached:
        return [
            {
                'open': float(candle.get('open', 0) or 0),
                'high': float(candle.get('high', 0) or 0),
                'low': float(candle.get('low', 0) or 0),
                'close': float(candle.get('close', 0) or 0),
                'volume': float(candle.get('volume', 0) or 0),
                'time': int(candle.get('time', 0) or 0),
            }
            for candle in cached
        ]
    if user is None:
        return []
    if adapter is not None:
        try:
            from trading_app.api_views import deriv_symbol_for_market

            candles = adapter.get_candles(deriv_symbol_for_market(symbol), granularity, 200)
            return [{
                'open': float(c.open),
                'high': float(c.high),
                'low': float(c.low),
                'close': float(c.close),
                'volume': float(c.volume),
                'time': int(c.timestamp.timestamp()),
            } for c in candles]
        except Exception:
            return []
    try:
        from trading_app.api_views import _fetch_chart_candles

        candles = _fetch_chart_candles(user, symbol, count=200, granularity=granularity)
        if candles:
            return [{
                'open': float(c.open),
                'high': float(c.high),
                'low': float(c.low),
                'close': float(c.close),
                'volume': float(c.volume),
                'time': int(c.timestamp.timestamp()),
            } for c in candles]
    except Exception:
        return []
    return []


def evaluate(context: CandleContext) -> StrategyResult:
    started = time.perf_counter()
    multi_timeframe = getattr(context, 'multi_timeframe', {}) or {}
    h4 = multi_timeframe.get('H4') or multi_timeframe.get('D1') or context.candles
    m15 = multi_timeframe.get('M15') or h4
    m5 = multi_timeframe.get('M5') or m15
    m1 = multi_timeframe.get('M1') or context.candles

    conditions: list[str] = []
    reasons: list[str] = []
    levels: dict[str, float] = {}

    if not h4 or not m15 or not m5 or not m1:
        return StrategyResult(
            'CHoCH', 'neutral', 0.0, ('Higher/lower timeframe context is unavailable for CHoCH.'),
            ('missing_multi_timeframe_data',), levels,
            round((time.perf_counter() - started) * 1000, 3),
        )

    news_status = getattr(context, 'news_status', None) or check_news_window('EUR/USD', 'M1')
    if news_status.get('status') == 'red':
        conditions.append('red_news_window')
        reasons.append('Red-news window detected; CHoCH avoids risky entries.')
        return StrategyResult(
            'CHoCH', 'neutral', 0.0, tuple(reasons), tuple(conditions), levels,
            round((time.perf_counter() - started) * 1000, 3),
        )

    h4_closes = [float(c.get('close', 0) or 0) for c in h4[-20:]]
    current_close = float(context.current.get('close', 0) or 0)
    previous_close = float(context.previous.get('close', current_close) or current_close)
    h4_bullish = len(h4_closes) >= 2 and h4_closes[-1] > max(h4_closes[:-1])
    h4_bearish = len(h4_closes) >= 2 and h4_closes[-1] < min(h4_closes[:-1])
    if h4_bullish:
        conditions.append('h4_context_bullish')
        reasons.append('H4 context is bullish and price is above the prior H4 range.')
    elif h4_bearish:
        conditions.append('h4_context_bearish')
        reasons.append('H4 context is bearish and price is below the prior H4 range.')

    support_zone, resistance_zone = _supply_demand_zones(m15, m5)
    if support_zone is not None:
        levels['support_zone'] = support_zone
    if resistance_zone is not None:
        levels['resistance_zone'] = resistance_zone

    m1_closes = [float(c.get('close', 0) or 0) for c in m1[-8:]]
    if len(m1_closes) >= 2:
        recent_mean = sum(m1_closes[:-1]) / len(m1_closes[:-1])
        m1_bullish = m1_closes[-1] > recent_mean and m1_closes[-1] > m1_closes[-2]
        m1_bearish = m1_closes[-1] < recent_mean and m1_closes[-1] < m1_closes[-2]
    else:
        m1_bullish = bool(m1_closes and current_close > previous_close)
        m1_bearish = bool(m1_closes and current_close < previous_close)
    if m1_bullish or m1_bearish:
        conditions.append('m1_confirmation')
        reasons.append('M1 confirmation aligns with the trend and confirms the structure break.')

    bullish = False
    bearish = False
    tolerance = max(0.0001, abs(current_close) * 0.0005)
    previous_h4_low = float(h4[-2].get('low', 0) or 0) if len(h4) >= 2 else 0.0
    previous_h4_high = float(h4[-2].get('high', 0) or 0) if len(h4) >= 2 else 0.0
    confirmed_h4_bear_break = h4_bearish and h4_closes[-1] < previous_h4_low
    confirmed_h4_bull_break = h4_bullish and h4_closes[-1] > previous_h4_high

    if support_zone is not None and current_close > support_zone + tolerance and confirmed_h4_bull_break and m1_bullish:
        bullish = True
        conditions.append('demand_zone_retest')
        reasons.append('Demand-zone retest is holding and M1 confirmation supports continuation.')
    elif resistance_zone is not None and current_close < resistance_zone - tolerance and confirmed_h4_bear_break and m1_bearish:
        bearish = True
        conditions.append('supply_zone_retest')
        reasons.append('Supply-zone rejection is holding and M1 confirmation supports continuation.')

    if support_zone is not None and confirmed_h4_bear_break and m1_bearish:
        bearish = True
        conditions.append('supply_demand_flip')
        reasons.append('Price has flipped through the demand zone and is confirming bearish continuation.')
    if resistance_zone is not None and confirmed_h4_bull_break and m1_bullish:
        bullish = True
        conditions.append('supply_demand_flip')
        reasons.append('Price has flipped through the supply zone and is confirming bullish continuation.')

    recent_m1_high = max(float(c.get('high', 0) or 0) for c in m1[-6:]) if m1 else current_close
    recent_m1_low = min(float(c.get('low', 0) or 0) for c in m1[-6:]) if m1 else current_close
    if m1_bullish and current_close > recent_m1_high:
        bullish = True
        conditions.append('choch_structure_break')
        reasons.append('CHoCH structure break is in place and the M1 close is beyond recent structure.')
    if m1_bearish and current_close < recent_m1_low:
        bearish = True
        conditions.append('choch_structure_break')
        reasons.append('CHoCH structure break is in place and the M1 close is below recent structure.')

    zone_entry = False
    if support_zone is not None and resistance_zone is not None:
        zone_entry = support_zone <= current_close <= resistance_zone
    if zone_entry and not bullish and not bearish:
        conditions.append('unmitigated_zone_limit_entry')
        reasons.append('Price is sitting in a supply/demand zone without a confirmed CHoCH break or M1 follow-through; limit entry is rejected.')
    elif zone_entry and (h4_bullish or h4_bearish) and not (m1_bullish or m1_bearish):
        conditions.append('unmitigated_zone_limit_entry')
        reasons.append('Zone has not been confirmed by a valid M1 continuation break; limit entry is rejected.')
        bullish = False
        bearish = False

    if bullish and not bearish:
        bias = 'bullish'
        confidence = 0.78
    elif bearish and not bullish:
        bias = 'bearish'
        confidence = 0.78
    elif bullish and bearish:
        bias = 'neutral'
        confidence = 0.0
        reasons.append('Conflicting CHoCH structure and flip signals.')
        conditions.append('conflicting_choch_signals')
    else:
        bias = 'neutral'
        confidence = 0.0

    return StrategyResult(
        'CHoCH', bias, confidence, tuple(reasons), tuple(dict.fromkeys(conditions)), levels,
        round((time.perf_counter() - started) * 1000, 3),
    )


def _supply_demand_zones(m15: list[dict], m5: list[dict]) -> tuple[float | None, float | None]:
    if not m15 or not m5:
        return None, None
    recent_m15 = m15[-30:]
    recent_m5 = m5[-30:]
    if len(recent_m15) < 2 or len(recent_m5) < 2:
        return None, None

    m15_support = min(float(c.get('low', 0) or 0) for c in recent_m15)
    m15_resistance = max(float(c.get('high', 0) or 0) for c in recent_m15)
    m5_support = min(float(c.get('low', 0) or 0) for c in recent_m5)
    m5_resistance = max(float(c.get('high', 0) or 0) for c in recent_m5)

    support_zone = min(m15_support, m5_support) if m15_support and m5_support else None
    resistance_zone = max(m15_resistance, m5_resistance) if m15_resistance and m5_resistance else None
    if support_zone is not None and resistance_zone is not None and support_zone > resistance_zone:
        support_zone, resistance_zone = resistance_zone, support_zone
    return support_zone, resistance_zone


__all__ = [
    'CHoCH',
    'TIMEFRAME_LABELS',
    'check_news_window',
    'evaluate',
    'fetch_multi_timeframe_candles',
]


CHoCH = evaluate
