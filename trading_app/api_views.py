import secrets
import time
import logging
from decimal import Decimal
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework import status, viewsets, generics, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from .models import (
    Trade, TradingSignal, RAGDocument, RAGChunk,
    LLMQuery, Subscription, EmailOTP, SecurityQuestion, Notification, RiskConfig,
)
from .serializers import (
    TradeSerializer, TradeCreateSerializer, TradingSignalSerializer,
    RAGDocumentSerializer, LLMQuerySerializer, LLMQueryCreateSerializer,
    SubscriptionSerializer, MarketDataSerializer, BrokerConfigSerializer,
    OTPVerifySerializer, UserSerializer, RiskConfigSerializer,
)
from .services.llm_service import llm_service
from .services.rag_engine import rag_engine
from .services.cache_service import CacheService
from .services.email_service import generate_otp, send_otp_email
from .services.credential_encrypt import encrypt, decrypt
from .services.adapter_resolver import get_adapter_for_broker, build_credentials
from .services.ticker_bridge import TickerBridge
from .trading_bot.paper_broker import PaperBrokerAdapter
from .trading_bot.risk_engine import RiskEngine, PositionSizing
from .trading_bot.interface import OrderRequest as RiskOrderRequest
from .trading_bot.logging_utils import ConsoleAuditLogger
from .decorators import two_factor_required_api

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
        serializer = TradeCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        trade = serializer.save(user=request.user, status='PENDING')

        trade = _execute_via_adapter(request.user, trade)
        return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)


class SignalViewSet(viewsets.ModelViewSet):
    serializer_class = TradingSignalSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return TradingSignal.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(user=self.request.user)


class RAGDocumentViewSet(viewsets.ModelViewSet):
    serializer_class = RAGDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return RAGDocument.objects.filter(user=self.request.user).order_by('-created_at')

    def perform_create(self, serializer):
        doc = serializer.save(user=self.request.user)
        chunks = rag_engine.process_document(
            title=doc.title,
            content=doc.content,
            source=doc.source,
        )
        chunk_objs = []
        for chunk_data in chunks:
            chunk_objs.append(RAGChunk(
                document=doc,
                chunk_id=chunk_data['id'],
                text=chunk_data['text'],
                start_pos=chunk_data['start_pos'],
                end_pos=chunk_data['end_pos'],
                embedding=chunk_data.get('embedding'),
            ))
        RAGChunk.objects.bulk_create(chunk_objs)
        doc.chunk_count = len(chunks)
        doc.is_indexed = True
        doc.save()


class LLMQueryView(generics.CreateAPIView):
    serializer_class = LLMQueryCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def create(self, request, *args, **kwargs):
        serializer = LLMQueryCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        query_type = serializer.validated_data['query_type']
        prompt = serializer.validated_data['prompt']
        market_data = serializer.validated_data.get('market_data', '')

        start_time = time.time()
        query = LLMQuery.objects.create(
            user=request.user,
            query_type=query_type,
            prompt=prompt,
            context_used={'market_data': market_data} if market_data else None,
        )

        try:
            if query_type == 'market_analysis':
                result = llm_service.analyze_market(market_data or prompt)
                response_text = str(result)
            elif query_type == 'trading_idea':
                result = llm_service.generate_trading_idea(prompt)
                response_text = str(result)
            elif query_type == 'rag_query':
                docs = RAGDocument.objects.filter(user=request.user)
                all_chunks = []
                for doc in docs:
                    chunks = RAGChunk.objects.filter(document=doc)
                    for c in chunks:
                        all_chunks.append({
                            'id': c.chunk_id,
                            'text': c.text,
                            'embedding': c.embedding,
                        })
                ranked = rag_engine.rank_chunks(prompt, all_chunks)
                context_texts = [c['text'] for c in ranked]
                response_text = llm_service.rag_query(prompt, context_texts)
            elif query_type == 'sentiment':
                response_text = llm_service.analyze_sentiment(prompt)
            else:
                response_text = 'Unknown query type'

            latency = int((time.time() - start_time) * 1000)
            query.response = response_text
            query.latency_ms = latency
            query.success = True
            query.save()

            return Response(LLMQuerySerializer(query).data, status=status.HTTP_200_OK)

        except Exception as e:
            latency = int((time.time() - start_time) * 1000)
            query.response = f'Error: {str(e)}'
            query.latency_ms = latency
            query.success = False
            query.save()
            return Response(
                {'error': str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )


# ---------------------------------------------------------------------------
# Market Data
# ---------------------------------------------------------------------------

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def market_data_view(request):
    symbol = request.query_params.get('symbol', 'EUR/USD')
    cached = CacheService.get_market_data(symbol)
    if cached:
        return Response(cached)

    adapter = _resolve_adapter(request.user)
    creds = build_credentials(request.user)
    try:
        adapter.connect(creds)
        candles = adapter.get_candles(symbol, 3600, 1)
        adapter.disconnect()
        if candles:
            data = {
                'symbol': symbol,
                'price': str(candles[-1].close),
                'high': str(candles[-1].high),
                'low': str(candles[-1].low),
                'open': str(candles[-1].open),
                'timestamp': candles[-1].timestamp.isoformat(),
            }
            CacheService.set_market_data(symbol, data)
            return Response(data)
    except Exception as e:
        logger.warning('Market data fetch failed: %s', e)

    cached = CacheService.get_market_data(symbol)
    if cached:
        return Response(cached)
    return Response({'symbol': symbol, 'price': '0', 'message': 'Using simulated data'})


# ---------------------------------------------------------------------------
# Trade Execution
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def execute_trade_view(request):
    serializer = TradeCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    risk = RiskConfig.get_for_user(user)

    if not risk.trading_enabled:
        return Response({'error': 'Trading is disabled in your risk settings'}, status=status.HTTP_403_FORBIDDEN)

    today = timezone.now().date()
    if user.last_trade_date != today:
        user.daily_trades_count = 0
        user.last_trade_date = today

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
        user.save()

    return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)


def _execute_via_adapter(user, trade, risk=None):
    if risk is None:
        risk = RiskConfig.get_for_user(user)

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

    today = timezone.now().date()
    from django.db.models import Sum
    daily_pnl = Trade.objects.filter(
        user=user, status='CLOSED', closed_at__date=today
    ).aggregate(total=Sum('pnl'))['total'] or Decimal('0')
    engine.record_trade_pnl(daily_pnl)

    fake_signal = RiskOrderRequest(
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
            order = paper.place_order(RiskOrderRequest(
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
            create_notification(user, 'Paper Trade Opened', f'{trade.action} {trade.symbol} at {trade.entry_price}', 'trade')
            audit.log_order('paper_filled', order.id, {'trade_id': trade.id, 'symbol': trade.symbol})
        except Exception as e:
            logger.error(f'Paper execution failed for trade {trade.id}: {e}')
            trade.status = 'FAILED'
            trade.save()
        return trade

    adapter = _resolve_adapter(user)
    creds = build_credentials(user)
    try:
        adapter.connect(creds)
        order = adapter.place_order(RiskOrderRequest(
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
        create_notification(user, 'Live Trade Opened', f'{trade.action} {trade.symbol} at {trade.entry_price}', 'trade')
        audit.log_order('live_filled', order.id, {'trade_id': trade.id, 'symbol': trade.symbol})
    except Exception as e:
        logger.error('Live execution failed: %s', e)
        trade.status = 'FAILED'
        trade.save()
        create_notification(user, f'Trade Failed — {trade.symbol}', f'{trade.action} {trade.symbol} failed: {str(e)[:200]}', 'trade')
        audit.log_error('live_execution', str(e), {'trade_id': trade.id, 'symbol': trade.symbol})

    return trade


# ---------------------------------------------------------------------------
# Trade Modification (SL/TP)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def modify_trade_view(request, trade_id):
    trade = get_object_or_404(Trade, id=trade_id, user=request.user, status='OPEN')

    stop_loss = request.data.get('stop_loss')
    take_profit = request.data.get('take_profit')

    if stop_loss is not None:
        trade.stop_loss = Decimal(str(stop_loss))
    if take_profit is not None:
        trade.take_profit = Decimal(str(take_profit))

    trade.save()
    audit.log_order('trade_modified', str(trade.id), {
        'stop_loss': str(trade.stop_loss),
        'take_profit': str(trade.take_profit),
    })
    return Response(TradeSerializer(trade).data)


# ---------------------------------------------------------------------------
# Broker Configuration & Health Check
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def configure_broker_view(request):
    serializer = BrokerConfigSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    if 'api_key' in serializer.validated_data and serializer.validated_data['api_key']:
        user.broker_api_key = encrypt(serializer.validated_data['api_key'])
    if 'api_secret' in serializer.validated_data and serializer.validated_data['api_secret']:
        user.broker_api_secret = encrypt(serializer.validated_data['api_secret'])
    if 'account_id' in serializer.validated_data:
        user.broker_account_id = serializer.validated_data['account_id']
    if 'paper_mode' in serializer.validated_data:
        user.paper_mode = serializer.validated_data['paper_mode']
    if 'broker' in serializer.validated_data:
        user.broker = serializer.validated_data['broker']
    user.save()

    adapter = _resolve_adapter(user)
    creds = build_credentials(user)
    connected = False
    error_msg = ''
    try:
        adapter.connect(creds)
        adapter.disconnect()
        connected = True
    except Exception as e:
        error_msg = str(e)
        logger.warning(f'Broker connection test failed for user {user.id}: {e}')

    audit.log_connection(user.broker or 'unknown', 'configured', {
        'user_id': user.id, 'connected': connected,
    })
    return Response({
        'status': 'Broker configured successfully',
        'paper_mode': user.paper_mode,
        'connection_test': {
            'success': connected,
            'error': error_msg if not connected else '',
        },
    })


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def broker_status_view(request):
    user = request.user
    configured = bool(user.broker_api_key) and bool(user.broker)

    health = {'reachable': False, 'error': ''}
    if configured and not user.paper_mode:
        adapter = _resolve_adapter(user)
        creds = build_credentials(user)
        try:
            adapter.connect(creds)
            adapter.disconnect()
            health['reachable'] = True
        except Exception as e:
            health['error'] = str(e)

    return Response({
        'broker': user.broker,
        'paper_mode': user.paper_mode,
        'configured': configured,
        'health': health,
        'broker_list': ['Deriv', 'Binance'],
    })


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def broker_health_check_view(request):
    user = request.user
    adapter = _resolve_adapter(user)
    creds = build_credentials(user)
    start = time.time()
    try:
        adapter.connect(creds)
        latency_ms = int((time.time() - start) * 1000)
        adapter.disconnect()
        return Response({
            'status': 'healthy',
            'broker': user.broker,
            'latency_ms': latency_ms,
        })
    except Exception as e:
        latency_ms = int((time.time() - start) * 1000)
        return Response({
            'status': 'unhealthy',
            'broker': user.broker,
            'error': str(e),
            'latency_ms': latency_ms,
        }, status=status.HTTP_503_SERVICE_UNAVAILABLE)


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
# Market Analysis (wired to LLM + signal creation)
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def analyze_and_signal_view(request):
    serializer = LLMQueryCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    symbol = serializer.validated_data.get('prompt', 'EUR/USD')
    try:
        candles = []
        if not request.user.paper_mode:
            adapter = _resolve_adapter(request.user)
            creds = build_credentials(request.user)
            adapter.connect(creds)
            candles = adapter.get_candles(symbol, 3600, 30)
            adapter.disconnect()

        from trading_app.trading_bot.llm_analyzer import LLMAnalyzer, OpenAIProvider
        from decouple import config as decouple_config

        api_key = decouple_config('OPENAI_API_KEY', default='')
        if api_key:
            provider = OpenAIProvider(api_key=api_key)
            analyzer = LLMAnalyzer(provider=provider)
            analysis = analyzer.analyze(symbol, candles)
        else:
            analysis = None

        bias = analysis.bias if analysis else 'neutral'
        confidence = int((analysis.confidence if analysis else 0) * 100)

        signal = TradingSignal.objects.create(
            user=request.user,
            symbol=symbol,
            signal_type='BUY' if bias == 'bullish' else 'SELL' if bias == 'bearish' else 'HOLD',
            confidence=confidence,
            reasoning=analysis.rationale if analysis else 'LLM not configured',
            source='AI',
            risk_level='MEDIUM',
        )
        audit.log_signal(symbol, signal.signal_type, confidence, {
            'signal_id': signal.id, 'rationale': signal.reasoning,
        })

        risk = RiskConfig.get_for_user(request.user)
        if risk.auto_execute and signal.signal_type != 'HOLD' and risk.trading_enabled:
            if confidence < risk.min_confidence:
                return Response({
                    'signal': TradingSignalSerializer(signal).data,
                    'analysis': {
                        'bias': bias,
                        'confidence': confidence,
                        'rationale': analysis.rationale if analysis else '',
                    },
                    'message': f'Confidence {confidence}% below threshold {risk.min_confidence}%. Signal created but not auto-executed.',
                })

            from decimal import Decimal
            trade_data = {
                'symbol': symbol,
                'action': signal.signal_type,
                'volume': float(risk.max_position_size),
                'entry_price': 0,
                'stop_loss': signal.stop_loss or 0,
                'take_profit': signal.take_profit or 0,
                'order_type': 'MARKET',
            }
            inner_serializer = TradeCreateSerializer(data=trade_data)
            if inner_serializer.is_valid():
                trade = inner_serializer.save(user=request.user, status='PENDING', is_live=not request.user.paper_mode)
                _execute_via_adapter(request.user, trade, risk)
                signal.is_executed = trade.status == 'OPEN'
                signal.save()
                return Response({
                    'signal': TradingSignalSerializer(signal).data,
                    'trade': TradeSerializer(trade).data,
                    'analysis': {
                        'bias': bias,
                        'confidence': confidence,
                        'rationale': analysis.rationale if analysis else '',
                    },
                })

        return Response({
            'signal': TradingSignalSerializer(signal).data,
            'analysis': {
                'bias': bias,
                'confidence': confidence,
                'rationale': analysis.rationale if analysis else '',
            },
        })

    except Exception as e:
        logger.error('Analysis failed: %s', e)
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


# ---------------------------------------------------------------------------
# OTP, Password Reset, Dashboard, Notifications
# ---------------------------------------------------------------------------

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def request_otp_view(request):
    email = request.data.get('email', '')
    purpose = request.data.get('purpose', '2fa')

    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({'status': 'If an account with that email exists, an OTP has been sent.'})

    if not CacheService.check_rate_limit(f'otp_{user.id}', max_attempts=3, window=60):
        return Response(
            {'error': 'Too many OTP requests. Please wait 60 seconds.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    otp_code = generate_otp()
    expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)

    EmailOTP.objects.create(
        user=user,
        code=otp_code,
        purpose=purpose,
        expires_at=expires_at,
    )
    CacheService.set_otp(user.id, otp_code)

    sent = send_otp_email(user, otp_code)
    return Response({'status': 'If an account with that email exists, an OTP has been sent.'})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def verify_otp_view(request):
    serializer = OTPVerifySerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    email = request.data.get('email', '')
    code = serializer.validated_data['code']
    purpose = serializer.validated_data['purpose']

    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email=email)
    except User.DoesNotExist:
        return Response({'error': 'Invalid or expired code'}, status=status.HTTP_400_BAD_REQUEST)

    if CacheService.verify_otp(user.id, code):
        otp = EmailOTP.objects.filter(
            user=user, code=code, purpose=purpose, is_used=False
        ).last()
        if otp and otp.is_valid():
            otp.is_used = True
            otp.save()
            return Response({'status': 'verified', 'user_id': user.id})

    otp = EmailOTP.objects.filter(
        user=user, code=code, purpose=purpose, is_used=False
    ).last()

    if otp and otp.is_valid():
        otp.is_used = True
        otp.save()
        CacheService.delete_otp(user.id)
        return Response({'status': 'verified', 'user_id': user.id})

    return Response({'error': 'Invalid or expired code'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def subscription_view(request):
    sub, _ = Subscription.objects.get_or_create(
        user=request.user,
        defaults={'tier': 'FREE', 'is_active': True},
    )
    return Response(SubscriptionSerializer(sub).data)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def dashboard_stats_view(request):
    from django.db.models import Sum, Q
    from django.db.models.functions import TruncDate
    from datetime import timedelta

    user = request.user
    open_trades = Trade.objects.filter(user=user, status='OPEN').count()
    total_trades = Trade.objects.filter(user=user).count()
    closed_trades = Trade.objects.filter(user=user, status='CLOSED')
    win_trades = closed_trades.filter(pnl__gt=0).count()
    loss_trades = closed_trades.filter(pnl__lt=0).count()
    win_rate = (win_trades / closed_trades.count() * 100) if closed_trades.count() > 0 else 0

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

    loss_rate = round(loss_trades / closed_trades.count() * 100, 1) if closed_trades.count() > 0 else 0

    risk = RiskConfig.get_for_user(user)
    recent_signals = TradingSignal.objects.filter(user=user).order_by('-created_at')[:5]
    signal_data = TradingSignalSerializer(recent_signals, many=True).data

    return Response({
        'balance': float(user.balance),
        'open_trades': open_trades,
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
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

    if not CacheService.check_rate_limit(f'api_pwd_reset_{user.id}', max_attempts=3, window=300):
        return Response(response_msg)

    otp_code = generate_otp()
    expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    EmailOTP.objects.create(
        user=user, code=otp_code, purpose='password_reset', expires_at=expires_at
    )
    CacheService.set_otp(user.id, otp_code)
    send_otp_email(user, otp_code)

    reset_token = secrets.token_urlsafe(32)
    CacheService.set(f'pwd_reset_token_{reset_token}', user.id, timeout=600)

    request.session['password_reset_token'] = reset_token

    return Response({'status': 'OTP sent', 'reset_token': reset_token})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_verify(request):
    reset_token = request.data.get('reset_token', '')
    code = request.data.get('code', '')

    if not reset_token or not code:
        return Response({'error': 'reset_token and code are required'}, status=status.HTTP_400_BAD_REQUEST)

    from .services.cache_service import CacheService
    user_id = CacheService.get(f'pwd_reset_token_{reset_token}')
    if not user_id:
        return Response({'error': 'Invalid or expired reset session'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_400_BAD_REQUEST)

    if CacheService.verify_otp(user.id, code):
        otp = EmailOTP.objects.filter(
            user=user, code=code, purpose='password_reset', is_used=False
        ).last()
        if otp and otp.is_valid():
            otp.is_used = True
            otp.save()
            CacheService.reset_rate_limit(f'api_pwd_reset_{user.id}')
            return Response({'status': 'verified', 'reset_token': reset_token})

    return Response({'error': 'Invalid or expired code'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_confirm(request):
    reset_token = request.data.get('reset_token', '')
    new_password = request.data.get('new_password', '')

    if not reset_token or not new_password:
        return Response({'error': 'reset_token and new_password are required'}, status=status.HTTP_400_BAD_REQUEST)

    user_id = CacheService.get(f'pwd_reset_token_{reset_token}')
    if not user_id:
        return Response({'error': 'Invalid or expired reset session'}, status=status.HTTP_400_BAD_REQUEST)

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

    CacheService.delete(f'pwd_reset_token_{reset_token}')

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
def run_backtest_view(request):
    from .trading_bot.backtest import run_backtest, BacktestResult
    from .trading_bot.interface import Candle, OrderRequest

    symbol = request.data.get('symbol', 'EUR/USD')
    initial_balance = Decimal(str(request.data.get('initial_balance', 10000)))
    days = int(request.data.get('days', 30))
    stop_loss_pct = Decimal(str(request.data.get('stop_loss_pct', '2')))
    take_profit_pct = Decimal(str(request.data.get('take_profit_pct', '4')))

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
        from datetime import datetime as _dt, timedelta, timezone as _tz
        import random
        raw_candles = []
        now = _dt.now(_tz.utc)
        for i in range(min(days * 24, 720)):
            ts = now - timedelta(hours=days * 24 - i)
            base = Decimal('1.05') if 'EUR' in symbol else Decimal('1.25')
            raw_candles.append(Candle(
                symbol=symbol,
                open=base + Decimal(str(random.uniform(-0.01, 0.01))),
                high=base + Decimal(str(random.uniform(0, 0.02))),
                low=base + Decimal(str(random.uniform(-0.02, 0))),
                close=base + Decimal(str(random.uniform(-0.01, 0.01))),
                volume=Decimal(str(random.randint(100, 10000))),
                timestamp=ts,
                granularity=3600,
            ))

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
    TickerBridge.ensure_subscription(symbol, user=request.user)
    return Response({'status': 'ok', 'symbol': symbol})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def ticker_unsubscribe_view(request):
    symbol = request.data.get('symbol', '').strip()
    if not symbol:
        return Response({'error': 'symbol required'}, status=status.HTTP_400_BAD_REQUEST)
    TickerBridge.remove_subscription(symbol, str(request.user.id))
    return Response({'status': 'ok', 'symbol': symbol})


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


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _resolve_adapter(user):
    return get_adapter_for_broker(user.broker or '')
