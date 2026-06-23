from celery import shared_task
from django.utils import timezone
from django.conf import settings
from decimal import Decimal
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
from .services.adapter_resolver import get_adapter_for_broker, build_credentials
from .models import EmailOTP, Trade, User, TradingSignal, RiskConfig, Notification
from .serializers import TradeCreateSerializer, TradingSignalSerializer
from .trading_bot.paper_broker import PaperBrokerAdapter
from .trading_bot.risk_engine import RiskEngine, PositionSizing
from .trading_bot.interface import OrderRequest
from .trading_bot.logging_utils import ConsoleAuditLogger
from .trading_bot.llm_analyzer import LLMAnalyzer, OpenAIProvider
import logging

logger = logging.getLogger(__name__)
audit = ConsoleAuditLogger()


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
    open_trades = Trade.objects.filter(is_live=True, status='OPEN')
    for trade in open_trades:
        try:
            user = trade.user
            adapter = get_adapter_for_broker(user.broker or '')
            creds = build_credentials(user)
            adapter.connect(creds)
            candles = adapter.get_candles(trade.symbol, 3600, 1)
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
                    candles = adapter.get_candles(trade.symbol, 3600, 1)
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
                Notification.create_notification(
                    user=user, title=f'Take Profit Hit — {trade.symbol}',
                    message=f'{trade.action} closed at {exit_price}, PnL: {pnl}',
                    notification_type='trade',
                )
            else:
                trade.save()
        except Exception as e:
            logger.error(f'Failed to check paper position {trade.id}: {e}')


@shared_task
def reset_daily_trade_counts():
    User.objects.update(daily_trades_count=0, last_trade_date=timezone.now().date())
    logger.info('Daily trade counts reset')


@shared_task
def run_bot_cycle():
    users = User.objects.filter(
        risk_config__trading_enabled=True,
        risk_config__auto_execute=True,
    ).select_related('risk_config')

    for user in users:
        try:
            _run_single_user_bot(user)
        except Exception as e:
            logger.error(f'Bot cycle failed for user {user.id}: {e}')


def _run_single_user_bot(user):
    risk = RiskConfig.get_for_user(user)
    symbols = user.watchlist or ['EUR/USD', 'GBP/USD', 'XAU/USD', 'BTC/USD']

    from decouple import config as decouple_config
    api_key = decouple_config('OPENAI_API_KEY', default='')
    if not api_key:
        logger.warning(f'No OPENAI_API_KEY for bot cycle user {user.id}')
        return

    provider = OpenAIProvider(api_key=api_key)
    analyzer = LLMAnalyzer(provider=provider)

    adapter = None
    try:
        adapter = get_adapter_for_broker(user.broker or '')
        creds = build_credentials(user)
        adapter.connect(creds)
    except Exception as e:
        logger.warning(f'Could not connect broker adapter for user {user.id}: {e}')

    for symbol in symbols:
        existing_open = Trade.objects.filter(user=user, symbol=symbol, status='OPEN').count()
        if existing_open > 0:
            continue

        today = timezone.now().date()
        if user.last_trade_date != today:
            user.daily_trades_count = 0
            user.last_trade_date = today

        if user.daily_trades_count >= risk.max_daily_trades:
            break

        try:
            candles = []
            if adapter is not None:
                try:
                    candles = adapter.get_candles(symbol, 3600, 30)
                    logger.info(f'Fetched {len(candles)} candles for {symbol} via Deriv')
                except Exception as e:
                    logger.warning(f'Failed to fetch candles for {symbol}: {e}')
                    cached = CacheService.get_market_data(symbol)
                    if cached and cached.get('price'):
                        candles = _make_fallback_candle(symbol, cached)

            if not candles:
                continue

            analysis = analyzer.analyze(symbol, candles)

            if analysis.bias == 'neutral':
                continue

            signal_type = 'BUY' if analysis.bias == 'bullish' else 'SELL'
            confidence = int(analysis.confidence * 100)

            signal = TradingSignal.objects.create(
                user=user,
                symbol=symbol,
                signal_type=signal_type,
                confidence=confidence,
                reasoning=analysis.rationale,
                source='AI',
                risk_level='MEDIUM',
            )
            Notification.create_notification(
                user=user, title=f'Signal: {signal_type} {symbol}',
                message=f'Confidence: {confidence}% — {analysis.rationale[:100]}',
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
            _execute_trade(user, trade, risk)

            signal.is_executed = True
            signal.save()

            user.daily_trades_count += 1
            user.save()

        except Exception as e:
            logger.error(f'Bot cycle symbol {symbol} failed for user {user.id}: {e}')

    if adapter is not None:
        try:
            adapter.disconnect()
        except Exception:
            pass


def _make_fallback_candle(symbol, cached):
    from .trading_bot.interface import Candle
    from decimal import Decimal
    from datetime import datetime, timezone
    price = Decimal(str(cached.get('price', 0)))
    ts_str = cached.get('timestamp')
    ts = datetime.fromisoformat(ts_str) if ts_str else datetime.now(timezone.utc)
    return [Candle(
        symbol=symbol,
        open=price, high=price, low=price, close=price,
        volume=Decimal('0'), timestamp=ts, granularity=3600,
    )]


def _execute_trade(user, trade, risk):
    engine = RiskEngine(
        sizing=PositionSizing(
            method='fixed',
            fixed_volume=Decimal(str(trade.volume)),
            max_position_size=risk.max_position_size,
        ),
        max_exposure_percent=Decimal('50'),
        max_daily_loss_percent=risk.max_drawdown,
    )

    fake_signal = OrderRequest(
        symbol=trade.symbol,
        side=trade.action.lower(),
        order_type=trade.order_type.lower(),
        volume=Decimal(str(trade.volume)),
        price=trade.entry_price,
        stop_loss=trade.stop_loss,
        take_profit=trade.take_profit,
    )

    risk_signal = engine.apply_rules(trade.symbol, fake_signal, user.balance)
    if risk_signal is None:
        trade.status = 'CANCELLED'
        trade.save()
        return

    if user.paper_mode:
        paper = PaperBrokerAdapter(initial_balance=user.balance)
        paper.connect({})
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
        trade.executed_at = timezone.now()
        trade.is_live = False
        trade.save()
        Notification.create_notification(
            user=user, title=f'Paper Trade Opened',
            message=f'{trade.action} {trade.symbol} at {trade.entry_price}',
            notification_type='trade',
        )
        return

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
        trade.executed_at = timezone.now()
        trade.is_live = True
        trade.save()
    except Exception as e:
        logger.error(f'Live execution failed for trade {trade.id}: {e}')
        trade.status = 'OPEN'
        trade.executed_at = timezone.now()
        trade.is_live = False
        trade.save()
