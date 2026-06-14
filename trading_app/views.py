import json
import logging
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
from .forms import RegisterForm, LoginForm, OTPForm
from .models import User, EmailOTP, Trade, TradingSignal, RAGDocument, RAGChunk, LLMQuery
from .decorators import two_factor_required
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
from .services.llm_service import llm_service
from .services.rag_engine import rag_engine
from .services.broker_service import BrokerService

logger = logging.getLogger(__name__)


def register_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = RegisterForm()
    if request.method == 'POST':
        form = RegisterForm(request.POST)
        if form.is_valid():
            user = form.save(commit=False)
            user.email = form.cleaned_data['email']
            user.broker = form.cleaned_data['broker']
            user.save()
            login(request, user)

            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(user.id, otp_code)
            send_otp_email(user, otp_code, request)

            messages.success(request, 'Account created! Check your email for the verification code.')
            return redirect('setup_2fa')
    return render(request, 'registration/register.html', {'form': form})


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm()
    if request.method == 'POST':
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            if not CacheService.check_rate_limit(
                f'login_{user.id}',
                max_attempts=settings.MAX_LOGIN_ATTEMPTS,
                window=300,
            ):
                messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
                return render(request, 'registration/login.html', {'form': form})

            login(request, user)

            if user.two_factor_enabled:
                otp_code = generate_otp()
                expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
                EmailOTP.objects.create(
                    user=user, code=otp_code, purpose='2fa', expires_at=expires_at
                )
                CacheService.set_otp(user.id, otp_code)
                send_otp_email(user, otp_code, request)
                messages.info(request, 'A verification code has been sent to your email.')
                return redirect('verify_2fa')

            CacheService.reset_rate_limit(f'login_{user.id}')
            messages.success(request, f'Welcome back, {user.email}!')
            return redirect('dashboard')
    return render(request, 'registration/login.html', {'form': form})


@login_required
def setup_2fa_view(request):
    if request.user.two_factor_enabled:
        messages.info(request, '2FA is already enabled.')
        return redirect('dashboard')

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            code = otp_form.cleaned_data['otp_code']

            if CacheService.verify_otp(request.user.id, code):
                otp = EmailOTP.objects.filter(
                    user=request.user, code=code, purpose='2fa', is_used=False
                ).last()
                if otp and otp.is_valid():
                    otp.is_used = True
                    otp.save()
                    request.user.two_factor_enabled = True
                    request.user.save()
                    request.session['2fa_verified'] = True
                    messages.success(request, 'Two-factor authentication enabled successfully!')
                    return redirect('dashboard')

            messages.error(request, 'Invalid or expired code. A new code has been sent.')
            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(request.user.id, otp_code)
            send_otp_email(request.user, otp_code, request)
    else:
        otp_code = generate_otp()
        expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
        EmailOTP.objects.create(
            user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
        )
        CacheService.set_otp(request.user.id, otp_code)
        send_otp_email(request.user, otp_code, request)
        messages.info(request, 'A verification code has been sent to your email.')

    otp_form = OTPForm()
    return render(request, 'registration/setup_2fa.html', {
        'otp_form': otp_form,
        'user_email': request.user.email,
    })


@login_required
def verify_2fa_view(request):
    if not request.user.two_factor_enabled:
        return redirect('dashboard')

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            code = otp_form.cleaned_data['otp_code']

            if CacheService.verify_otp(request.user.id, code):
                otp = EmailOTP.objects.filter(
                    user=request.user, code=code, purpose='2fa', is_used=False
                ).last()
                if otp and otp.is_valid():
                    otp.is_used = True
                    otp.save()
                    request.session['2fa_verified'] = True
                    CacheService.reset_rate_limit(f'login_{request.user.id}')
                    messages.success(request, f'Welcome back, {request.user.email}!')
                    return redirect('dashboard')

            messages.error(request, 'Invalid or expired code. A new code has been sent.')
            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(request.user.id, otp_code)
            send_otp_email(request.user, otp_code, request)
    else:
        if not request.session.get('otp_sent'):
            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(request.user.id, otp_code)
            send_otp_email(request.user, otp_code, request)
            request.session['otp_sent'] = True
            messages.info(request, 'A verification code has been sent to your email.')

    otp_form = OTPForm()
    return render(request, 'registration/verify_2fa.html', {'otp_form': otp_form})


@login_required
def logout_view(request):
    logout(request)
    messages.success(request, 'You have been logged out.')
    return redirect('login')


@login_required
def dashboard_view(request):
    user = request.user
    open_trades = Trade.objects.filter(user=user, status='OPEN').count()
    recent_signals = TradingSignal.objects.filter(user=user).order_by('-created_at')[:3]
    win_trades = Trade.objects.filter(user=user, status='CLOSED', pnl__gt=0).count()
    closed_trades = Trade.objects.filter(user=user, status='CLOSED').count()
    win_rate = round((win_trades / closed_trades * 100) if closed_trades > 0 else 0, 1)

    context = {
        'user_email': user.email,
        'user_broker': user.broker,
        'balance': float(user.balance),
        'page_title': 'Dashboard',
        'open_trades': open_trades,
        'recent_signals': recent_signals,
        'win_rate': win_rate,
        'total_trades': closed_trades + Trade.objects.filter(user=user, status='OPEN').count(),
    }
    return render(request, 'dashboard/base.html', context)


@login_required
def profile_view(request):
    if request.method == 'POST':
        user = request.user
        email = request.POST.get('email', '').strip()
        broker = request.POST.get('broker', '').strip()
        if email:
            user.email = email
        if broker:
            user.broker = broker
        user.save()
        messages.success(request, 'Profile updated successfully.')
        return redirect('settings_profile')
    return render(request, 'dashboard/settings.html', {'page_title': 'Settings'})


@login_required
def toggle_2fa_view(request):
    user = request.user
    if user.two_factor_enabled:
        user.two_factor_enabled = False
        user.save()
        EmailOTP.objects.filter(user=user, purpose='2fa').delete()
        CacheService.delete_otp(user.id)
        messages.success(request, 'Two-factor authentication disabled.')
    else:
        return redirect('setup_2fa')
    return redirect('settings_profile')


@login_required
def resend_otp_view(request):
    if request.method == 'POST':
        if not CacheService.check_rate_limit(f'resend_otp_{request.user.id}', max_attempts=3, window=60):
            messages.error(request, 'Too many requests. Please wait 60 seconds.')
            return redirect('verify_2fa')

        otp_code = generate_otp()
        expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
        EmailOTP.objects.create(
            user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
        )
        CacheService.set_otp(request.user.id, otp_code)
        sent = send_otp_email(request.user, otp_code, request)
        if sent:
            messages.success(request, 'A new verification code has been sent to your email.')
        return redirect('verify_2fa')
    return redirect('dashboard')


@login_required
def llm_query_view(request):
    if request.method == 'POST':
        query_type = request.POST.get('query_type', 'market_analysis')
        prompt = request.POST.get('prompt', '')

        if not prompt:
            messages.error(request, 'Please enter a query.')
            return redirect('dashboard')

        context = {}
        if query_type == 'rag_query':
            docs = RAGDocument.objects.filter(user=request.user)
            all_chunks = []
            for doc in docs:
                chunks = RAGChunk.objects.filter(document=doc)
                for c in chunks:
                    all_chunks.append({'id': c.chunk_id, 'text': c.text})
            ranked = rag_engine.rank_chunks(prompt, all_chunks)
            context_texts = [c['text'] for c in ranked]
            context['rag_results'] = context_texts

        llm_query = LLMQuery.objects.create(
            user=request.user,
            query_type=query_type,
            prompt=prompt,
            context_used=context if context else None,
        )

        try:
            if query_type == 'market_analysis':
                result = llm_service.analyze_market(prompt)
            elif query_type == 'trading_idea':
                result = llm_service.generate_trading_idea(prompt)
            elif query_type == 'rag_query':
                result = llm_service.rag_query(prompt, context.get('rag_results', []))
            else:
                result = {'error': 'Unknown query type'}

            response_text = str(result) if isinstance(result, dict) else result
            llm_query.response = response_text
            llm_query.success = True
            llm_query.save()

            messages.success(request, 'Analysis complete!')
            return render(request, 'dashboard/llm_result.html', {
                'query': llm_query,
                'result': response_text,
                'page_title': 'AI Analysis Result',
            })

        except Exception as e:
            llm_query.response = f'Error: {str(e)}'
            llm_query.success = False
            llm_query.save()
            messages.error(request, f'Analysis failed: {str(e)}')
            return redirect('dashboard')

    return redirect('dashboard')


@login_required
def upload_document_view(request):
    if request.method == 'POST':
        title = request.POST.get('title', '')
        content = request.POST.get('content', '')
        source = request.POST.get('source', 'manual')

        if not title or not content:
            messages.error(request, 'Title and content are required.')
            return redirect('dashboard')

        if len(content) > 100000:
            messages.error(request, 'Document too large. Maximum 100KB.')
            return redirect('dashboard')

        doc = RAGDocument.objects.create(
            user=request.user,
            title=title,
            content=content,
            source=source,
            file_size=len(content.encode('utf-8')),
        )

        chunks = rag_engine.process_document(title=title, content=content, source=source)
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

        messages.success(request, f'Document "{title}" uploaded and indexed ({len(chunks)} chunks).')
        return redirect('dashboard')

    return redirect('dashboard')
