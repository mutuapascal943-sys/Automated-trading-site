import time
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
from .services.adapter_resolver import get_adapter_for_broker, build_credentials
from .models import EmailOTP, Trade, User, TradingSignal, PredictionRecord, RiskConfig, Notification
from .serializers import TradeCreateSerializer, TradingSignalSerializer
from .trading_bot.paper_broker import PaperBrokerAdapter
from .trading_bot.risk_engine import RiskEngine, PositionSizing
from .trading_bot.interface import OrderRequest
from .trading_bot.logging_utils import ConsoleAuditLogger
import logging

logger = logging.getLogger(__name__)
audit = ConsoleAuditLogger()

CANDLE_GRANULARITY = 900  # 15-minute candles for faster signal generation


@shared_task(
    bind=True,
    max_retries=3,
    default_retry_delay=60,
    acks_late=True,
)
def send_otp_email_task(self, user_id: int, purpose: str = '2fa') -> bool:
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
        try:
            self.retry(exc=e)
        except self.MaxRetriesExceededError:
            logger.error(f'Max retries exceeded for OTP email to user {user_id}')
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
    open_trades = Trade.objects.filter(is_live=True, status='OPEN')
    for trade in open_trades:
        try:
            user = trade.user
            adapter = get_adapter_for_broker(user.broker or '')
            creds = build_credentials(user)
            adapter.connect(creds)
            candles = adapter.get_candles(trade.symbol, CANDLE_GRANULARITY, 1)
            adapter.disconnect()
            if candles:
                CacheService.set_market_data(trade.symbol, {
                    'symbol': trade.symbol,
                    'price': str(candles[-1].close),
                    'timestamp': candles[-1].timestamp.isoformat(),
                })
        except Exception as e:
            logger.error(f'Failed to sync trade {trade.id}: {e}')


@shared_task
def check_paper_positions():
    open_paper_trades = Trade.objects.filter(is_live=False, status='OPEN').select_related('user')
    for trade in open_paper_trades:
        try:
            user = trade.user
            current_price = None

            if not user.paper_mode:
                try:
                    adapter = get_adapter_for_broker(user.broker or '')
                    creds = build_credentials(user)
                    adapter.connect(creds)
                    candles = adapter.get_candles(trade.symbol, CANDLE_GRANULARITY, 1)
                    adapter.disconnect()
                    if candles:
                        current_price = Decimal(str(candles[-1].close))
                except Exception:
                    pass

            if current_price is None:
                cached = CacheService.get_market_data(trade.symbol)
                if cached and cached.get('price'):
                    current_price = Decimal(cached['price'])

            if current_price is None:
                continue

            side = trade.action.lower()
            if side == 'buy':
                unrealized = (current_price - trade.entry_price) * Decimal(str(trade.volume))
                sl_hit = trade.stop_loss and current_price <= trade.stop_loss
                tp_hit = trade.take_profit and current_price >= trade.take_profit
            else:
                unrealized = (trade.entry_price - current_price) * Decimal(str(trade.volume))
                sl_hit = trade.stop_loss and current_price >= trade.stop_loss
                tp_hit = trade.take_profit and current_price <= trade.take_profit

            trade.current_price = current_price
            trade.unrealized_pnl = unrealized

            if sl_hit:
                exit_price = trade.stop_loss
                if side == 'buy':
                    pnl = (exit_price - trade.entry_price) * Decimal(str(trade.volume))
                else:
                    pnl = (trade.entry_price - exit_price) * Decimal(str(trade.volume))
                trade.status = 'CLOSED'
                trade.exit_price = exit_price
                trade.pnl = pnl
                trade.closed_at = timezone.now()
                trade.save()
                _update_user_balance(user, pnl)
                Notification.create_notification(
                    user=user, title=f'Stop Loss Hit — {trade.symbol}',
                    message=f'{trade.action} closed at {exit_price}, PnL: {pnl}',
                    notification_type='trade',
                )
            elif tp_hit:
                exit_price = trade.take_profit
                if side == 'buy':
                    pnl = (exit_price - trade.entry_price) * Decimal(str(trade.volume))
                else:
                    pnl = (trade.entry_price - exit_price) * Decimal(str(trade.volume))
                trade.status = 'CLOSED'
                trade.exit_price = exit_price
                trade.pnl = pnl
                trade.closed_at = timezone.now()
                trade.save()
                _update_user_balance(user, pnl)
                Notification.create_notification(
                    user=user, title=f'Take Profit Hit — {trade.symbol}',
                    message=f'{trade.action} closed at {exit_price}, PnL: {pnl}',
                    notification_type='trade',
                )
            else:
                trade.save()
        except Exception as e:
            logger.error(f'Failed to check paper position {trade.id}: {e}')


def _update_user_balance(user, pnl):
    user.balance += pnl
    user.save(update_fields=['balance'])


@shared_task
def reset_daily_trade_counts():
    User.objects.update(daily_trades_count=0, last_trade_date=timezone.now().date())
    logger.info('Daily trade counts reset')


@shared_task(
    soft_time_limit=120,
    time_limit=180,
)
def run_bot_cycle():
    users = User.objects.filter(
        risk_config__trading_enabled=True,
        risk_config__auto_execute=True,
    ).select_related('risk_config')[:50]

    for user in users:
        try:
            _run_single_user_bot(user)
        except Exception as e:
            logger.error(f'Bot cycle failed for user {user.id}: {e}')


def _run_single_user_bot(user):
    risk = RiskConfig.get_for_user(user)
    # The bot only trades the market the user has selected in the bot panel.
    selected = (user.selected_market or '').strip() or 'EUR/USD'
    symbols = [selected]

    open_trades = Trade.objects.filter(user=user, status='OPEN')
    open_count = open_trades.count()
    if open_count >= risk.max_open_positions:
        logger.info(f'User {user.id} hit max open positions ({risk.max_open_positions})')
        return

    today = timezone.now().date()
    if user.last_trade_date != today:
        user.daily_trades_count = 0
        user.last_trade_date = today

    if user.daily_trades_count >= risk.max_daily_trades:
        return

    adapter = None
    try:
        adapter = get_adapter_for_broker(user.broker or '')
        creds = build_credentials(user)
        adapter.connect(creds)
    except Exception as e:
        logger.warning(f'Could not connect broker adapter for user {user.id}: {e}')

    daily_pnl = _get_daily_pnl(user)

    from trading_app.trading_bot.technical_analyzer import analyze_technical

    for symbol in symbols:
        existing_open = Trade.objects.filter(user=user, symbol=symbol, status='OPEN').count()
        if existing_open > 0:
            continue

        if user.daily_trades_count >= risk.max_daily_trades:
            break

        if open_count >= risk.max_open_positions:
            break

        try:
            candles = CacheService.get_candles(symbol, CANDLE_GRANULARITY)
            if candles is not None:
                logger.info(f'Cache hit for {symbol} ({len(candles)} candles)')
            elif adapter is not None:
                try:
                    candles = adapter.get_candles(symbol, CANDLE_GRANULARITY, 50)
                    logger.info(f'Fetched {len(candles)} candles for {symbol} via {user.broker}')
                    if candles:
                        CacheService.set_candles(symbol, CANDLE_GRANULARITY, candles)
                except Exception as e:
                    logger.warning(f'Failed to fetch candles for {symbol}: {e}')
                    cached = CacheService.get_market_data(symbol)
                    if cached and cached.get('price'):
                        candles = _make_fallback_candle(symbol, cached)

            if not candles:
                continue

            recent_trades = list(
                Trade.objects.filter(user=user, symbol=symbol, status='CLOSED')
                .order_by('-closed_at')[:10]
                .values('action', 'pnl', 'confidence', 'symbol')
            )

            candle_dicts = []
            for c in candles:
                candle_dicts.append({
                    'open': float(c.open), 'high': float(c.high),
                    'low': float(c.low), 'close': float(c.close),
                    'volume': float(c.volume),
                    'time': int(c.timestamp.timestamp()),
                })

            analysis = analyze_technical(symbol, candle_dicts, trade_history=recent_trades)

            from trading_app.ml.inference import predict_signal
            ml_signal = predict_signal(candle_dicts, symbol=symbol)

            if ml_signal is not None and ml_signal.bias in ('bullish', 'bearish'):
                PredictionRecord.objects.create(
                    user=user,
                    symbol=symbol,
                    granularity=CANDLE_GRANULARITY,
                    horizon=ml_signal.horizon,
                    bias=ml_signal.bias,
                    confidence=ml_signal.confidence,
                    probability=ml_signal.probability,
                    features=ml_signal.features or {},
                    candle_time=candle_dicts[-1].get('time', int(time.time())),
                )

            if analysis.bias != 'neutral' and ml_signal is not None and ml_signal.bias != 'neutral':
                confidence = int((analysis.confidence * 0.6 + ml_signal.confidence * 0.4) * 100)
                ml_bias = 'BUY' if ml_signal.bias == 'bullish' else 'SELL'
                if ml_bias != ('BUY' if analysis.bias == 'bullish' else 'SELL'):
                    confidence = int(confidence * 0.8)
                signal_type = 'BUY' if analysis.bias == 'bullish' else 'SELL'
                source_detail = f' [ML: {ml_signal.model_info}]'
            elif analysis.bias != 'neutral':
                confidence = int(analysis.confidence * 100)
                signal_type = 'BUY' if analysis.bias == 'bullish' else 'SELL'
                source_detail = ''
            elif ml_signal is not None and ml_signal.bias != 'neutral' and ml_signal.confidence > 0.6:
                confidence = int(ml_signal.confidence * 100)
                signal_type = 'BUY' if ml_signal.bias == 'bullish' else 'SELL'
                source_detail = f' [ML-only: {ml_signal.model_info}]'
            else:
                continue

            if confidence < risk.min_confidence:
                logger.info(
                    f'Signal for {symbol} confidence {confidence}% below threshold {risk.min_confidence}%'
                )
                continue

            signal = TradingSignal.objects.create(
                user=user,
                symbol=symbol,
                signal_type=signal_type,
                confidence=confidence,
                reasoning=analysis.rationale + source_detail,
                source='SYSTEM',
                risk_level='LOW' if confidence < 40 else 'MEDIUM' if confidence < 70 else 'HIGH',
            )
            Notification.create_notification(
                user=user, title=f'Signal: {signal_type} {symbol}',
                message=f'Confidence: {confidence}% — {(analysis.rationale + source_detail)[:120]}',
                notification_type='signal',
            )
            audit.log_signal(symbol, signal_type, confidence, {
                'signal_id': signal.id, 'user_id': user.id, 'source': 'bot_cycle',
            })

            signal_data = {
                'symbol': symbol,
                'action': signal_type,
                'volume': float(risk.max_position_size),
                'order_type': 'MARKET',
            }
            inner_serializer = TradeCreateSerializer(data=signal_data)
            if not inner_serializer.is_valid():
                continue

            trade = inner_serializer.save(user=user, status='PENDING', is_live=not user.paper_mode)
            trade = _execute_trade(user, trade, risk, daily_pnl=daily_pnl)

            if trade.status == 'OPEN':
                signal.entry_price = trade.entry_price
                signal.stop_loss = trade.stop_loss
                signal.take_profit = trade.take_profit
                signal.is_executed = True
                signal.save()
                user.daily_trades_count += 1
                user.save()
                open_count += 1

                if trade.pnl is not None:
                    daily_pnl += trade.pnl

        except Exception as e:
            logger.error(f'Bot cycle symbol {symbol} failed for user {user.id}: {e}')

    if adapter is not None:
        try:
            adapter.disconnect()
        except Exception:
            pass


def _get_daily_pnl(user):
    today = timezone.now().date()
    from django.db.models import Sum
    result = Trade.objects.filter(
        user=user, status='CLOSED', closed_at__date=today
    ).aggregate(total=Sum('pnl'))
    return result['total'] or Decimal('0')


def _make_fallback_candle(symbol, cached=None):
    """Generate simulated candles when real market data is unavailable."""
    from .trading_bot.simulated_candles import generate_simulated_candles
    return generate_simulated_candles(symbol, count=50, granularity=CANDLE_GRANULARITY)


def _execute_trade(user, trade, risk, daily_pnl=None):
    open_trades = list(Trade.objects.filter(user=user, status='OPEN'))

    sizing = PositionSizing(
        method='fixed',
        fixed_volume=Decimal(str(trade.volume)),
        max_position_size=risk.max_position_size,
    )

    engine = RiskEngine(
        sizing=sizing,
        max_exposure_percent=risk.max_exposure_percent,
        max_daily_loss_percent=risk.max_drawdown,
        trailing_stop_percent=risk.trailing_stop_percent,
        account_balance=user.balance,
    )

    if daily_pnl is not None:
        engine.record_trade_pnl(daily_pnl)

    fake_signal = OrderRequest(
        symbol=trade.symbol,
        side=trade.action.lower(),
        order_type=trade.order_type.lower(),
        volume=Decimal(str(trade.volume)),
        price=trade.entry_price,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
    )

    risk_signal = engine.apply_rules(trade.symbol, fake_signal, user.balance, open_trades)
    if risk_signal is None:
        trade.status = 'CANCELLED'
        trade.save()
        audit.log_risk('rules_engine', triggered=True, details={
            'trade_id': trade.id, 'symbol': trade.symbol, 'reason': 'risk_rules_rejected',
        })
        return trade

    if user.paper_mode:
        paper = PaperBrokerAdapter(initial_balance=user.balance)
        paper.connect({})
        try:
            order = paper.place_order(OrderRequest(
                symbol=trade.symbol,
                side=trade.action.lower(),
                order_type=trade.order_type.lower(),
                volume=Decimal(str(trade.volume)),
                price=trade.entry_price,
                stop_loss=risk_signal.stop_loss,
                take_profit=risk_signal.take_profit,
            ))
            trade.status = 'OPEN'
            trade.entry_price = Decimal(str(order.filled_price)) if order.filled_price else trade.entry_price
            trade.current_price = trade.entry_price
            trade.unrealized_pnl = Decimal('0')
            trade.stop_loss = risk_signal.stop_loss
            trade.take_profit = risk_signal.take_profit
            trade.executed_at = timezone.now()
            trade.is_live = False
            trade.save()
            Notification.create_notification(
                user=user, title='Paper Trade Opened',
                message=f'{trade.action} {trade.symbol} at {trade.entry_price}',
                notification_type='trade',
            )
            audit.log_order('paper_filled', order.id, {'trade_id': trade.id, 'symbol': trade.symbol})
        except Exception as e:
            logger.error(f'Paper execution failed for trade {trade.id}: {e}')
            trade.status = 'FAILED'
            trade.save()
        return trade

    adapter = get_adapter_for_broker(user.broker or '')
    creds = build_credentials(user)
    try:
        adapter.connect(creds)
        order = adapter.place_order(OrderRequest(
            symbol=trade.symbol,
            side=trade.action.lower(),
            order_type=trade.order_type.lower(),
            volume=Decimal(str(trade.volume)),
            price=trade.entry_price,
            stop_loss=risk_signal.stop_loss,
            take_profit=risk_signal.take_profit,
        ))
        adapter.disconnect()
        trade.status = 'OPEN'
        trade.entry_price = Decimal(str(order.filled_price)) if order.filled_price else trade.entry_price
        trade.broker_trade_id = order.broker_order_id or ''
        trade.stop_loss = risk_signal.stop_loss
        trade.take_profit = risk_signal.take_profit
        trade.executed_at = timezone.now()
        trade.is_live = True
        trade.save()
        Notification.create_notification(
            user=user, title='Live Trade Opened',
            message=f'{trade.action} {trade.symbol} at {trade.entry_price}',
            notification_type='trade',
        )
        audit.log_order('live_filled', order.id, {'trade_id': trade.id, 'symbol': trade.symbol})
    except Exception as e:
        logger.error(f'Live execution failed for trade {trade.id}: {e}')
        trade.status = 'FAILED'
        trade.save()
        Notification.create_notification(
            user=user, title=f'Trade Failed — {trade.symbol}',
            message=f'{trade.action} {trade.symbol} failed: {str(e)[:200]}',
            notification_type='trade',
        )
        audit.log_error('live_execution', str(e), {'trade_id': trade.id, 'symbol': trade.symbol})

    return trade


# ---------------------------------------------------------------------------
# Position Monitoring — SL/TP hit detection + trailing stops
# ---------------------------------------------------------------------------

@shared_task
def monitor_live_positions():
    """
    Check all open trades (paper + live) against current price.
    Closes trades when SL/TP is hit, applies trailing stops,
    updates unrealized P&L, and notifies the user.
    """
    open_trades = Trade.objects.filter(status='OPEN').select_related('user')
    for trade in open_trades:
        try:
            _monitor_single_trade(trade)
        except Exception as e:
            logger.error(f'Position monitor failed for trade {trade.id}: {e}')


def _monitor_single_trade(trade):
    user = trade.user
    current_price = _fetch_current_price(trade.symbol, user)
    if current_price is None:
        return

    side = trade.action.lower()
    entry = trade.entry_price
    sl = trade.stop_loss
    tp = trade.take_profit

    if side == 'buy':
        unrealized = (current_price - entry) * Decimal(str(trade.volume))
        sl_hit = sl is not None and current_price <= sl
        tp_hit = tp is not None and current_price >= tp
    else:
        unrealized = (entry - current_price) * Decimal(str(trade.volume))
        sl_hit = sl is not None and current_price >= sl
        tp_hit = tp is not None and current_price <= tp

    trade.current_price = current_price
    trade.unrealized_pnl = unrealized

    if sl_hit:
        _close_trade(trade, sl, 'Stop Loss Hit', unrealized)
        return

    if tp_hit:
        _close_trade(trade, tp, 'Take Profit Hit', unrealized)
        return

    # Trailing stop: move SL in profit direction if price has moved enough
    risk = RiskConfig.get_for_user(user)
    if risk.trailing_stop_percent and risk.trailing_stop_percent > 0:
        _apply_trailing_stop(trade, current_price, risk.trailing_stop_percent)

    trade.save()


def _fetch_current_price(symbol, user):
    """Fetch current price from broker adapter or cache."""
    try:
        adapter = get_adapter_for_broker(user.broker or '')
        creds = build_credentials(user)
        adapter.connect(creds)
        candles = adapter.get_candles(symbol, CANDLE_GRANULARITY, 1)
        adapter.disconnect()
        if candles:
            return Decimal(str(candles[-1].close))
    except Exception as e:
        logger.warning(f'Price fetch failed for {symbol}: {e}')

    cached = CacheService.get_market_data(symbol)
    if cached and cached.get('price'):
        return Decimal(cached['price'])

    return None


def _close_trade(trade, exit_price, reason, pnl):
    """Close a trade, update balance, and notify user."""
    user = trade.user
    trade.status = 'CLOSED'
    trade.exit_price = exit_price
    trade.pnl = pnl
    trade.closed_at = timezone.now()
    trade.save()

    _update_user_balance(user, pnl)

    emoji = '+' if pnl >= 0 else ''
    Notification.create_notification(
        user=user,
        title=f'{reason} — {trade.symbol}',
        message=f'{trade.action} {trade.symbol} closed at {exit_price}, PnL: {emoji}{pnl}',
        notification_type='trade',
    )
    audit.log_order('position_closed', str(trade.id), {
        'symbol': trade.symbol, 'exit_price': str(exit_price),
        'pnl': str(pnl), 'reason': reason,
    })


def _apply_trailing_stop(trade, current_price, trailing_pct):
    """
    Move stop loss in the profitable direction when price has moved
    beyond entry by at least 1x the initial SL distance.
    """
    if trade.stop_loss is None or trade.entry_price is None:
        return

    side = trade.action.lower()
    trail_frac = Decimal(str(trailing_pct)) / Decimal('100')

    if side == 'buy':
        initial_sl_dist = trade.entry_price - trade.stop_loss
        if initial_sl_dist <= 0:
            return
        new_sl = current_price - (current_price * trail_frac)
        if new_sl > trade.stop_loss and current_price > trade.entry_price + initial_sl_dist:
            trade.stop_loss = new_sl.quantize(Decimal('0.00001'))
            logger.info(f'Trailing stop moved to {trade.stop_loss} for trade {trade.id}')
    else:
        initial_sl_dist = trade.stop_loss - trade.entry_price
        if initial_sl_dist <= 0:
            return
        new_sl = current_price + (current_price * trail_frac)
        if new_sl < trade.stop_loss and current_price < trade.entry_price - initial_sl_dist:
            trade.stop_loss = new_sl.quantize(Decimal('0.00001'))
            logger.info(f'Trailing stop moved to {trade.stop_loss} for trade {trade.id}')


# ---------------------------------------------------------------------------
# Prediction Feedback Loop — resolve ML predictions against realized price
# ---------------------------------------------------------------------------

@shared_task(
    soft_time_limit=240,
    time_limit=300,
)
def resolve_pending_predictions():
    """Check pending PredictionRecords that have aged past their horizon and
    mark them correct/incorrect against realized price. Only predictions made
    with real (non-paper) market data become training feedback."""
    pending = list(
        PredictionRecord.objects
        .filter(resolved=False)
        .select_related('user')
        .order_by('predicted_at')[:200]
    )
    resolved_count = 0
    for pred in pending:
        try:
            if _resolve_prediction(pred):
                resolved_count += 1
        except Exception as e:
            logger.error(f'Failed to resolve prediction {pred.id}: {e}')
    logger.info(f'Resolved {resolved_count}/{len(pending)} pending predictions')
    return resolved_count


def _resolve_prediction(pred) -> bool:
    """Resolve one prediction. Returns True if it reached a final state."""
    if pred.resolved:
        return True

    elapsed = (timezone.now() - pred.predicted_at).total_seconds()
    horizon_secs = pred.horizon * pred.granularity
    if elapsed < horizon_secs * 2:
        return False

    user = pred.user
    if user is None or user.paper_mode:
        pred.resolved = True
        pred.resolved_at = timezone.now()
        pred.save(update_fields=['resolved', 'resolved_at'])
        return True

    candles = _fetch_prediction_candles(pred, user)
    if not candles:
        if elapsed > 3 * 86400:
            pred.resolved = True
            pred.resolved_at = timezone.now()
            pred.save(update_fields=['resolved', 'resolved_at'])
            return True
        return False

    ordered = sorted(candles, key=lambda c: c.timestamp)
    start_idx = next(
        (i for i, c in enumerate(ordered) if c.timestamp.timestamp() >= pred.candle_time),
        None,
    )
    if start_idx is None or start_idx + pred.horizon >= len(ordered):
        pred.resolved = True
        pred.resolved_at = timezone.now()
        pred.save(update_fields=['resolved', 'resolved_at'])
        return True

    entry = float(ordered[start_idx].close)
    exit_price = float(ordered[start_idx + pred.horizon].close)

    realized = 1 if exit_price > entry else 0
    pred.realized_target = realized
    pred.prediction_correct = (
        (pred.bias == 'bullish' and realized == 1)
        or (pred.bias == 'bearish' and realized == 0)
    )
    pred.resolved = True
    pred.resolved_at = timezone.now()
    pred.save(update_fields=['realized_target', 'prediction_correct', 'resolved', 'resolved_at'])
    return True


def _fetch_prediction_candles(pred, user):
    """Fetch candle history from the user's broker for outcome evaluation."""
    count = min(
        5000,
        max(100, int((time.time() - pred.candle_time) / pred.granularity) + pred.horizon + 10),
    )
    try:
        adapter = get_adapter_for_broker(user.broker or '')
        creds = build_credentials(user)
        adapter.connect(creds)
        candles = adapter.get_candles(pred.symbol, pred.granularity, count)
        adapter.disconnect()
        return candles or []
    except Exception as e:
        logger.warning(f'Failed to fetch prediction history for {pred.id}: {e}')
        return []
