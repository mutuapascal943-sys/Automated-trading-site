import threading
import time
from celery import shared_task
from django.utils import timezone
from django.conf import settings
from decouple import config
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

CANDLE_GRANULARITY = 300  # 5-minute candles for faster signal generation


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
    # Signal/prediction generation runs for every user with trading enabled.
    # auto_execute only controls whether trades are opened — not whether the
    # bot produces signals, so the Analytics/History panels stay populated
    # even for users who prefer to review before executing.
    users = User.objects.filter(
        risk_config__trading_enabled=True,
    ).select_related('risk_config')[:50]

    for user in users:
        try:
            _run_single_user_bot(user)
        except Exception as e:
            logger.error(f'Bot cycle failed for user {user.id}: {e}')


def _record_prediction(user, symbol, granularity, candle_time, ml_signal, entry_price=None):
    """Create a new live prediction only when no prediction is currently open
    for this symbol. The active prediction is held until it resolves as
    correct or missed instead of being replaced on every bot cycle, so the
    History/Analytics panels show one prediction per candle outcome."""
    if PredictionRecord.objects.filter(
        user=user, symbol=symbol, granularity=granularity, resolved=False,
    ).exists():
        return None
    return PredictionRecord.objects.create(
        user=user,
        symbol=symbol,
        granularity=granularity,
        horizon=ml_signal.horizon,
        bias=ml_signal.bias,
        confidence=ml_signal.confidence,
        probability=ml_signal.probability,
        features=ml_signal.features or {},
        candle_time=candle_time,
        entry_price=Decimal(str(entry_price)) if entry_price is not None else None,
    )


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
        # Paper mode: keep the simulated market walking so predictions made on
        # live data still resolve against simulated outcomes without a browser
        # open streaming ticks. 60 ticks/minute mirrors live tick speed.
        if user.paper_mode:
            from trading_app.trading_bot.simulated_market import SimulatedMarket
            SimulatedMarket.advance(selected, ticks=60)

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

            if not candles and user.paper_mode:
                # Unknown broker / no live adapter in paper mode: fall back to
                # the shared simulated series so the bot still generates
                # predictions and signals for every paper user.
                from trading_app.trading_bot.simulated_market import SimulatedMarket
                candles = SimulatedMarket.get_candles(symbol, CANDLE_GRANULARITY, 50)

            if not candles:
                continue

            recent_trades = list(
                Trade.objects.filter(user=user, symbol=symbol, status='CLOSED')
                .order_by('-closed_at')[:10]
                .values('action', 'pnl', 'symbol')
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
                pred_candle_time = candle_dicts[-1].get('time', int(time.time()))
                _record_prediction(
                    user, symbol, CANDLE_GRANULARITY, pred_candle_time,
                    ml_signal,
                    entry_price=candle_dicts[-1].get('close'),
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

            # Execution is opt-in per risk config: generate signals for every
            # enabled user but only open trades when auto_execute is on.
            if not risk.auto_execute:
                logger.info(
                    f'Signal {signal_type} {symbol} for user {user.id} generated '
                    f'(auto_execute off, no trade opened)'
                )
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
    """Check pending PredictionRecords whose horizon window has passed and
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

    # A prediction can only be judged once the `horizon`th candle after the
    # entry candle has closed. Resolve as soon as that window has passed so
    # the History/Analytics panels update in real time instead of lingering
    # in "Pending" for twice the horizon.
    horizon_secs = pred.horizon * pred.granularity
    now_ts = time.time()
    elapsed = now_ts - pred.predicted_at.timestamp()
    if pred.candle_time and pred.candle_time > 0:
        entry_close = pred.candle_time + pred.granularity
        outcome_close = entry_close + horizon_secs
    else:
        outcome_close = pred.predicted_at.timestamp() + horizon_secs
    if now_ts < outcome_close:
        return False

    user = pred.user
    if user is None or user.paper_mode:
        # Prefer REAL market candles so paper predictions resolve against
        # actual price moves (Deriv candle history is public and needs no
        # token). Fall back to the shared simulated market when offline.
        candles = _fetch_real_outcome_candles(pred)
        if not candles:
            candles = _fetch_paper_prediction_candles(pred)
        if candles and _apply_prediction_outcome(pred, sorted(candles, key=lambda c: c.timestamp)):
            return True
        # The market may still be filling in bars after the entry candle.
        # Wait for a later cycle instead of discarding the outcome; only give
        # up after a long grace period so records never dangle.
        if elapsed > 3 * 86400:
            pred.resolved = True
            pred.resolved_at = timezone.now()
            pred.save(update_fields=['resolved', 'resolved_at'])
            return True
        return False

    candles = _fetch_prediction_candles(pred, user)
    if not candles:
        if elapsed > 3 * 86400:
            pred.resolved = True
            pred.resolved_at = timezone.now()
            pred.save(update_fields=['resolved', 'resolved_at'])
            return True
        return False

    ordered = sorted(candles, key=lambda c: c.timestamp)
    if not _apply_prediction_outcome(pred, ordered):
        pred.resolved = True
        pred.resolved_at = timezone.now()
        pred.save(update_fields=['resolved', 'resolved_at'])
    return True


def _apply_prediction_outcome(pred, ordered) -> bool:
    """Compare the entry candle close with the close `horizon` bars later and
    store the realized target. Returns True when an outcome was computed."""
    start_idx = next(
        (i for i, c in enumerate(ordered) if c.timestamp.timestamp() >= pred.candle_time),
        None,
    )
    if start_idx is None or start_idx + pred.horizon >= len(ordered):
        return False

    entry = float(ordered[start_idx].close)
    exit_price = float(ordered[start_idx + pred.horizon].close)

    realized = 1 if exit_price > entry else 0
    pred.entry_price = ordered[start_idx].close
    pred.exit_price = ordered[start_idx + pred.horizon].close
    pred.realized_target = realized
    pred.prediction_correct = (
        (pred.bias == 'bullish' and realized == 1)
        or (pred.bias == 'bearish' and realized == 0)
    )
    pred.resolved = True
    pred.resolved_at = timezone.now()
    pred.save(update_fields=[
        'entry_price', 'exit_price', 'realized_target', 'prediction_correct',
        'resolved', 'resolved_at',
    ])
    return True


def _fetch_paper_prediction_candles(pred):
    """Fetch outcome candles from the shared simulated market (paper mode)."""
    from trading_app.trading_bot.simulated_market import SimulatedMarket
    try:
        candles = SimulatedMarket.get_candles(pred.symbol, pred.granularity, count=2000)
        return candles or []
    except Exception as e:
        logger.warning(f'Failed to fetch paper prediction history for {pred.id}: {e}')
        return []


def _deriv_symbol_for_label(label: str) -> str:
    """Map a frontend market label ('EUR/USD') to its Deriv API symbol
    ('frxEURUSD'). Falls back to the label unchanged for unknown symbols."""
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS
    from trading_app.ml.symbols import deriv_symbol_for
    prefix = MARKET_SYMBOLS.get(label)
    if prefix:
        return deriv_symbol_for(prefix)
    return deriv_symbol_for(label)


def _fetch_real_outcome_candles(pred):
    """Fetch REAL market candles for the outcome window.

    Uses Deriv's public candle history (works without an API token) via the
    same async fetcher the training pipeline uses, mapped to the correct
    Deriv symbol for the prediction's market label. This makes paper-mode
    predictions resolve against actual price moves instead of the simulated
    market. Returns [] if Deriv is unreachable — the caller falls back to the
    simulated market."""
    import asyncio
    from decimal import Decimal
    from datetime import datetime, timezone

    from trading_app.ml.data_collector import _fetch_all
    from trading_app.trading_bot.interface import Candle
    from trading_app.trading_bot.deriv_adapter import GRANULARITY_MAP

    symbol = _deriv_symbol_for_label(pred.symbol)
    gran = pred.granularity
    if gran not in GRANULARITY_MAP:
        return []

    count = min(
        5000,
        max(100, int((time.time() - pred.candle_time) / gran) + pred.horizon + 10),
    )
    try:
        loop = asyncio.new_event_loop()
        try:
            rows = loop.run_until_complete(
                _fetch_all(symbol, gran, count, int(time.time()), config("DERIV_APP_ID", default="1089"), None)
            )
        finally:
            loop.close()
        candles = [
            Candle(
                symbol=pred.symbol,
                open=Decimal(str(r["open"])),
                high=Decimal(str(r["high"])),
                low=Decimal(str(r["low"])),
                close=Decimal(str(r["close"])),
                volume=Decimal(str(r["volume"])),
                timestamp=datetime.fromtimestamp(r["time"], tz=timezone.utc),
                granularity=gran,
            )
            for r in rows
        ]
        return candles
    except Exception as e:
        logger.warning(f'Failed to fetch real prediction history for {pred.id}: {e}')
        return []


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
        symbol = pred.symbol
        if type(adapter).__name__ == 'DerivAdapter':
            symbol = _deriv_symbol_for_label(pred.symbol)
        candles = adapter.get_candles(symbol, pred.granularity, count)
        adapter.disconnect()
        return candles or []
    except Exception as e:
        logger.warning(f'Failed to fetch prediction history for {pred.id}: {e}')
        return []


# ---------------------------------------------------------------------------
# Automatic feedback retraining — scheduled, background, non-blocking.
# ---------------------------------------------------------------------------

_retrain_lock = threading.Lock()
_retrain_in_progress = False
# Minimum NEW resolved outcomes per symbol before a retrain is worth running.
RETRAIN_MIN_NEW = config('RETRAIN_MIN_NEW', default=25, cast=int)


@shared_task
def retrain_models_with_feedback():
    """Check for enough new resolved predictions and, if so, retrain the
    affected symbols in a background thread so the bot cycle is never blocked."""
    global _retrain_in_progress
    with _retrain_lock:
        if _retrain_in_progress:
            return
        _retrain_in_progress = True
    threading.Thread(target=_feedback_retrain_worker, daemon=True).start()


def _feedback_retrain_worker():
    global _retrain_in_progress
    try:
        _run_feedback_retrain()
    except Exception as e:
        logger.error("Feedback retrain failed: %s", e, exc_info=True)
    finally:
        with _retrain_lock:
            _retrain_in_progress = False


def _run_feedback_retrain():
    import json
    from django.db.models import Max
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS, _model_exists
    from trading_app.ml import inference as ml_inference
    from trading_app.ml.feedback import load_feedback_rows
    from trading_app.ml.features import FEATURE_COLUMNS
    from trading_app.ml.pipeline import run_pipeline
    from trading_app.ml.symbols import deriv_symbol_for

    marker_path = ml_inference.OUTPUT_DIR / 'feedback_marker.json'
    marker = {}
    if marker_path.exists():
        try:
            marker = json.loads(marker_path.read_text())
        except Exception:
            marker = {}

    changed = False
    for label, symbol in MARKET_SYMBOLS.items():
        last_id = int(marker.get(symbol, 0))
        new_count = PredictionRecord.objects.filter(
            resolved=True,
            realized_target__isnull=False,
            symbol=label,
            granularity=CANDLE_GRANULARITY,
            id__gt=last_id,
        ).count()
        if new_count < RETRAIN_MIN_NEW:
            continue
        if not _model_exists(symbol, str(ml_inference.OUTPUT_DIR)):
            continue

        feedback_rows = load_feedback_rows(symbol, FEATURE_COLUMNS, granularity=CANDLE_GRANULARITY)
        if not feedback_rows:
            continue

        logger.info('Feedback retrain triggered for %s (%d new outcomes)', symbol, new_count)
        try:
            run_pipeline(
                symbol=symbol,
                data_symbol=deriv_symbol_for(symbol),
                granularity=CANDLE_GRANULARITY,
                years=2.0,
                horizon=4,
                threshold_pct=0.1,
                n_windows=5,
                output_dir=str(ml_inference.OUTPUT_DIR),
                feedback_rows=feedback_rows,
            )
            max_id = PredictionRecord.objects.filter(
                resolved=True, realized_target__isnull=False, symbol=label,
                granularity=CANDLE_GRANULARITY,
            ).aggregate(m=Max('id'))['m']
            if max_id:
                marker[symbol] = max_id
            changed = True
            ml_inference._model_cache = None
            ml_inference._model_cache_key = None
            logger.info('Feedback retrain completed for %s', symbol)
        except Exception as e:
            logger.error('Feedback retrain failed for %s: %s', symbol, e)

    if changed:
        try:
            marker_path.write_text(json.dumps(marker, indent=2))
        except Exception as e:
            logger.error('Failed to write feedback marker: %s', e)
