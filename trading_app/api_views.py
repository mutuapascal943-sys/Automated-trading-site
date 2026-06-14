import time
import logging
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.conf import settings
from rest_framework import status, viewsets, generics, permissions
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.response import Response
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle
from .models import (
    Trade, TradingSignal, RAGDocument, RAGChunk,
    LLMQuery, Subscription, EmailOTP, SecurityQuestion,
)
from .serializers import (
    TradeSerializer, TradeCreateSerializer, TradingSignalSerializer,
    RAGDocumentSerializer, LLMQuerySerializer, LLMQueryCreateSerializer,
    SubscriptionSerializer, MarketDataSerializer, BrokerConfigSerializer,
    OTPVerifySerializer, UserSerializer,
)
from .services.llm_service import llm_service
from .services.rag_engine import rag_engine
from .services.broker_service import BrokerService
from .services.cache_service import CacheService
from .services.email_service import generate_otp, send_otp_email
from .decorators import two_factor_required_api

User = get_user_model()
logger = logging.getLogger(__name__)


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

        broker = BrokerService(
            api_key=request.user.broker_api_key,
            api_secret=request.user.broker_api_secret,
            account_id=request.user.broker_account_id,
        )
        if broker.is_configured():
            result = broker.execute_trade(
                symbol=trade.symbol,
                action=trade.action,
                volume=float(trade.volume),
                order_type=trade.order_type,
            )
            if result:
                trade.is_live = True
                trade.broker_trade_id = result.get('trade_id', '')
                trade.status = 'OPEN'
                trade.executed_at = timezone.now()
                trade.save()
                return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)

        trade.status = 'OPEN'
        trade.executed_at = timezone.now()
        trade.save()
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
                        all_chunks.append({'id': c.chunk_id, 'text': c.text})
                ranked = rag_engine.rank_chunks(prompt, all_chunks)
                context_texts = [c['text'] for c in ranked]
                response_text = llm_service.rag_query(prompt, context_texts)
            elif query_type == 'sentiment':
                response_text = 'Sentiment analysis not yet implemented'
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


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def market_data_view(request):
    symbol = request.query_params.get('symbol', 'EUR/USD')
    cached = CacheService.get_market_data(symbol)
    if cached:
        return Response(cached)

    broker = BrokerService(
        api_key=request.user.broker_api_key,
        api_secret=request.user.broker_api_secret,
        account_id=request.user.broker_account_id,
    )
    data = broker.get_market_data(symbol)
    if data:
        CacheService.set_market_data(symbol, data)
        return Response(data)

    return Response({'symbol': symbol, 'price': '0', 'message': 'Using simulated data'})


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def execute_trade_view(request):
    serializer = TradeCreateSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    today = timezone.now().date()

    if user.last_trade_date != today:
        user.daily_trades_count = 0
        user.last_trade_date = today

    if user.daily_trades_count >= 50:
        return Response(
            {'error': 'Daily trade limit reached (50 trades/day)'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    trade = serializer.save(
        user=user,
        status='PENDING',
        is_live=False,
    )

    broker = BrokerService(
        api_key=user.broker_api_key,
        api_secret=user.broker_api_secret,
        account_id=user.broker_account_id,
    )
    if broker.is_configured():
        result = broker.execute_trade(
            symbol=trade.symbol,
            action=trade.action,
            volume=float(trade.volume),
            order_type=trade.order_type,
        )
        if result:
            trade.is_live = True
            trade.broker_trade_id = result.get('trade_id', '')
            trade.status = 'OPEN'
            trade.executed_at = timezone.now()
            trade.save()
            user.daily_trades_count += 1
            user.save()
            return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)

    trade.status = 'OPEN'
    trade.executed_at = timezone.now()
    trade.save()
    user.daily_trades_count += 1
    user.save()

    return Response(TradeSerializer(trade).data, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def configure_broker_view(request):
    serializer = BrokerConfigSerializer(data=request.data)
    serializer.is_valid(raise_exception=True)

    user = request.user
    user.broker_api_key = serializer.validated_data['api_key']
    user.broker_api_secret = serializer.validated_data['api_secret']
    user.broker_account_id = serializer.validated_data.get('account_id', '')
    user.save()

    return Response({'status': 'Broker configured successfully'})


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
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

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
    if sent:
        return Response({'status': 'OTP sent to your email'})
    return Response(
        {'error': 'Failed to send email. Please try again.'},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )


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
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    # Check cache first (fast path)
    if CacheService.verify_otp(user.id, code):
        otp = EmailOTP.objects.filter(
            user=user, code=code, purpose=purpose, is_used=False
        ).last()
        if otp and otp.is_valid():
            otp.is_used = True
            otp.save()
            return Response({'status': 'verified', 'user_id': user.id})

    # Check database (slow path)
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
    user = request.user
    open_trades = Trade.objects.filter(user=user, status='OPEN').count()
    total_trades = Trade.objects.filter(user=user).count()
    win_trades = Trade.objects.filter(user=user, status='CLOSED', pnl__gt=0).count()
    closed_trades = Trade.objects.filter(user=user, status='CLOSED').count()
    win_rate = (win_trades / closed_trades * 100) if closed_trades > 0 else 0

    recent_signals = TradingSignal.objects.filter(user=user).order_by('-created_at')[:5]
    signal_data = TradingSignalSerializer(recent_signals, many=True).data

    return Response({
        'balance': float(user.balance),
        'open_trades': open_trades,
        'total_trades': total_trades,
        'win_rate': round(win_rate, 1),
        'daily_trades_count': user.daily_trades_count,
        'recent_signals': signal_data,
    })


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_request(request):
    email = request.data.get('email', '')
    if not email:
        return Response({'error': 'Email is required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response({'error': 'No account found with that email'}, status=status.HTTP_404_NOT_FOUND)

    if not CacheService.check_rate_limit(f'api_pwd_reset_{user.id}', max_attempts=3, window=300):
        return Response(
            {'error': 'Too many requests. Please try again in 5 minutes.'},
            status=status.HTTP_429_TOO_MANY_REQUESTS,
        )

    otp_code = generate_otp()
    expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    EmailOTP.objects.create(
        user=user, code=otp_code, purpose='password_reset', expires_at=expires_at
    )
    CacheService.set_otp(user.id, otp_code)
    send_otp_email(user, otp_code)

    return Response({'status': 'OTP sent', 'user_id': user.id})


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_verify(request):
    email = request.data.get('email', '')
    code = request.data.get('code', '')

    if not email or not code:
        return Response({'error': 'Email and code are required'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    if CacheService.verify_otp(user.id, code):
        otp = EmailOTP.objects.filter(
            user=user, code=code, purpose='password_reset', is_used=False
        ).last()
        if otp and otp.is_valid():
            otp.is_used = True
            otp.save()
            CacheService.reset_rate_limit(f'api_pwd_reset_{user.id}')
            return Response({'status': 'verified', 'reset_token': str(user.id)})

    return Response({'error': 'Invalid or expired code'}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def api_password_reset_confirm(request):
    email = request.data.get('email', '')
    new_password = request.data.get('new_password', '')

    if not email or not new_password:
        return Response({'error': 'Email and new_password are required'}, status=status.HTTP_400_BAD_REQUEST)

    if len(new_password) < 8:
        return Response({'error': 'Password must be at least 8 characters'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        user = User.objects.get(email__iexact=email)
    except User.DoesNotExist:
        return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)

    user.set_password(new_password)
    user.save()

    # Invalidate all sessions for this user
    from django.contrib.sessions.models import Session
    sessions = Session.objects.filter(expire_date__gte=timezone.now())
    for session in sessions:
        data = session.get_decoded()
        if str(user.id) == str(data.get('_auth_user_id')):
            session.delete()

    return Response({'status': 'Password reset successful'})
