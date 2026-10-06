import secrets
import time
import logging
import decimal
from concurrent.futures import ThreadPoolExecutor, as_completed
from functools import wraps
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework import status, viewsets, generics, permissions
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.throttling import UserRateThrottle
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from .models import (
    Trade, TradingSignal,
    Subscription, EmailOTP, SecurityQuestion, Notification, RiskConfig,
)
from .serializers import (
    TradeSerializer, TradeCreateSerializer, TradingSignalSerializer,
    SubscriptionSerializer, MarketDataSerializer, BrokerConfigSerializer,
    OTPVerifySerializer, UserSerializer, RiskConfigSerializer,
)
from .services.cache_service import CacheService
from .services.email_service import send_otp_email
from .services.credential_encrypt import encrypt, decrypt
from .services.adapter_resolver import get_adapter_for_broker, build_credentials
from .ml.symbols import deriv_symbol_for
from .services.ticker_bridge import TickerBridge
from .trading_bot.paper_broker import PaperBrokerAdapter
from .trading_bot.risk_engine import RiskEngine, PositionSizing
from .trading_bot.interface import OrderRequest as RiskOrderRequest
from .trading_bot.logging_utils import ConsoleAuditLogger
from .decorators import two_factor_required_api
from .services.admin_access import AuthorizedAdminPermission

User = get_user_model()
logger = logging.getLogger(__name__)
audit = ConsoleAuditLogger()


def create_notification(user, title, message='', notification_type='system'):
    Notification.create_notification(
        user=user, title=title, message=message, notification_type=notification_type
    )


class UserViewSet(viewsets.ModelViewSet):
    queryset = User.objects.all()
    serializer_class = UserSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return User.objects.filter(id=self.request.user.id)


class TradeViewSet(viewsets.ModelViewSet):
    serializer_class = TradeSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Trade.objects.filter(user=self.request.user).select_related('user')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)

    def create(self, request, *args, **kwargs):
        return _execution_disabled_response()

    def update(self, request, *args, **kwargs):
        return _execution_disabled_response()

    def partial_update(self, request, *args, **kwargs):
        return _execution_disabled_response()

    def destroy(self, request, *args, **kwargs):
        return _execution_disabled_response()


class SignalViewSet(viewsets.ModelViewSet):
    serializer_class = TradingSignalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        access = _subscription_access_response(self.request.user)
        if access is not None:
            return TradingSignal.objects.none()
        return TradingSignal.objects.filter(user=self.request.user).order_by('-created_at')

    def create(self, request, *args, **kwargs):
        return Response({
            'error': 'Signals can only be created by the centralized live analysis pipeline.',
        }, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def update(self, request, *args, **kwargs):
        return Response({'error': 'Signals are immutable.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

    def partial_update(self, request, *args, **kwargs):
        return Response({'error': 'Signals are immutable.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)


def _subscription_access_response(user):
    from trading_app.services.subscription_access import bot_access_status

    access = bot_access_status(user)
    if access['has_access']:
        return None
    return Response({
        'error': 'Bot access is unavailable. Start a subscription or contact support.',
        'bot_access': {'has_access': False, 'status': access['status']},
        'subscription': _serialize_subscription_status(access['subscription']),
    }, status=status.HTTP_403_FORBIDDEN)


def _serialize_subscription_status(access: dict) -> dict:
    expires_at = access['expires_at']
    return {
        'has_access': access['has_access'],
        'status': access['status'],
        'tier': access['tier'],
        'expires_at': expires_at.isoformat() if expires_at else None,
        'seconds_remaining': access['seconds_remaining'],
        'signals_used': access.get('signals_used'),
        'signals_remaining': access.get('signals_remaining'),
    }


def _execution_disabled_response():
    return Response({
        'error': 'Trade execution is disabled. This system provides market data and analysis signals only.',
        'execution_enabled': False,
    }, status=status.HTTP_410_GONE)


def _measure_analysis_duration(view):
    @wraps(view)
    def wrapped(request, *args, **kwargs):
        started = time.perf_counter()
        response = view(request, *args, **kwargs)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        if isinstance(response.data, dict):
            response.data['analysis_duration_ms'] = duration_ms
        response['X-Analysis-Duration-Ms'] = str(duration_ms)
        return response
    return wrapped


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def market_data_view(request):
    symbol = request.query_params.get('symbol', 'EUR/USD')
    try:
        granularity = int(request.query_params.get('granularity', 900))
    except ValueError:
        return Response({'error': 'granularity must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS
    from trading_app.trading_bot.deriv_adapter import GRANULARITY_MAP
    if symbol not in MARKET_SYMBOLS:
        return Response({'error': 'Unsupported market'}, status=status.HTTP_400_BAD_REQUEST)
    if granularity not in GRANULARITY_MAP:
        return Response({'error': 'Unsupported granularity'}, status=status.HTTP_400_BAD_REQUEST)

    candles, source = _fetch_chart_candles_with_source(request.user, symbol, 2, granularity)
    if not candles:
        return Response({'symbol': symbol, 'error': 'Live market data unavailable'},
                        status=status.HTTP_503_SERVICE_UNAVAILABLE)
    latest = candles[-1]
    return Response({
        'symbol': symbol,
        'granularity': granularity,
        'price': str(latest.close),
        'high': str(latest.high),
        'low': str(latest.low),
        'open': str(latest.open),
        'timestamp': latest.timestamp.isoformat(),
        'source': source,
    })


# ---------------------------------------------------------------------------
# Trade Execution
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@two_factor_required_api
def execute_trade_view(request):
    return _execution_disabled_response()


def _execute_trade_impl(request):
    data = request.data.copy()

    try:
        if not data.get('volume') or float(data.get('volume', 0)) <= 0:
            data['volume'] = float(request.user.stake_amount)
    except (ValueError, TypeError):
        return Response({'error': 'Invalid volume value'}, status=status.HTTP_400_BAD_REQUEST)

    # Serialise concurrent trade requests per user with a row-level lock
    user = request.user.__class__.objects.select_for_update().get(pk=request.user.pk)
    risk = RiskConfig.get_for_user(user)

    if not data.get('stop_loss') or not data.get('take_profit'):
        signal_type = data.get('action', 'BUY').upper()
        try:
            confidence = float(data.get('confidence', 0.65))
            current_price = float(data.get('entry_price', 0)) or float(data.get('current_price', 0))
        except (ValueError, TypeError):
            return Response({'error': 'Invalid price or confidence value'}, status=status.HTTP_400_BAD_REQUEST)

        candles = []
        try:
            if not user.paper_mode:
                adapter = _resolve_adapter(user)
                creds = build_credentials(user)
                adapter.connect(creds)
                candles = adapter.get_candles(data.get('symbol', 'EUR/USD'), 3600, 50)
                adapter.disconnect()
        except Exception:
            pass

        if not candles and current_price > 0:
            candles = []

        from trading_app.trading_bot.sl_tp_calculator import compute_sl_tp
        proposal = compute_sl_tp(
            candles=candles,
            signal_type=signal_type,
            confidence=confidence,
            current_price=current_price or (float(candles[-1]['close']) if candles else None),
            risk_per_trade_pct=float(risk.risk_per_trade),
            account_balance=float(user.balance),
        )
        if not data.get('stop_loss'):
            data['stop_loss'] = str(proposal.stop_loss)
        if not data.get('take_profit'):
            data['take_profit'] = str(proposal.take_profit)

    serializer = TradeCreateSerializer(data=data)
    serializer.is_valid(raise_exception=True)

    if user.last_trade_date != timezone.now().date():
        user.daily_trades_count = 0
        user.last_trade_date = timezone.now().date()

    if user.daily_trades_count >= risk.max_daily_trades:
        return Response(
            {'error': f'Daily trade limit reached ({risk.max_daily_trades} trades/day)'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    open_count = Trade.objects.filter(user=user, status='OPEN').count()
    if open_count >= risk.max_open_positions:
        return Response(
            {'error': f'Max open positions reached ({risk.max_open_positions})'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    trade = serializer.save(user=user, status='PENDING', is_live=not user.paper_mode)
    trade = _execute_via_adapter(user, trade, risk)

    if trade.status == 'OPEN':
        user.daily_trades_count += 1
        user.save(update_fields=['daily_trades_count', 'last_trade_date'])

    return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)


def _execute_via_adapter(user, trade, risk=None):
    return _execution_disabled_response()


# ---------------------------------------------------------------------------
# Trade Modification (SL/TP)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def modify_trade_view(request, trade_id):
    return _execution_disabled_response()


# ---------------------------------------------------------------------------
# Broker Configuration & Health Check
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@two_factor_required_api
def configure_broker_view(request):
    return _execution_disabled_response()


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
@two_factor_required_api
def broker_status_view(request):
    return Response({
        'configured': False,
        'execution_enabled': False,
        'purpose': 'Market data and analysis only',
        'market_data_provider': 'Deriv WebSocket',
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@two_factor_required_api
def broker_health_check_view(request):
    return _execution_disabled_response()


# ---------------------------------------------------------------------------
# Risk Configuration
# ---------------------------------------------------------------------------

@api_view(['GET', 'PUT'])
@permission_classes([permissions.IsAuthenticated])
def risk_config_view(request):
    risk = RiskConfig.get_for_user(request.user)

    if request.method == 'GET':
        return Response(RiskConfigSerializer(risk).data)

    serializer = RiskConfigSerializer(risk, data=request.data, partial=True)
    serializer.is_valid(raise_exception=True)
    serializer.save()
    return Response(RiskConfigSerializer(risk).data)


# ---------------------------------------------------------------------------
# Market Analysis (technical analyzer + signal creation)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([UserRateThrottle])
def analyze_and_signal_view(request):
    return _measure_analysis_duration(_run_analysis_pipeline)(request, verification_mode=False)


@api_view(['POST'])
@permission_classes([permissions.IsAdminUser])
@throttle_classes([UserRateThrottle])
def analysis_verification_view(request):
    if not settings.TRADING_ANALYSIS_VERIFICATION_ENABLED:
        return Response({'error': 'Analysis verification is disabled.'}, status=status.HTTP_404_NOT_FOUND)
    return _measure_analysis_duration(_run_analysis_pipeline)(request, verification_mode=True)


def _run_analysis_pipeline(request, verification_mode: bool):
    access = _subscription_access_response(request.user)
    if access is not None:
        return access
    bot_status = CacheService.get(f'bot_status_{request.user.id}', {'status': 'idle'})
    if not verification_mode and bot_status.get('status') != 'running':
        return Response({'error': 'Start the bot before requesting an analysis.'}, status=status.HTTP_409_CONFLICT)

    symbol = request.data.get(
        'prompt', request.data.get(
            'symbol', request.query_params.get('symbol', request.user.selected_market or 'EUR/USD'),
        ),
    )
    if symbol != request.user.selected_market:
        return Response({'error': 'Analysis is limited to your selected market.'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        granularity = int(request.data.get('granularity', request.query_params.get('granularity', 300)))
    except (TypeError, ValueError):
        return Response({'error': 'granularity must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
    from trading_app.trading_bot.deriv_adapter import GRANULARITY_MAP
    if granularity not in GRANULARITY_MAP:
        return Response({'error': 'Unsupported granularity'}, status=status.HTTP_400_BAD_REQUEST)
    analysis_started = time.perf_counter()
    stage_timings: dict[str, float] = {}
    try:
        from trading_app.models import AnalysisWaitCycle
        from datetime import timedelta

        candle_started = time.perf_counter()
        raw_candles, mtf, live_error = _fetch_verification_candles(
            request.user, symbol, granularity,
        )
        if live_error:
            return _wait_response(
                request.user, symbol, granularity, live_error,
                verification_mode=verification_mode,
            )
        stage_timings['live_candle_retrieval_ms'] = round((time.perf_counter() - candle_started) * 1000, 3)
        if not raw_candles:
            return _wait_response(
                request.user, symbol, granularity, 'Live market data unavailable',
                verification_mode=verification_mode,
            )
        preprocess_started = time.perf_counter()
        candles = _candles_to_dicts(raw_candles)
        latest_candle_time = int(candles[-1].get('time', 0)) if candles else 0
        data_age_seconds = int(timezone.now().timestamp()) - latest_candle_time
        candle_fresh = 0 <= data_age_seconds <= max(granularity * 2, 120)
        from trading_app.strategies.base import prepare_context
        shared_context = prepare_context(candles)
        stage_timings['preprocessing_ms'] = round((time.perf_counter() - preprocess_started) * 1000, 3)

        recent_trades = list(Trade.objects.filter(
            user=request.user, symbol=symbol, status='CLOSED'
        ).values('pnl', 'action')[:10])

        from trading_app.trading_bot.technical_analyzer import analyze_technical
        from trading_app.strategies import choch, fibonacci, ict, momentum, support_resistance
        from trading_app.strategies.consensus import combine
        from trading_app.strategies.runner import run_parallel
        analysis_box = {}
        mtf = {
            label: _candles_to_dicts(mtf[label]) if mtf.get(label) and not isinstance(mtf[label][0], dict) else mtf.get(label, [])
            for label in ('H4', 'M15', 'M5', 'M1')
        }
        mtf_freshness = {
            label: _candles_are_fresh(mtf[label], {'H4': 14400, 'M15': 900, 'M5': 300, 'M1': 60}[label])
            for label in mtf
        }
        shared_context.multi_timeframe = mtf
        shared_context.news_status = choch.check_news_window(symbol, 'M1')

        def run_technical():
            started = time.perf_counter()
            analysis_box['analysis'] = analyze_technical(symbol, candles, trade_history=recent_trades)
            return round((time.perf_counter() - started) * 1000, 3)

        core_futures = {}
        executor = ThreadPoolExecutor(max_workers=4)
        try:
            core_futures[executor.submit(run_technical)] = 'technical_smc'
            strategy_future = executor.submit(run_parallel, shared_context, {
                'fibonacci': fibonacci.evaluate,
                'ict': ict.evaluate,
                'momentum': momentum.evaluate,
                'support_resistance': support_resistance.evaluate,
                'choch': choch.evaluate,
            })
            core_futures[strategy_future] = 'strategy_evaluators'

            strategy_results = []
            for future in as_completed(core_futures):
                stage = core_futures[future]
                started = time.perf_counter()
                if stage == 'technical_smc':
                    stage_timings[stage + '_ms'] = future.result()
                elif stage == 'strategy_evaluators':
                    strategy_results, strategy_timings = future.result()
                    stage_timings.update({f'{name}_ms': value for name, value in strategy_timings.items()})
        except Exception:
            executor.shutdown(wait=True, cancel_futures=True)
            raise
        analysis = analysis_box['analysis']
        strategy_results.append(_technical_strategy_result(analysis, stage_timings.get('technical_smc_ms', 0)))
        consensus_started = time.perf_counter()
        consensus = combine(
            strategy_results,
            threshold=settings.STRATEGY_CONSENSUS_THRESHOLD,
            minimum_votes=settings.MIN_STRATEGY_VOTES,
        )
        stage_timings['consensus_ms'] = round((time.perf_counter() - consensus_started) * 1000, 3)

        actionable_levels_ready = bool(
            analysis.stop_loss is not None
            and analysis.take_profit is not None
            and candles[-1].get('close') is not None
            and float(candles[-1]['close']) > 0
            and (
                (consensus.bias == 'bullish' and float(analysis.stop_loss) < float(candles[-1]['close']) and float(analysis.take_profit) > float(candles[-1]['close']))
                or (consensus.bias == 'bearish' and float(analysis.stop_loss) > float(candles[-1]['close']) and float(analysis.take_profit) < float(candles[-1]['close']))
            )
        )
        strategy_confluence_ready = (
            consensus.bias != 'neutral'
            and all(result.bias == consensus.bias for result in strategy_results)
            and consensus.total_directional_votes >= settings.MIN_STRATEGY_VOTES
            and consensus.agreement >= settings.STRATEGY_CONSENSUS_THRESHOLD
            and actionable_levels_ready
            and candle_fresh
            and all(mtf_freshness.values())
        )
        ml_started = time.perf_counter()
        ml_signal = _predict_from_existing_candles(
            candles, shared_context.featured, symbol, granularity,
        )
        stage_timings['ml_prediction_ms'] = round((time.perf_counter() - ml_started) * 1000, 3)
        executor.shutdown(wait=True)

        ml_compatible = bool(
            ml_signal is None
            or ml_signal.bias not in ('bullish', 'bearish')
            or ml_signal.bias == consensus.bias
        )
        bias = consensus.bias if strategy_confluence_ready and ml_compatible else 'neutral'
        confidence = int(consensus.confidence * 100)
        total_analysis_ms = round((time.perf_counter() - analysis_started) * 1000, 3)
        stage_timings['total_analysis_ms'] = total_analysis_ms
        analysis_metrics = {
            'bias': bias,
            'confidence': confidence,
            'rationale': analysis.rationale if bias != 'neutral' else 'No consensus',
            'rsi': analysis.rsi,
            'sma_short': analysis.sma_short,
            'sma_long': analysis.sma_long,
            'atr': analysis.atr,
            'granularity': granularity,
            'consensus': {
                'buy_votes': consensus.buy_votes,
                'sell_votes': consensus.sell_votes,
                'directional_votes': consensus.total_directional_votes,
                'agreement': consensus.agreement,
                'threshold': settings.STRATEGY_CONSENSUS_THRESHOLD,
                'minimum_votes': settings.MIN_STRATEGY_VOTES,
                'reason': consensus.reason,
                'contributors': list(consensus.contributing_strategies),
            },
            'strategies': [result.as_dict() for result in strategy_results],
            'timings': stage_timings,
            'data_fresh': candle_fresh,
            'data_age_seconds': data_age_seconds,
            'multi_timeframe_available': {label: bool(mtf.get(label)) for label in ('H4', 'M15', 'M5', 'M1')},
            'multi_timeframe_fresh': mtf_freshness,
            'ml_compatible': ml_compatible,
            'fast_path': False,
        }
        if verification_mode:
            analysis_metrics['data_source'] = 'live_deriv'
            analysis_metrics['verification_mode'] = 'read_only'

        # Record the ML prediction so the Analytics/History panels and the
        # feedback loop capture every manual scan. The prediction is held
        # until it resolves as correct/missed — a new one is only recorded
        # once the current prediction for this symbol has been decided.
        if not verification_mode:
            try:
                from trading_app.tasks import _record_prediction, CANDLE_GRANULARITY
                if ml_signal is not None and granularity == CANDLE_GRANULARITY and ml_signal.bias in ('bullish', 'bearish'):
                    _record_prediction(
                        request.user, symbol, CANDLE_GRANULARITY,
                        int(candles[-1].get('time', 0)), ml_signal,
                        entry_price=candles[-1].get('close'),
                    )
            except Exception as e:
                logger.warning(f'ML prediction skipped for manual scan {symbol}: {e}')

        if not verification_mode and bias != 'neutral':
            existing = TradingSignal.objects.filter(
                user=request.user, symbol=symbol,
                signal_type='BUY' if bias == 'bullish' else 'SELL',
                outcome='PENDING',
                granularity=granularity,
                entry_price__isnull=False,
                stop_loss__isnull=False,
                take_profit__isnull=False,
                evidence_at__gte=timezone.now() - timedelta(seconds=max(granularity * 2, 120)),
            ).order_by('-evidence_at').first()
            if existing:
                return Response({
                    'decision': existing.signal_type,
                    'signal': TradingSignalSerializer(existing).data,
                    'analysis': analysis_metrics,
                    'pending': True,
                })

        if bias == 'neutral':
            reasons = []
            if not candle_fresh:
                reasons.append('Market data is stale')
            if not all(mtf_freshness.values()):
                reasons.append('Required timeframes are missing or stale')
            if consensus.bias == 'neutral':
                reasons.append(consensus.reason)
            if not ml_compatible:
                reasons.append('ML prediction is unavailable or contradicts strategy confluence')
            return _wait_response(
                request.user, symbol, granularity, '; '.join(reasons) or 'Confluence is incomplete',
                analysis=analysis_metrics, verification_mode=verification_mode,
            )

        if not analysis.stop_loss or not analysis.take_profit or not candles[-1].get('close'):
            analysis_metrics['bias'] = 'neutral'
            analysis_metrics['confidence'] = 0
            analysis_metrics['rationale'] = 'No consensus: required Entry, Stop Loss, or Take Profit is unavailable'
            return _wait_response(
                request.user, symbol, granularity,
                'Required entry, stop-loss, or take-profit evidence is unavailable',
                analysis=analysis_metrics, verification_mode=verification_mode,
            )

        signal_values = {
            'user': request.user,
            'symbol': symbol,
            'signal_type': 'BUY' if bias == 'bullish' else 'SELL',
            'confidence': confidence,
            'entry_price': Decimal(str(candles[-1]['close'])).quantize(Decimal('0.00001')),
            'stop_loss': Decimal(str(analysis.stop_loss)).quantize(Decimal('0.00001')),
            'take_profit': Decimal(str(analysis.take_profit)).quantize(Decimal('0.00001')),
            'reasoning': analysis.rationale,
            'granularity': granularity,
            'evidence_at': timezone.now(),
            'strategy_evidence': [result.as_dict() for result in strategy_results],
            'source': 'SYSTEM',
            'risk_level': 'LOW' if confidence < 40 else 'MEDIUM' if confidence < 70 else 'HIGH',
        }
        persist_signal = not verification_mode or request.data.get('persist') is True
        if not persist_signal:
            signal = TradingSignal(**signal_values)
            return Response({
                'decision': signal.signal_type,
                'signal': TradingSignalSerializer(signal).data,
                'analysis': analysis_metrics,
                'persisted': False,
            })

        signal = TradingSignal.objects.create(**signal_values)
        AnalysisWaitCycle.objects.filter(
            user=request.user, symbol=symbol, granularity=granularity,
        ).delete()
        audit.log_signal(symbol, signal.signal_type, confidence, {
            'signal_id': signal.id, 'rationale': signal.reasoning,
        })

        if verification_mode:
            return Response({
                'decision': signal.signal_type,
                'signal': TradingSignalSerializer(signal).data,
                'analysis': analysis_metrics,
                'persisted': True,
            })

        total_analysis_ms = round((time.perf_counter() - analysis_started) * 1000, 3)
        stage_timings['total_analysis_ms'] = total_analysis_ms
        analysis_metrics['timings'] = stage_timings
        return Response({
            'decision': signal.signal_type,
            'signal': TradingSignalSerializer(signal).data,
            'analysis': analysis_metrics,
            'analysis_duration_ms': total_analysis_ms,
        })

    except Exception as e:
        logger.error('Analysis failed: %s', e, exc_info=True)
        from trading_app.services.system_events import record_system_event
        record_system_event('ANALYSIS_FAILED', request.data.get('symbol', ''))
        return Response({'error': 'Analysis failed. Please try again later.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _wait_response(user, symbol, granularity, reason, *, analysis=None, verification_mode=False):
    from datetime import timedelta
    from trading_app.models import AnalysisWaitCycle

    from django.db import IntegrityError, transaction

    now = timezone.now()
    if verification_mode:
        wait_started_at = now
        wait_expires_at = now + timedelta(seconds=180)
    else:
        with transaction.atomic():
            cycle = AnalysisWaitCycle.objects.select_for_update().filter(
                user=user, symbol=symbol, granularity=granularity,
            ).first()
            if cycle is None:
                try:
                    cycle = AnalysisWaitCycle.objects.create(
                        user=user, symbol=symbol, granularity=granularity,
                        wait_started_at=now, wait_expires_at=now + timedelta(seconds=180),
                    )
                except IntegrityError:
                    cycle = AnalysisWaitCycle.objects.select_for_update().get(
                        user=user, symbol=symbol, granularity=granularity,
                    )
            elif cycle.wait_expires_at <= now:
                cycle.wait_started_at = now
                cycle.wait_expires_at = now + timedelta(seconds=180)
                cycle.save(update_fields=['wait_started_at', 'wait_expires_at'])
        wait_started_at = cycle.wait_started_at
        wait_expires_at = cycle.wait_expires_at
    remaining = max(0, int((wait_expires_at - now).total_seconds()))
    payload = {
        'decision': 'WAIT',
        'signal': None,
        'reason': reason,
        'message': reason,
        'wait_started_at': wait_started_at.isoformat(),
        'wait_expires_at': wait_expires_at.isoformat(),
        'wait_seconds_remaining': remaining,
        'reanalysis_due': remaining == 0,
    }
    if analysis is not None:
        payload['analysis'] = analysis
    if verification_mode:
        payload['data_source'] = 'live_deriv'
        payload['verification_mode'] = 'read_only'
    return Response(payload)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def analysis_wait_status_view(request):
    from trading_app.models import AnalysisWaitCycle

    symbol = request.query_params.get('symbol', request.user.selected_market or 'EUR/USD')
    try:
        granularity = int(request.query_params.get('granularity', 300))
    except (TypeError, ValueError):
        return Response({'error': 'granularity must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
    cycle = AnalysisWaitCycle.objects.filter(
        user=request.user, symbol=symbol, granularity=granularity,
    ).first()
    if cycle is None:
        return Response({'decision': 'WAIT', 'wait_started_at': None, 'wait_expires_at': None, 'wait_seconds_remaining': 0})
    remaining = max(0, int((cycle.wait_expires_at - timezone.now()).total_seconds()))
    if remaining == 0:
        bot_status = CacheService.get(f'bot_status_{request.user.id}', {'status': 'idle'})
        from trading_app.services.subscription_access import bot_access_status
        if bot_status.get('status') == 'running' and bot_access_status(request.user)['has_access']:
            return _measure_analysis_duration(_run_analysis_pipeline)(request, verification_mode=False)
        cycle.delete()
        return Response({
            'decision': 'WAIT', 'wait_started_at': None, 'wait_expires_at': None,
            'wait_seconds_remaining': 0, 'reanalysis_due': True,
            'reason': 'WAIT expired; start the bot to run a fresh analysis.',
        })
    return Response({
        'decision': 'WAIT',
        'wait_started_at': cycle.wait_started_at.isoformat(),
        'wait_expires_at': cycle.wait_expires_at.isoformat(),
        'wait_seconds_remaining': remaining,
        'reanalysis_due': remaining == 0,
    })


def _predict_from_existing_candles(candles, featured_candles, symbol: str, granularity: int):
    from trading_app.ml.inference import predict_signal, load_latest_model

    model_data = load_latest_model(symbol=symbol, granularity=granularity)
    if not model_data:
        return None
    return predict_signal(
        candles, symbol=symbol, granularity=granularity,
        featured_candles=featured_candles,
        model_data=model_data,
    )


def _candles_are_fresh(candles, granularity: int) -> bool:
    if not candles:
        return False
    raw_time = candles[-1].get('time')
    if raw_time is None:
        return False
    age_seconds = timezone.now().timestamp() - float(raw_time)
    return -5 <= age_seconds <= max(granularity * 2, 120)


def _technical_strategy_result(analysis, processing_time_ms: float):
    from trading_app.strategies.base import StrategyResult

    bias = analysis.bias if analysis.bias in ('bullish', 'bearish') else 'neutral'
    levels = {}
    if analysis.stop_loss is not None:
        levels['stop_loss'] = float(analysis.stop_loss)
    if analysis.take_profit is not None:
        levels['take_profit'] = float(analysis.take_profit)
    return StrategyResult(
        'Technical/SMC', bias, float(analysis.confidence),
        (str(analysis.rationale),) if bias != 'neutral' else (),
        ('existing_candlestick_smc_rules',), levels, processing_time_ms,
    )


def _ml_strategy_result(ml_signal, processing_time_ms: float):
    from trading_app.strategies.base import StrategyResult

    bias = {
        'bullish': 'bullish',
        'bearish': 'bearish',
    }.get(ml_signal.bias, 'neutral')
    reasons = (f'Local ML {ml_signal.model_info}',) if bias != 'neutral' else ()
    return StrategyResult(
        'Local ML', bias, float(ml_signal.confidence), reasons,
        ('trained_model_prediction',), {}, processing_time_ms,
    )


# ---------------------------------------------------------------------------
# Stake Configuration
# ---------------------------------------------------------------------------

@api_view(['GET', 'PUT'])
@permission_classes([permissions.IsAuthenticated])
@two_factor_required_api
def stake_config_view(request):
    user = request.user

    if request.method == 'GET':
        return Response({
            'stake_amount': str(user.stake_amount),
            'balance': str(user.balance),
            'max_position_size': str(RiskConfig.get_for_user(user).max_position_size),
        })

    raw = request.data.get('stake_amount')
    if raw is None:
        return Response({'error': 'stake_amount is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        amount = Decimal(str(raw))
    except Exception:
        return Response({'error': 'Invalid stake_amount'}, status=status.HTTP_400_BAD_REQUEST)

    if amount <= 0:
        return Response({'error': 'Stake must be positive'}, status=status.HTTP_400_BAD_REQUEST)

    risk = RiskConfig.get_for_user(user)
    if amount > risk.max_position_size:
        return Response(
            {'error': f'Stake exceeds max position size ({risk.max_position_size})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    max_risk_amount = user.balance * (risk.risk_per_trade / Decimal('100'))
    if amount > max_risk_amount * 10:
        return Response(
            {'error': f'Stake seems too large for your risk settings (max risk per trade: {max_risk_amount})'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user.stake_amount = amount
    user.save(update_fields=['stake_amount'])

    return Response({
        'stake_amount': str(user.stake_amount),
        'balance': str(user.balance),
        'max_position_size': str(risk.max_position_size),
    })


# ---------------------------------------------------------------------------
# Signal Propose — bot-calculated SL/TP before execution
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def signal_propose_view(request):
    return Response({
        'error': 'Directional proposals are available only from approved live confluence analysis.',
    }, status=status.HTTP_405_METHOD_NOT_ALLOWED)


# ---------------------------------------------------------------------------
# OTP, Password Reset, Dashboard, Notifications
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def request_otp_view(request):
    email = request.data.get('email', '')
    purpose = request.data.get('purpose', 'password_reset')
    if purpose != 'password_reset':
        return Response({'error': 'Only password recovery codes may be requested through this endpoint.'}, status=status.HTTP_400_BAD_REQUEST)

    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({'status': 'If an account with that email exists, an OTP has been sent.'})

    from trading_app.services.otp_service import can_request_otp, invalidate_otp, issue_otp
    if not can_request_otp(user.id, purpose, scope='issue'):
        return Response(
            {'error': 'Too many OTP requests. Please wait 60 seconds.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    _otp, otp_code = issue_otp(user, purpose)
    sent = send_otp_email(user, otp_code, purpose=purpose)
    if not sent:
        invalidate_otp(_otp)
        return Response({'status': 'email_delivery_failed'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    return Response({'status': 'otp_sent'})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def verify_otp_view(request):
    serializer = OTPVerifySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = request.data.get('email', '')
    code = serializer.validated_data['code']
    purpose = serializer.validated_data['purpose']
    if purpose != 'password_reset':
        return Response({'error': 'Use the authenticated web login flow for 2FA.'}, status=status.HTTP_400_BAD_REQUEST)

    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({'error': 'Invalid or expired code'}, status=status.HTTP_400_BAD_REQUEST)

    from trading_app.services.otp_service import OTPResult, consume_otp
    result = consume_otp(user, purpose, code)
    if result == OTPResult.VERIFIED:
        return Response({'status': 'verified'})
    if result == OTPResult.RATE_LIMITED:
        return Response({'status': result.value, 'error': 'Too many attempts. Request a new code later.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    return Response({'status': result.value, 'error': {
        OTPResult.EXPIRED: 'Verification code expired.',
        OTPResult.USED: 'Verification code has already been used.',
        OTPResult.INVALID: 'Incorrect or expired verification code.',
    }[result]}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def subscription_view(request):
    sub, _ = Subscription.objects.get_or_create(
        user=request.user,
        defaults={'tier': 'FREE', 'is_active': True},
    )
    from trading_app.services.subscription_access import subscription_status

    access = subscription_status(request.user)
    payload = SubscriptionSerializer(sub).data
    payload['access'] = _serialize_subscription_status(access)
    return Response(payload)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def subscription_checkout_view(request):
    tier = str(request.data.get('tier', '')).upper()
    if tier != 'BASIC':
        return Response({'error': 'Unsupported subscription product.'}, status=status.HTTP_400_BAD_REQUEST)
    phone_value = request.data.get('phone_number')
    if not phone_value:
        return Response({'error': 'phone_number is required.'}, status=status.HTTP_400_BAD_REQUEST)

    from trading_app.models import PaymentTransaction
    from trading_app.payments import daraja

    try:
        config = daraja.DarajaConfig.from_settings()
        config.validate(require_product_price=True)
    except daraja.DarajaError as exc:
        return Response({
            'status': 'payment_unavailable',
            'access_granted': False,
            'message': str(exc),
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)
    try:
        phone_number = daraja.normalize_phone_number(phone_value)
    except daraja.DarajaError as exc:
        return Response({'error': str(exc)}, status=status.HTTP_400_BAD_REQUEST)

    subscription, _ = Subscription.objects.get_or_create(
        user=request.user,
        defaults={'tier': 'FREE', 'is_active': True},
    )
    payment = PaymentTransaction.objects.create(
        user=request.user,
        subscription=subscription,
        tier='BASIC',
        amount_kes=config.basic_amount_kes,
        phone_number=phone_number,
        status=PaymentTransaction.STATUS_INITIATED,
    )
    try:
        stk_response = daraja.initiate_stk_push(
            phone_number=phone_number,
            account_reference=f'ATS{payment.pk}',
            transaction_desc='BASIC subscription',
        )
    except daraja.DarajaTimeoutError:
        payment.status = PaymentTransaction.STATUS_PENDING
        payment.result_code = 'REQUEST_OUTCOME_UNKNOWN'
        payment.result_description = 'The request timed out; provider acceptance is unknown. Contact support before retrying.'
        payment.save(update_fields=['status', 'result_code', 'result_description'])
        return Response({
            'status': 'payment_request_unconfirmed',
            'access_granted': False,
            'payment_id': payment.pk,
            'message': 'Payment request status is unknown. Do not retry until it has been checked.',
        }, status=status.HTTP_202_ACCEPTED)
    except daraja.DarajaError as exc:
        payment.status = PaymentTransaction.STATUS_FAILED
        payment.result_description = str(exc)[:255]
        payment.completed_at = timezone.now()
        payment.save(update_fields=['status', 'result_description', 'completed_at'])
        return Response({
            'status': 'payment_request_failed',
            'access_granted': False,
            'message': str(exc),
        }, status=status.HTTP_502_BAD_GATEWAY)

    payment.merchant_request_id = stk_response['merchant_request_id']
    payment.checkout_request_id = stk_response['checkout_request_id']
    payment.status = PaymentTransaction.STATUS_PENDING
    payment.save(update_fields=['merchant_request_id', 'checkout_request_id', 'status'])

    return Response({
        'status': 'payment_pending',
        'tier': 'BASIC',
        'amount_kes': payment.amount_kes,
        'duration_days': 30,
        'access_granted': False,
        'payment_id': payment.pk,
        'message': 'M-Pesa payment prompt sent. Subscription access is granted only after payment verification.',
    }, status=status.HTTP_202_ACCEPTED)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def daraja_payment_status_view(request, payment_id):
    from trading_app.models import PaymentTransaction
    from trading_app.services.subscription_access import subscription_status

    payment = PaymentTransaction.objects.filter(pk=payment_id, user=request.user).select_related('subscription').first()
    if payment is None:
        return Response({'error': 'Payment not found.'}, status=status.HTTP_404_NOT_FOUND)
    return Response({
        'payment_id': payment.pk,
        'status': payment.status,
        'tier': payment.tier,
        'amount_kes': payment.amount_kes,
        'receipt_number': payment.mpesa_receipt_number or None,
        'access_granted': subscription_status(request.user)['has_access'],
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def daraja_stk_callback_view(request):
    from trading_app.payments import daraja
    from trading_app.payments.callback import CallbackError, process_stk_callback

    try:
        status_value = process_stk_callback(request.data, status_query=daraja.query_stk_status)
    except CallbackError as exc:
        logger.warning('Rejected Daraja callback (%s).', type(exc).__name__)
        return Response({'ResultCode': 1, 'ResultDesc': 'Callback could not be processed.'})
    except Exception:
        logger.exception('Daraja callback processing failed.')
        return Response({'ResultCode': 1, 'ResultDesc': 'Callback could not be processed.'})
    logger.info('Daraja callback processed with payment state %s.', status_value)
    return Response({'ResultCode': 0, 'ResultDesc': 'Accepted'})


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def dashboard_stats_view(request):
    from django.db.models import Sum, Q
    from django.db.models.functions import TruncDate
    from datetime import timedelta

    user = request.user
    from trading_app.models import PredictionRecord
    from trading_app.services.subscription_access import subscription_status
    from trading_app.services.subscription_access import bot_access_status

    open_trades = Trade.objects.filter(user=user, status='OPEN').count()
    total_trades = Trade.objects.filter(user=user).count()
    closed_trades = Trade.objects.filter(user=user, status='CLOSED')
    resolved_signals = TradingSignal.objects.filter(
        user=user, outcome__in=('WIN', 'LOSS'),
    )
    win_trades = resolved_signals.filter(outcome='WIN').count()
    loss_trades = resolved_signals.filter(outcome='LOSS').count()
    resolved_count = win_trades + loss_trades
    win_rate = (win_trades / resolved_count * 100) if resolved_count else 0
    week_start = timezone.now() - timedelta(days=timezone.now().weekday())
    weekly_resolved = resolved_signals.filter(outcome_resolved_at__gte=week_start)
    weekly_total = weekly_resolved.count()
    weekly_wins = weekly_resolved.filter(outcome='WIN').count()
    weekly_win_rate = round(weekly_wins / weekly_total * 100, 1) if weekly_total else 0

    closed_pnl = closed_trades.aggregate(total_pnl=Sum('pnl'))
    total_pnl = float(closed_pnl['total_pnl'] or 0)
    gross_profit = float(closed_trades.filter(pnl__gt=0).aggregate(s=Sum('pnl'))['s'] or 0)
    gross_loss = abs(float(closed_trades.filter(pnl__lt=0).aggregate(s=Sum('pnl'))['s'] or 0))
    profit_factor = round(gross_profit / gross_loss, 2) if gross_loss > 0 else round(gross_profit, 2) if gross_profit > 0 else 0

    avg_trade = round(total_pnl / closed_trades.count(), 2) if closed_trades.count() > 0 else 0

    now = timezone.now()
    daily_pnl = float(closed_trades.filter(closed_at__date=now.date()).aggregate(s=Sum('pnl'))['s'] or 0)
    week_start = now - timedelta(days=now.weekday())
    weekly_pnl = float(closed_trades.filter(closed_at__gte=week_start).aggregate(s=Sum('pnl'))['s'] or 0)
    month_start = now.replace(day=1)
    monthly_pnl = float(closed_trades.filter(closed_at__gte=month_start).aggregate(s=Sum('pnl'))['s'] or 0)

    last_30 = now - timedelta(days=30)
    daily_pnl_series = (
        closed_trades.filter(closed_at__gte=last_30)
        .annotate(date=TruncDate('closed_at'))
        .values('date')
        .annotate(pnl=Sum('pnl'))
        .order_by('date')
    )
    pnl_history = []
    running = 0
    for entry in daily_pnl_series:
        running += float(entry['pnl'] or 0)
        pnl_history.append({
            'date': entry['date'].isoformat(),
            'pnl': round(running, 2),
        })

    loss_rate = round(loss_trades / resolved_count * 100, 1) if resolved_count else 0

    risk = RiskConfig.get_for_user(user)
    recent_signals = TradingSignal.objects.filter(
        user=user, signal_type__in=('BUY', 'SELL'),
    ).order_by('-created_at')[:5]
    signal_data = TradingSignalSerializer(recent_signals, many=True).data
    total_signals = TradingSignal.objects.filter(
        user=user, signal_type__in=('BUY', 'SELL'),
    ).count()

    return Response({
        'balance': float(user.balance),
        'open_trades': open_trades,
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
        'weekly_win_rate': weekly_win_rate,
        'weekly_resolved_signals': weekly_total,
        'loss_rate': loss_rate,
        'profit_factor': profit_factor,
        'avg_trade': avg_trade,
        'daily_pnl': round(daily_pnl, 2),
        'weekly_pnl': round(weekly_pnl, 2),
        'monthly_pnl': round(monthly_pnl, 2),
        'total_pnl': round(total_pnl, 2),
        'win_trades': win_trades,
        'loss_trades': loss_trades,
        'pnl_history': pnl_history,
        'daily_trades_count': user.daily_trades_count,
        'paper_mode': user.paper_mode,
        'trading_enabled': risk.trading_enabled,
        'recent_signals': signal_data,
        'total_signals': total_signals,
        'resolved_signals': resolved_count,
        'subscription': _serialize_subscription_status(subscription_status(user)),
        'bot_access': bot_access_status(user),
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def predictions_view(request):
    """Prediction history + outcome stats for the Signal Analytics / History
    panels. Stats are computed from resolved predictions only, so the panels
    update as predictions come true or miss."""
    from trading_app.models import PredictionRecord

    qs = PredictionRecord.objects.filter(user=request.user)
    ml_resolved = qs.filter(resolved=True, prediction_correct__isnull=False)
    ml_correct = ml_resolved.filter(prediction_correct=True).count()
    ml_missed = ml_resolved.filter(prediction_correct=False).count()
    ml_decided = ml_correct + ml_missed
    ml_win_rate = round(ml_correct / ml_decided * 100, 1) if ml_decided else 0
    ml_loss_rate = round(ml_missed / ml_decided * 100, 1) if ml_decided else 0
    signal_qs = TradingSignal.objects.filter(
        user=request.user, signal_type__in=('BUY', 'SELL'),
    )
    resolved_signals = signal_qs.filter(outcome__in=('WIN', 'LOSS'))
    correct = resolved_signals.filter(outcome='WIN').count()
    missed = resolved_signals.filter(outcome='LOSS').count()
    decided = correct + missed

    win_rate = round(correct / decided * 100, 1) if decided else 0
    loss_rate = round(missed / decided * 100, 1) if decided else 0
    profit_factor = round(correct / missed, 2) if missed else (correct if correct else 0)

    history = []
    for signal in signal_qs.order_by('-created_at')[:100]:
        status = {'WIN': 'Correct', 'LOSS': 'Missed'}.get(signal.outcome, 'Pending')
        history.append({
            'id': signal.id,
            'date': (signal.outcome_resolved_at or signal.created_at).isoformat(),
            'symbol': signal.symbol,
            'signal': signal.signal_type,
            'confidence': signal.confidence,
            'entry_price': float(signal.entry_price) if signal.entry_price is not None else None,
            'stop_loss': float(signal.stop_loss) if signal.stop_loss is not None else None,
            'take_profit': float(signal.take_profit) if signal.take_profit is not None else None,
            'exit_price': float(signal.outcome_price) if signal.outcome_price is not None else None,
            'result': status,
            'status': status,
        })

    if not history:
        for prediction in qs.order_by('-predicted_at')[:100]:
            result = (
                'Correct' if prediction.prediction_correct is True else
                'Missed' if prediction.prediction_correct is False else 'Pending'
            )
            history.append({
                'id': prediction.id,
                'date': (prediction.resolved_at or prediction.predicted_at).isoformat(),
                'symbol': prediction.symbol,
                'signal': 'BUY' if prediction.bias == 'bullish' else 'SELL',
                'confidence': prediction.confidence,
                'entry_price': float(prediction.entry_price) if prediction.entry_price is not None else None,
                'exit_price': float(prediction.exit_price) if prediction.exit_price is not None else None,
                'result': result,
                'status': result,
            })

    return Response({
        'stats': {
            'total': signal_qs.count(),
            'resolved': ml_decided,
            'pending': qs.filter(resolved=False).count(),
            'correct_signals': ml_correct,
            'missed_signals': ml_missed,
            'win_rate': ml_win_rate,
            'loss_rate': ml_loss_rate,
            'profit_factor': round(ml_correct / ml_missed, 2) if ml_missed else (ml_correct if ml_correct else 0),
            'strategy_resolved': decided,
            'strategy_pending': signal_qs.filter(outcome='PENDING').count(),
            'strategy_wins': correct,
            'strategy_losses': missed,
            'strategy_win_rate': win_rate,
            'strategy_loss_rate': loss_rate,
            'strategy_profit_factor': profit_factor,
        },
        'history': history,
        'ml_predictions': list(qs.order_by('-predicted_at').values(
            'id', 'symbol', 'bias', 'confidence', 'resolved', 'prediction_correct',
        )[:100]),
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_request(request):
    email = request.data.get('email', '')
    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    response_msg = {'status': 'If an account with that email exists, a reset code has been sent.'}

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response(response_msg)

    from trading_app.services.otp_service import can_request_otp, invalidate_otp, issue_otp
    if not can_request_otp(user.id, 'password_reset', scope='issue'):
        return Response(response_msg)

    _otp, otp_code = issue_otp(user, 'password_reset')
    if not send_otp_email(user, otp_code, purpose='password_reset'):
        invalidate_otp(_otp)
        return Response({'status': 'email_delivery_failed'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    reset_token = secrets.token_urlsafe(32)
    CacheService.set(f'pwd_reset_token_{reset_token}', user.id, timeout=600)

    request.session['password_reset_token'] = reset_token

    return Response({'status': 'If an account with that email exists, a reset code has been sent.'})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_verify(request):
    code = request.data.get('code', '')

    reset_token = request.session.get('password_reset_token', '')

    if not reset_token or not code:
        return Response({'error': 'code and reset_token are required'}, status=status.HTTP_400_BAD_REQUEST)

    from .services.cache_service import CacheService
    user_id = CacheService.get(f'pwd_reset_token_{reset_token}')
    if not user_id:
        return Response({'error': 'Invalid or expired reset session'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)

    from trading_app.services.otp_service import OTPResult, consume_otp
    result = consume_otp(user, 'password_reset', code)
    if result == OTPResult.VERIFIED:
        confirmed_token = secrets.token_urlsafe(32)
        CacheService.set(f'pwd_reset_verified_{confirmed_token}', user.id, timeout=300)
        return Response({'status': 'verified', 'verified_token': confirmed_token})
    if result == OTPResult.RATE_LIMITED:
        return Response({'status': result.value, 'error': 'Too many attempts. Request a new code later.'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    return Response({'status': result.value, 'error': 'Incorrect, expired, or already-used code.'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_confirm(request):
    verified_token = request.data.get('verified_token', '')
    new_password = request.data.get('new_password', '')

    user_id = CacheService.get(f'pwd_reset_verified_{verified_token}') if verified_token else None

    if not user_id or not new_password:
        return Response({'error': 'verified_token and new_password are required'}, status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 8:
        return Response({'error': 'Password must be at least 8 characters'}, status=status.HTTP_400_BAD_REQUEST)

    if new_password == new_password.lower():
        return Response({'error': 'Password must contain at least one uppercase letter'}, status=status.HTTP_400_BAD_REQUEST)

    if new_password == new_password.upper():
        return Response({'error': 'Password must contain at least one lowercase letter'}, status=status.HTTP_400_BAD_REQUEST)

    if not any(c.isdigit() for c in new_password):
        return Response({'error': 'Password must contain at least one digit'}, status=status.HTTP_400_BAD_REQUEST)

    if not any(c in '!@#$%^&*()_+-=[]{}|;:,.<>?' for c in new_password):
        return Response({'error': 'Password must contain at least one special character'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)

    user.set_password(new_password)
    user.save()

    from trading_app.models import RememberMeToken
    RememberMeToken.objects.filter(user=user).delete()

    CacheService.delete(f'pwd_reset_verified_{verified_token}')

    from django.contrib.sessions.models import Session
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in sessions:
        data = session.get_decoded()
        if str(user.id) == str(data.get('_auth_user_id')):
            session.delete()

    return Response({'status': 'Password reset successful'})


# ---------------------------------------------------------------------------
# Notifications
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def list_notifications(request):
    notifications = Notification.objects.filter(user=request.user)[:20]
    unread_count = Notification.objects.filter(user=request.user, is_read=False).count()
    data = [{
        'id': n.id,
        'title': n.title,
        'message': n.message,
        'type': n.notification_type,
        'is_read': n.is_read,
        'created_at': n.created_at.isoformat(),
    } for n in notifications]
    return Response({'notifications': data, 'unread_count': unread_count})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_read(request, notification_id):
    notification = get_object_or_404(Notification, id=notification_id, user=request.user)
    notification.is_read = True
    notification.save()
    return Response({'status': 'ok'})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_read(request):
    Notification.objects.filter(user=request.user, is_read=False).update(is_read=True)
    return Response({'status': 'ok'})


# ---------------------------------------------------------------------------
# Backtest
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([UserRateThrottle])
def run_backtest_view(request):
    return _execution_disabled_response()

    from .trading_bot.backtest import run_backtest, BacktestResult
    from .trading_bot.interface import Candle, OrderRequest

    symbol = request.data.get('symbol', 'EUR/USD')
    try:
        initial_balance = Decimal(str(request.data.get('initial_balance', 10000)))
        days = min(int(request.data.get('days', 30)), 90)
        stop_loss_pct = Decimal(str(request.data.get('stop_loss_pct', '2')))
        take_profit_pct = Decimal(str(request.data.get('take_profit_pct', '4')))
    except (ValueError, TypeError, decimal.InvalidOperation):
        return Response({'error': 'Invalid parameter value'}, status=status.HTTP_400_BAD_REQUEST)

    paper = PaperBrokerAdapter(initial_balance=initial_balance)
    paper.connect({})

    try:
        adapter = _resolve_adapter(request.user)
        creds = build_credentials(request.user)
        adapter.connect(creds)
        raw_candles = adapter.get_candles(symbol, 3600, days * 24)
        adapter.disconnect()
    except Exception:
        raw_candles = []

    if not raw_candles:
        return Response({'error': 'Live market data unavailable'}, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    paper.seed_candles(raw_candles)

    def sma_strategy(candle, positions, balance):
        if positions:
            return None
        candles_list = paper._candle_store.get(symbol, [])
        if len(candles_list) < 20:
            return None
        recent = candles_list[-20:]
        sma = sum(c.close for c in recent) / len(recent)
        if candle.close > sma * Decimal('1.002'):
            return OrderRequest(
                symbol=symbol, side='buy', order_type='market',
                volume=Decimal('0.1'),
                stop_loss=candle.close * (Decimal('1') - stop_loss_pct / Decimal('100')),
                take_profit=candle.close * (Decimal('1') + take_profit_pct / Decimal('100')),
            )
        elif candle.close < sma * Decimal('0.998'):
            return OrderRequest(
                symbol=symbol, side='sell', order_type='market',
                volume=Decimal('0.1'),
                stop_loss=candle.close * (Decimal('1') + stop_loss_pct / Decimal('100')),
                take_profit=candle.close * (Decimal('1') - take_profit_pct / Decimal('100')),
            )
        return None

    result = run_backtest(paper, raw_candles, sma_strategy, initial_balance=initial_balance)

    return Response({
        'symbol': symbol,
        'total_trades': result.total_trades,
        'winning_trades': result.winning_trades,
        'losing_trades': result.losing_trades,
        'total_pnl': str(result.total_pnl),
        'final_balance': str(result.final_balance),
        'max_drawdown': str(result.max_drawdown),
        'peak_equity': str(result.peak_equity),
        'win_rate': round(result.winning_trades / result.total_trades * 100, 1) if result.total_trades > 0 else 0,
        'equity_curve': [str(e) for e in result.equity_curve],
    })


# ---------------------------------------------------------------------------
# Ticker subscription (WebSocket bridge control)
# ---------------------------------------------------------------------------


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def ticker_subscribe_view(request):
    symbol = request.data.get('symbol', '').strip()
    if not symbol:
        return Response({'error': 'symbol required'}, status=status.HTTP_400_BAD_REQUEST)
    if not CacheService.check_rate_limit(f'sub_{request.user.id}', max_attempts=30, window=60):
        return Response({'error': 'Rate limit exceeded'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    TickerBridge.ensure_subscription(symbol, user=request.user)
    return Response({'status': 'ok', 'symbol': symbol})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def ticker_unsubscribe_view(request):
    symbol = request.data.get('symbol', '').strip()
    if not symbol:
        return Response({'error': 'symbol required'}, status=status.HTTP_400_BAD_REQUEST)
    if not CacheService.check_rate_limit(f'unsub_{request.user.id}', max_attempts=30, window=60):
        return Response({'error': 'Rate limit exceeded'}, status=status.HTTP_429_TOO_MANY_REQUESTS)
    TickerBridge.remove_subscription(symbol, str(request.user.id))
    return Response({'status': 'ok', 'symbol': symbol})


# ---------------------------------------------------------------------------
# ML Model Export — serves latest trained model for MQL5 EA
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, AuthorizedAdminPermission])
def model_export_view(request):
    """
    Return metadata + download URL for the latest trained model.
    The MQL5 EA calls this endpoint to fetch feature columns, thresholds,
    and model file path for on-device inference.
    """
    import json
    from pathlib import Path

    ml_output = Path(settings.BASE_DIR) / "ml_output"
    results_path = ml_output / "pipeline_results.json"

    if not results_path.exists():
        return Response(
            {'error': 'No trained model found. Run: python manage.py train_model'},
            status=status.HTTP_404_NOT_FOUND,
        )

    try:
        with open(results_path, "r") as f:
            results = json.load(f)
    except Exception as e:
        return Response({'error': f'Failed to load results: {e}'},
                        status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    # Find the latest model + metadata files
    model_files = sorted(ml_output.glob("*_model.pkl"), reverse=True)
    onnx_files = sorted(ml_output.glob("*_model.onnx"), reverse=True)
    meta_files = sorted(ml_output.glob("*_metadata.json"), reverse=True)

    model_info = {
        'symbol': results.get('symbol', 'EURUSD'),
        'granularity': results.get('granularity', 900),
        'metrics': results.get('metrics', {}),
        'has_onnx': len(onnx_files) > 0,
        'model_file': str(model_files[0].name) if model_files else None,
        'onnx_file': str(onnx_files[0].name) if onnx_files else None,
        'metadata_file': str(meta_files[0].name) if meta_files else None,
    }

    # Load metadata for feature columns
    if meta_files:
        try:
            with open(meta_files[0], "r") as f:
                meta = json.load(f)
            model_info['feature_columns'] = meta.get('feature_columns', [])
            model_info['model_type'] = meta.get('model_type', '')
            model_info['trained_at'] = meta.get('timestamp', '')
        except Exception:
            pass

    return Response(model_info)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, AuthorizedAdminPermission])
def model_download_view(request, filename):
    """
    Serve a trained model file for download.
    MQL5 EA downloads the .onnx or .pkl file from here.
    """
    import os
    from pathlib import Path
    from django.http import FileResponse, Http404

    ml_output = Path(settings.BASE_DIR) / "ml_output"
    file_path = (ml_output / filename).resolve()

    # Prevent path traversal — must run before file existence check
    try:
        file_path.relative_to(ml_output.resolve())
    except ValueError:
        raise Http404("Invalid path")

    if not file_path.exists() or not file_path.is_file():
        raise Http404("Model file not found")

    return FileResponse(open(file_path, 'rb'), as_attachment=True, name=filename)


# ---------------------------------------------------------------------------
# Health Check
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.AllowAny])
def health_check_view(request):
    from django.db import connection
    db_ok = True
    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
    except Exception:
        db_ok = False

    cache_ok = True
    try:
        CacheService.set('_health_check', 'ok', timeout=10)
        if CacheService.get('_health_check') != 'ok':
            cache_ok = False
    except Exception:
        cache_ok = False

    redis_status = 'unavailable'
    try:
        from django_redis import get_redis_connection
        conn = get_redis_connection("default")
        conn.ping()
        redis_status = 'connected'
    except Exception:
        redis_status = 'not_configured'

    return Response({
        'status': 'healthy' if db_ok else 'degraded',
        'database': 'ok' if db_ok else 'error',
        'cache': 'ok' if cache_ok else 'error',
        'redis': redis_status,
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, AuthorizedAdminPermission])
def admin_overview_view(request):
    from django.db import connection
    from datetime import timedelta
    from trading_app.models import PaymentTransaction
    from trading_app.services.subscription_access import subscription_status
    from trading_app.services.subscription_access import bot_access_status
    from trading_app.services.email_diagnostics import email_configuration_status

    users = User.objects.order_by('-date_joined')
    search = request.query_params.get('search', '').strip()
    if search:
        from django.db.models import Q
        users = users.filter(
            Q(email__icontains=search) | Q(first_name__icontains=search)
            | Q(last_name__icontains=search) | Q(phone__icontains=search)
        )
    try:
        page = max(1, int(request.query_params.get('page', 1)))
        page_size = min(100, max(1, int(request.query_params.get('page_size', 25))))
    except (TypeError, ValueError):
        return Response({'error': 'page and page_size must be integers.'}, status=status.HTTP_400_BAD_REQUEST)
    user_count = users.count()
    users = users[(page - 1) * page_size:page * page_size]
    user_rows = []
    for user in users:
        access = subscription_status(user)
        subscription = getattr(user, 'subscription', None)
        bot_status = CacheService.get(f'bot_status_{user.pk}', {'status': 'idle'})
        latest_signal = TradingSignal.objects.filter(user=user).order_by('-created_at').first()
        user_rows.append({
            'id': user.pk,
            'full_name': user.get_full_name(),
            'first_name': user.first_name,
            'last_name': user.last_name,
            'email': user.email,
            'phone': user.phone,
            'created_at': user.date_joined.isoformat(),
            'registered_at': user.date_joined.isoformat(),
            'last_login': user.last_login.isoformat() if user.last_login else None,
            'account_status': 'active' if user.is_active else 'inactive',
            'is_active': user.is_active,
            'trial_started_at': user.trial_started_at.isoformat() if user.trial_started_at else None,
            'trial_expires_at': (user.trial_started_at + timedelta(hours=24)).isoformat() if user.trial_started_at else None,
            'trial_status': access['status'] if access['status'] in ('trial', 'expired', 'trial_limit_reached') else 'not_in_trial',
            'subscription_status': access['status'],
            'subscription_tier': access['tier'],
            'subscription_started_at': subscription.started_at.isoformat() if subscription else None,
            'subscription_expires_at': access['expires_at'].isoformat() if access['expires_at'] else None,
            'subscription_bypass': user.subscription_bypass,
            'bot_bypass': user.bot_bypass,
            'bot_access': bot_access_status(user),
            'subscription_access_denied': user.subscription_access_denied,
            'selected_market': user.selected_market,
            'bot_status': bot_status.get('status', 'idle'),
            'bot_market': bot_status.get('market', user.selected_market),
            'latest_signal': _admin_signal_payload(latest_signal),
            'two_factor_enabled': user.two_factor_enabled,
            'email_verified': user.email_verified,
        })

    try:
        with connection.cursor() as cursor:
            cursor.execute('SELECT 1')
            database_status = 'ok'
    except Exception:
        database_status = 'error'
    try:
        CacheService.set('_admin_health_probe', 'ok', timeout=5)
        cache_status = 'ok' if CacheService.get('_admin_health_probe') == 'ok' else 'error'
    except Exception:
        cache_status = 'error'

    signals = TradingSignal.objects.select_related('user').order_by('-created_at')[:100]
    payments = PaymentTransaction.objects.select_related('user', 'subscription').order_by('-created_at')[:100]
    from trading_app.models import SystemEvent
    system_events = SystemEvent.objects.all()[:100]
    latest_feed_event = SystemEvent.objects.filter(
        event_type__in=(SystemEvent.EVENT_FEED_FAILED, SystemEvent.EVENT_FEED_SUCCEEDED),
    ).first()
    auth_activity = Notification.objects.filter(
        notification_type='account',
        title__in=('New Login', 'Account Created', 'Two-Factor Authentication'),
    ).select_related('user').order_by('-created_at')[:100]
    return Response({
        'users': user_rows,
        'user_pagination': {
            'page': page, 'page_size': page_size, 'total': user_count,
            'pages': (user_count + page_size - 1) // page_size,
        },
        'analysis_activity': [{
            'id': signal.pk,
            'user_id': signal.user_id,
            'user_email': signal.user.email if signal.user else None,
            'market': signal.symbol,
            'signal': signal.signal_type,
            'confidence': signal.confidence,
            'entry': str(signal.entry_price) if signal.entry_price is not None else None,
            'stop_loss': str(signal.stop_loss) if signal.stop_loss is not None else None,
            'take_profit': str(signal.take_profit) if signal.take_profit is not None else None,
            'outcome': signal.outcome,
            'strategy_source': signal.source,
            'reasoning': signal.reasoning,
            'created_at': signal.created_at.isoformat(),
        } for signal in signals],
        'payments': [{
            'id': payment.pk,
            'user_id': payment.user_id,
            'email': payment.user.email,
            'tier': payment.tier,
            'amount_kes': payment.amount_kes,
            'status': payment.status,
            'receipt_number': payment.mpesa_receipt_number or None,
            'created_at': payment.created_at.isoformat(),
            'completed_at': payment.completed_at.isoformat() if payment.completed_at else None,
        } for payment in payments],
        'authentication_activity': [{
            'user_id': item.user_id,
            'email': item.user.email,
            'event': item.title,
            'created_at': item.created_at.isoformat(),
        } for item in auth_activity],
        'system_events': [{
            'event_type': event.event_type,
            'market': event.market,
            'created_at': event.created_at.isoformat(),
        } for event in system_events],
        'system': {
            'database': database_status,
            'cache': cache_status,
            'email': email_configuration_status(),
            'deriv_data': 'unknown' if latest_feed_event is None else (
                'available' if latest_feed_event.event_type == SystemEvent.EVENT_FEED_SUCCEEDED else 'unavailable'
            ),
            'failed_analysis_requests': SystemEvent.objects.filter(
                event_type=SystemEvent.EVENT_ANALYSIS_FAILED,
            ).count(),
        },
    })


def _admin_signal_payload(signal):
    if signal is None:
        return None
    return {
        'id': signal.pk,
        'market': signal.symbol,
        'signal': signal.signal_type,
        'confidence': signal.confidence,
        'entry': str(signal.entry_price) if signal.entry_price is not None else None,
        'stop_loss': str(signal.stop_loss) if signal.stop_loss is not None else None,
        'take_profit': str(signal.take_profit) if signal.take_profit is not None else None,
        'outcome': signal.outcome,
        'created_at': signal.created_at.isoformat(),
    }


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated, AuthorizedAdminPermission])
def admin_user_detail_view(request, user_id):
    from datetime import timedelta
    from django.contrib.sessions.models import Session
    from trading_app.models import PaymentTransaction
    from trading_app.services.subscription_access import bot_access_status, subscription_status

    user = get_object_or_404(User, pk=user_id)
    subscription = getattr(user, 'subscription', None)
    access = subscription_status(user)
    sessions = []
    active_sessions = Session.objects.filter(expire_date__gt=timezone.now()).order_by('expire_date')[:1000]
    for session in active_sessions:
        if str(user.pk) == str(session.get_decoded().get('_auth_user_id')):
            sessions.append({'expires_at': session.expire_date.isoformat()})
    return Response({
        'account': {
            'id': user.pk,
            'first_name': user.first_name,
            'last_name': user.last_name,
            'full_name': user.get_full_name(),
            'email': user.email,
            'phone': user.phone,
            'created_at': user.date_joined.isoformat(),
            'last_login': user.last_login.isoformat() if user.last_login else None,
            'is_active': user.is_active,
            'email_verified': user.email_verified,
            'two_factor_enabled': user.two_factor_enabled,
        },
        'trial': {
            'started_at': user.trial_started_at.isoformat() if user.trial_started_at else None,
            'expires_at': (user.trial_started_at + timedelta(hours=24)).isoformat() if user.trial_started_at else None,
            'status': access['status'] if access['status'] in ('trial', 'expired', 'trial_limit_reached') else 'not_in_trial',
        },
        'subscription': {
            'status': access['status'],
            'tier': access['tier'],
            'started_at': subscription.started_at.isoformat() if subscription else None,
            'expires_at': access['expires_at'].isoformat() if access['expires_at'] else None,
            'bypass': user.subscription_bypass,
        },
        'bot': {
            'access': bot_access_status(user),
            'bypass': user.bot_bypass,
            'status': CacheService.get(f'bot_status_{user.pk}', {'status': 'idle'}),
        },
        'payments': list(PaymentTransaction.objects.filter(user=user).order_by('-created_at').values(
            'id', 'tier', 'amount_kes', 'status', 'mpesa_receipt_number', 'created_at', 'completed_at',
        )[:25]),
        'trades': list(Trade.objects.filter(user=user).order_by('-created_at').values(
            'id', 'symbol', 'action', 'status', 'entry_price', 'exit_price', 'pnl', 'created_at',
        )[:25]),
        'signals': [_admin_signal_payload(signal) for signal in TradingSignal.objects.filter(
            user=user,
        ).order_by('-created_at')[:25]],
        'active_sessions': sessions,
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, AuthorizedAdminPermission])
def admin_user_access_view(request, user_id):
    from django.contrib.sessions.models import Session
    from trading_app.models import AdminAuditLog
    target = get_object_or_404(User, pk=user_id)
    fields = ('is_active', 'subscription_bypass', 'bot_bypass', 'subscription_access_denied', 'revoke_sessions')
    updates = {}
    for field in fields:
        if field in request.data:
            value = request.data[field]
            if not isinstance(value, bool):
                return Response({'error': f'{field} must be a boolean.'}, status=status.HTTP_400_BAD_REQUEST)
            updates[field] = value
    if not updates:
        return Response({'error': 'No supported access changes provided.'}, status=status.HTTP_400_BAD_REQUEST)
    if target.pk == request.user.pk and any(field != 'revoke_sessions' for field in updates):
        return Response({'error': 'Administrator access settings cannot be changed through this endpoint.'}, status=status.HTTP_400_BAD_REQUEST)
    if target.pk == request.user.pk and updates.get('is_active') is False:
        return Response({'error': 'The administrator cannot deactivate their own account.'}, status=status.HTTP_400_BAD_REQUEST)
    revoke_sessions = updates.pop('revoke_sessions', False)
    previous_state = {field: getattr(target, field) for field in updates}
    for field, value in updates.items():
        setattr(target, field, value)
    if updates:
        target.save(update_fields=list(updates))
        for field, value in updates.items():
            if previous_state[field] != value:
                AdminAuditLog.objects.create(
                    administrator=request.user,
                    affected_user=target,
                    action=f'{field}_changed',
                    previous_state={field: previous_state[field]},
                    new_state={field: value},
                    ip_address=request.META.get('REMOTE_ADDR') or None,
                    user_agent=request.META.get('HTTP_USER_AGENT', '')[:512],
                )
    if revoke_sessions or updates.get('is_active') is False:
        for session in Session.objects.filter(expire_date__gte=timezone.now()).iterator():
            if str(target.pk) == str(session.get_decoded().get('_auth_user_id')):
                session.delete()
        target.remember_me_tokens.all().delete()
        AdminAuditLog.objects.create(
            administrator=request.user,
            affected_user=target,
            action='sessions_revoked' if revoke_sessions else 'account_deactivated',
            previous_state={'active_sessions_revoked': False},
            new_state={'active_sessions_revoked': True},
            ip_address=request.META.get('REMOTE_ADDR') or None,
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:512],
        )
    return Response({'status': 'updated', 'user_id': target.pk, 'updated_fields': sorted(updates)})


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_adapter(user):
    return get_adapter_for_broker(user.broker or '')


CHART_GRANULARITY = 60  # matches the frontend 1-minute candle aggregation


def _fetch_chart_candles(user, symbol: str, count: int = 200, granularity: int = CHART_GRANULARITY):
    candles, _source = _fetch_chart_candles_with_source(user, symbol, count, granularity)
    return candles


def _fetch_chart_candles_with_source(user, symbol: str, count: int, granularity: int):
    """Fetch real read-only Deriv candles; never substitute generated prices."""
    live_cache_key = f'live_{symbol}'
    cached = CacheService.get_candles(live_cache_key, granularity)
    if cached and len(cached) >= count:
        from trading_app.services.system_events import record_system_event
        record_system_event('FEED_SUCCEEDED', symbol)
        return cached[-count:], 'live'

    adapter = None
    connected = False
    try:
        adapter = _resolve_adapter(user)
        adapter.connect(build_credentials(user))
        connected = True
        connection_deadline = time.monotonic() + 5
        while not adapter._connected:
            if time.monotonic() >= connection_deadline:
                raise TimeoutError('Deriv WebSocket connection timed out')
            time.sleep(0.05)
        candles = adapter.get_candles(deriv_symbol_for_market(symbol), granularity, count)
        if candles:
            CacheService.set_candles(live_cache_key, granularity, candles)
            from trading_app.services.system_events import record_system_event
            record_system_event('FEED_SUCCEEDED', symbol)
            return candles, 'live'
    except Exception as e:
        logger.warning('Chart candle fetch failed for %s: %s', symbol, e)
    finally:
        if connected:
            try:
                adapter.disconnect()
            except Exception as e:
                logger.warning('Chart adapter disconnect failed for %s: %s', symbol, e)

    from trading_app.services.system_events import record_system_event
    record_system_event('FEED_FAILED', symbol)
    return [], 'unavailable'


def _fetch_verification_candles(user, symbol: str, selected_granularity: int):
    """Fetch fresh, uncached Deriv candles for verification over one connection."""
    import threading
    from trading_app.strategies.choch import TIMEFRAME_LABELS

    required = tuple(TIMEFRAME_LABELS)
    granularities = tuple(dict.fromkeys((*required, selected_granularity)))
    adapter = _resolve_adapter(user)
    raw_by_granularity = {}
    now_epoch = time.time()
    tick_received = threading.Event()
    tick_subscription_started = False
    requested_deriv_symbol = deriv_symbol_for_market(symbol)

    def accept_current_tick(tick):
        tick_timestamp = getattr(tick, 'timestamp', None)
        if getattr(tick, 'symbol', None) != requested_deriv_symbol or tick_timestamp is None:
            return
        tick_age = timezone.now().timestamp() - tick_timestamp.timestamp()
        if -5 <= tick_age <= 60:
            tick_received.set()

    try:
        adapter.connect(build_credentials(user))
        connection_deadline = time.monotonic() + 5
        while not adapter._connected:
            if time.monotonic() >= connection_deadline:
                raise TimeoutError('Deriv WebSocket connection timed out')
            time.sleep(0.05)
        adapter.subscribe_ticks(
            requested_deriv_symbol, accept_current_tick,
        )
        tick_subscription_started = True
        if not tick_received.wait(timeout=8):
            return [], {}, 'Live market data unavailable: no current Deriv tick received.'
        try:
            adapter.unsubscribe_ticks(requested_deriv_symbol)
            tick_subscription_started = False
        except Exception:
            pass
        for granularity in granularities:
            try:
                raw = adapter.get_candles(requested_deriv_symbol, granularity, 200)
            except Exception as exc:
                message = str(exc).lower()
                if 'presently closed' in message or 'market is closed' in message or 'market closed' in message:
                    return [], {}, f'Market closed: {symbol} is not currently trading.'
                return [], {}, f'Live market data unavailable for {TIMEFRAME_LABELS.get(granularity, granularity)}.'
            if not raw:
                return [], {}, f'Live market data unavailable for {TIMEFRAME_LABELS.get(granularity, granularity)}.'
            newest_epoch = max(int(candle.timestamp.timestamp()) for candle in raw)
            if any(candle.symbol != requested_deriv_symbol for candle in raw):
                return [], {}, f'Live market data unavailable: {TIMEFRAME_LABELS.get(granularity, granularity)} market mismatch.'
            max_age = max(granularity * 2, 120)
            if now_epoch - newest_epoch < -5 or now_epoch - newest_epoch > max_age:
                return [], {}, f'Live market data unavailable: {TIMEFRAME_LABELS.get(granularity, granularity)} candles are stale.'
            raw_by_granularity[granularity] = raw
    except Exception as exc:
        message = str(exc).lower()
        if 'presently closed' in message or 'market is closed' in message or 'market closed' in message:
            return [], {}, f'Market closed: {symbol} is not currently trading.'
        logger.warning('Verification candle fetch failed for %s: %s', symbol, exc)
        return [], {}, 'Live market data unavailable.'
    finally:
        if tick_subscription_started:
            try:
                adapter.unsubscribe_ticks(requested_deriv_symbol)
            except Exception:
                pass
        try:
            adapter.disconnect()
        except Exception:
            pass

    raw_candles = raw_by_granularity[selected_granularity]
    mtf = {
        TIMEFRAME_LABELS[granularity]: _candles_to_dicts(raw_by_granularity[granularity])
        for granularity in required
    }
    return raw_candles, mtf, None


def deriv_symbol_for_market(symbol: str) -> str:
    """Translate a dashboard market label to the symbol expected by Deriv."""
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS

    return deriv_symbol_for(MARKET_SYMBOLS.get(symbol, symbol))


def _candles_to_dicts(candles):
    return [{
        'open': float(c.open),
        'high': float(c.high),
        'low': float(c.low),
        'close': float(c.close),
        'volume': float(c.volume),
        'time': int(c.timestamp.timestamp()),
    } for c in candles]


# ---------------------------------------------------------------------------
# Bot market selection — the bot only trades the market the user selected
# ---------------------------------------------------------------------------

@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def bot_market_view(request):
    if request.method == 'GET':
        return Response({'market': request.user.selected_market or 'EUR/USD'})

    market = str(request.data.get('market', '')).strip()
    if not market:
        return Response({'error': 'market is required'}, status=status.HTTP_400_BAD_REQUEST)
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS
    if market not in MARKET_SYMBOLS:
        return Response({'error': 'Unsupported market'}, status=status.HTTP_400_BAD_REQUEST)
    request.user.selected_market = market
    request.user.save(update_fields=['selected_market'])
    return Response({'market': request.user.selected_market})


@api_view(['GET', 'POST'])
@permission_classes([permissions.IsAuthenticated])
def bot_control_view(request):
    cache_key = f'bot_status_{request.user.id}'
    current = CacheService.get(cache_key, {'status': 'idle', 'market': request.user.selected_market or 'EUR/USD'})
    if request.method == 'GET':
        return Response(current)

    action = str(request.data.get('action', '')).lower()
    if action not in ('start', 'stop', 'running', 'results'):
        return Response({'error': 'action must be start, stop, running, or results'}, status=status.HTTP_400_BAD_REQUEST)
    if action == 'start':
        access = _subscription_access_response(request.user)
        if access is not None:
            return access
        if current.get('status') == 'running':
            return Response({'error': 'Bot is already running'}, status=status.HTTP_409_CONFLICT)
        market = str(request.data.get('market', request.user.selected_market or 'EUR/USD')).strip()
        from trading_app.management.commands.train_all_models import MARKET_SYMBOLS
        if market not in MARKET_SYMBOLS:
            return Response({'error': 'Unsupported market'}, status=status.HTTP_400_BAD_REQUEST)
        if request.user.selected_market != market:
            request.user.selected_market = market
            request.user.save(update_fields=['selected_market'])
        current = {'status': 'running', 'market': market}
    elif action == 'stop':
        current = {'status': 'idle', 'market': request.user.selected_market or 'EUR/USD'}
    elif action == 'results':
        if current.get('status') != 'running':
            return Response({'error': 'Bot is not running'}, status=status.HTTP_409_CONFLICT)
        current = {'status': 'results', 'market': request.user.selected_market or 'EUR/USD'}
    else:
        if current.get('status') not in ('running', 'results'):
            return Response({'error': 'Bot must be started before analysis'}, status=status.HTTP_409_CONFLICT)
        current = {'status': 'running', 'market': request.user.selected_market or 'EUR/USD'}

    CacheService.set(cache_key, current, timeout=3600)
    return Response(current)


# ---------------------------------------------------------------------------
# Chart Candles — live Deriv history for the bot chart
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
@throttle_classes([UserRateThrottle])
def chart_candles_view(request):
    symbol = request.query_params.get('symbol', 'EUR/USD')
    from trading_app.management.commands.train_all_models import MARKET_SYMBOLS
    if symbol not in MARKET_SYMBOLS:
        return Response({'error': 'Unsupported market'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        granularity = int(request.query_params.get('granularity', 900))
    except ValueError:
        return Response({'error': 'granularity must be an integer'}, status=status.HTTP_400_BAD_REQUEST)
    from trading_app.trading_bot.deriv_adapter import GRANULARITY_MAP
    if granularity not in GRANULARITY_MAP:
        return Response({'error': 'Unsupported granularity'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        count = int(request.query_params.get('count', 200))
    except ValueError:
        count = 200
    count = max(10, min(count, 2000))

    candles, source = _fetch_chart_candles_with_source(
        request.user, symbol, count=count, granularity=granularity
    )
    if not candles:
        return Response({
            'symbol': symbol,
            'granularity': granularity,
            'source': 'unavailable',
            'error': 'Live market data unavailable',
            'candles': [],
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)

    data = [{
        'time': int(c.timestamp.timestamp()),
        'open': float(c.open),
        'high': float(c.high),
        'low': float(c.low),
        'close': float(c.close),
        'volume': float(c.volume),
    } for c in candles]

    data.sort(key=lambda c: c['time'])
    return Response({
        'symbol': symbol,
        'granularity': granularity,
        'source': source,
        'candles': data,
    })
