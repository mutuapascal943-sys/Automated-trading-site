import hashlib
import json
import logging
import secrets
from datetime import timedelta
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.conf import settings
from django.utils import timezone
from django.http import HttpResponseRedirect
from .forms import (
    RegisterForm, LoginForm, OTPForm,
    PasswordResetRequestForm, PasswordResetVerifyForm,
    SetNewPasswordForm, SecurityQuestionForm, SecurityAnswerForm,
    ProfileForm, ProfilePictureForm,
)
from .models import User, EmailOTP, Trade, TradingSignal, SecurityQuestion, RememberMeToken, Notification
from .decorators import two_factor_required
from .services.email_service import generate_otp, send_otp_email
from .services.cache_service import CacheService
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
            base_username = user.email.split('@')[0]
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f'{base_username}{counter}'
                counter += 1
            user.username = username
            user.save()
            create_notification(user, 'Account Created', 'Welcome to Automated Trading Signal Application! Please verify your email to get started.', 'account')
            login(request, user)

            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(user.id, otp_code)
            send_otp_email(user, otp_code, request, purpose='email_verify')

            messages.success(request, 'Account created! Check your email for the verification code.')
            return redirect('setup_2fa')
    return render(request, 'registration/register.html', {'form': form})


def _login_rate_key(request) -> str:
    """Rate-limit key based on IP + submitted email to prevent global lockout."""
    email = request.POST.get('username', 'unknown')
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    return f'login_{ip}_{email}'


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm()
    if request.method == 'POST':
        # Rate limit applies to ALL login attempts (even wrong usernames)
        # to prevent credential stuffing at the network level.
        rl_key = _login_rate_key(request)
        if not CacheService.check_rate_limit(
            rl_key,
            max_attempts=settings.MAX_LOGIN_ATTEMPTS,
            window=300,
        ):
            messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
            return render(request, 'registration/login.html', {'form': form})

        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()

            remember_me = request.POST.get('remember_me') == 'on'
            login(request, user)

            if remember_me:
                token = secrets.token_hex(32)
                RememberMeToken.objects.create(
                    user=user,
                    token=token,
                    expires_at=timezone.now() + timedelta(days=30),
                )
                response = HttpResponseRedirect(
                    reverse('verify_2fa') if user.two_factor_enabled else reverse('dashboard')
                )
                response.set_signed_cookie(
                    'remember_me', token,
                    max_age=30 * 24 * 3600,
                    secure=not settings.DEBUG,
                    httponly=True,
                    samesite='Lax',
                )
                if not user.two_factor_enabled:
                    CacheService.reset_rate_limit(rl_key)
                    messages.success(request, f'Welcome back, {user.email}!')
                return response

            if user.two_factor_enabled:
                otp_code = generate_otp()
                expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
                EmailOTP.objects.create(
                    user=user, code=otp_code, purpose='2fa', expires_at=expires_at
                )
                CacheService.set_otp(user.id, otp_code)
                send_otp_email(user, otp_code, request, purpose='2fa')
                messages.info(request, 'A verification code has been sent to your email.')
                return redirect('verify_2fa')

            CacheService.reset_rate_limit(rl_key)
            create_notification(user, 'New Login', f'New sign-in to your account from a web browser.', 'account')
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
            send_otp_email(request.user, otp_code, request, purpose='2fa')
    else:
        otp_code = generate_otp()
        expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
        EmailOTP.objects.create(
            user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
        )
        CacheService.set_otp(request.user.id, otp_code)
        send_otp_email(request.user, otp_code, request, purpose='2fa')
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
            send_otp_email(request.user, otp_code, request, purpose='2fa')
    else:
        if not request.session.get('otp_sent'):
            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=request.user, code=otp_code, purpose='2fa', expires_at=expires_at
            )
            CacheService.set_otp(request.user.id, otp_code)
            send_otp_email(request.user, otp_code, request, purpose='2fa')
            request.session['otp_sent'] = True
            messages.info(request, 'A verification code has been sent to your email.')

    otp_form = OTPForm()
    return render(request, 'registration/verify_2fa.html', {'otp_form': otp_form})


@login_required
def logout_view(request):
    token = request.get_signed_cookie('remember_me', default=None)
    if token:
        RememberMeToken.objects.filter(token=token).delete()
    response = HttpResponseRedirect(reverse('login'))
    response.delete_cookie('remember_me')
    logout(request)
    messages.success(request, 'You have been logged out.')
    return response


def create_notification(user, title, message='', notification_type='system'):
    Notification.create_notification(
        user=user, title=title, message=message, notification_type=notification_type
    )


def auto_login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    token = request.get_signed_cookie('remember_me', default=None)
    if token:
        try:
            rm_token = RememberMeToken.objects.get(token=token, expires_at__gt=timezone.now())
            user = rm_token.user
            login(request, user)
            if user.two_factor_enabled and not request.session.get('2fa_verified'):
                messages.info(request, 'Please verify your identity with 2FA.')
                return redirect('verify_2fa')
            messages.success(request, f'Welcome back, {user.email}!')
            return redirect('dashboard')
        except RememberMeToken.DoesNotExist:
            response = redirect('login')
            response.delete_cookie('remember_me')
            return response
    return redirect('login')


@login_required
def dashboard_view(request):
    user = request.user
    open_trades = Trade.objects.filter(user=user, status='OPEN').count()
    recent_signals = TradingSignal.objects.filter(user=user).order_by('-created_at')[:3]
    win_trades = Trade.objects.filter(user=user, status='CLOSED', pnl__gt=0).count()
    closed_trades = Trade.objects.filter(user=user, status='CLOSED').count()
    win_rate = round((win_trades / closed_trades * 100) if closed_trades > 0 else 0, 1)

    show_welcome = not request.session.get('welcome_dismissed', False)
    if show_welcome:
        request.session['welcome_dismissed'] = True
        create_notification(
            user, 'Welcome to Automated Trading Signal Application!',
            'Your AI-powered trading assistant is ready. Start exploring the dashboard to access market analysis, signals, and more.',
            'system'
        )

    unread_notifications = Notification.objects.filter(user=user, is_read=False).count()

    context = {
        'user_email': user.email,
        'user_broker': user.broker,
        'balance': float(user.balance),
        'page_title': 'Dashboard',
        'open_trades': open_trades,
        'recent_signals': recent_signals,
        'win_rate': win_rate,
        'total_trades': closed_trades + Trade.objects.filter(user=user, status='OPEN').count(),
        'show_welcome': show_welcome,
        'unread_notifications': unread_notifications,
    }
    return render(request, 'dashboard/base.html', context)


@login_required
@two_factor_required
def profile_view(request):
    user = request.user
    form = ProfileForm(instance=user)
    pic_form = ProfilePictureForm(instance=user)

    if request.method == 'POST':
        if 'remove_avatar' in request.POST:
            if user.avatar:
                user.avatar.delete()
                user.avatar = None
                user.save()
                messages.success(request, 'Profile picture removed.')
            return redirect('settings_profile')
        if 'update_profile' in request.POST:
            form = ProfileForm(request.POST, instance=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Profile updated successfully.')
                return redirect('settings_profile')
        elif 'update_avatar' in request.POST:
            pic_form = ProfilePictureForm(request.POST, request.FILES, instance=user)
            if pic_form.is_valid():
                pic_form.save()
                messages.success(request, 'Profile picture updated successfully.')
                return redirect('settings_profile')

    return render(request, 'dashboard/settings_panel.html', {
        'profile_form': form,
        'pic_form': pic_form,
        'page_title': 'Settings',
    })


@login_required
@two_factor_required
def profile_page_view(request):
    user = request.user
    form = ProfileForm(instance=user)
    pic_form = ProfilePictureForm(instance=user)

    if request.method == 'POST':
        if 'remove_avatar' in request.POST:
            if user.avatar:
                user.avatar.delete()
                user.avatar = None
                user.save()
                messages.success(request, 'Profile picture removed.')
            return redirect('profile_page')
        if 'update_profile' in request.POST:
            form = ProfileForm(request.POST, instance=user)
            if form.is_valid():
                form.save()
                messages.success(request, 'Profile updated successfully.')
                return redirect('profile_page')
        elif 'update_avatar' in request.POST:
            pic_form = ProfilePictureForm(request.POST, request.FILES, instance=user)
            if pic_form.is_valid():
                pic_form.save()
                messages.success(request, 'Profile picture updated successfully.')
                return redirect('profile_page')

    return render(request, 'registration/profile.html', {
        'profile_form': form,
        'pic_form': pic_form,
        'page_title': 'My Profile',
    })


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
        sent = send_otp_email(request.user, otp_code, request, purpose='2fa')
        if sent:
            messages.success(request, 'A new verification code has been sent to your email.')
        return redirect('verify_2fa')
    return redirect('dashboard')


def password_reset_request_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    form = PasswordResetRequestForm()
    if request.method == 'POST':
        form = PasswordResetRequestForm(request.POST)
        if form.is_valid():
            email = form.cleaned_data['email']
            try:
                user = User.objects.get(email__iexact=email)
            except User.DoesNotExist:
                messages.success(request, 'If an account with that email exists, a verification code has been sent.')
                return redirect('password_reset_verify')

            if not CacheService.check_rate_limit(f'pwd_reset_{user.id}', max_attempts=3, window=300):
                messages.success(request, 'If an account with that email exists, a verification code has been sent.')
                return redirect('password_reset_verify')

            otp_code = generate_otp()
            expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
            EmailOTP.objects.create(
                user=user, code=otp_code, purpose='password_reset', expires_at=expires_at
            )
            CacheService.set_otp(user.id, otp_code)
            send_otp_email(user, otp_code, request, purpose='password_reset')

            request.session['reset_user_id'] = user.id
            messages.success(request, 'A verification code has been sent to your email.')
            return redirect('password_reset_verify')

    return render(request, 'registration/password_reset_request.html', {'form': form})


def password_reset_verify_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    user_id = request.session.get('reset_user_id')
    if not user_id:
        messages.error(request, 'Please start the password reset process again.')
        return redirect('password_reset_request')

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, 'User not found. Please start again.')
        return redirect('password_reset_request')

    form = PasswordResetVerifyForm()
    if request.method == 'POST':
        form = PasswordResetVerifyForm(request.POST)
        if form.is_valid():
            code = form.cleaned_data['otp_code']

            if CacheService.verify_otp(user.id, code):
                otp = EmailOTP.objects.filter(
                    user=user, code=code, purpose='password_reset', is_used=False
                ).last()
                if otp and otp.is_valid():
                    otp.is_used = True
                    otp.save()
                    request.session['reset_verified'] = True
                    CacheService.reset_rate_limit(f'pwd_reset_{user.id}')
                    messages.success(request, 'Code verified. You can now set a new password.')
                    return redirect('password_reset_confirm')

            messages.error(request, 'Invalid or expired code. Please request a new one.')

    return render(request, 'registration/password_reset_verify.html', {'form': form, 'user_email': user.email})


def password_reset_confirm_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    user_id = request.session.get('reset_user_id')
    if not user_id or not request.session.get('reset_verified'):
        messages.error(request, 'Please start the password reset process again.')
        return redirect('password_reset_request')

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        messages.error(request, 'User not found. Please start again.')
        return redirect('password_reset_request')

    form = SetNewPasswordForm(user)
    if request.method == 'POST':
        form = SetNewPasswordForm(user, request.POST)
        if form.is_valid():
            form.save()
            del request.session['reset_user_id']
            del request.session['reset_verified']

            # Invalidate all existing sessions for this user
            from django.contrib.sessions.models import Session
            sessions = Session.objects.filter(expire_date__gte=timezone.now())
            for session in sessions:
                data = session.get_decoded()
                if str(user.id) == str(data.get('_auth_user_id')):
                    session.delete()

            messages.success(request, 'Password reset successful! You can now log in.')
            return redirect('login')

    return render(request, 'registration/password_reset_confirm.html', {'form': form})


def resend_reset_otp_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    user_id = request.session.get('reset_user_id')
    if not user_id:
        return redirect('password_reset_request')

    try:
        user = User.objects.get(id=user_id)
    except User.DoesNotExist:
        return redirect('password_reset_request')

    if not CacheService.check_rate_limit(f'resend_reset_otp_{user.id}', max_attempts=3, window=60):
        messages.error(request, 'Too many requests. Please wait 60 seconds.')
        return redirect('password_reset_verify')

    otp_code = generate_otp()
    expires_at = timezone.now() + timezone.timedelta(seconds=settings.OTP_EXPIRY_SECONDS)
    EmailOTP.objects.create(
        user=user, code=otp_code, purpose='password_reset', expires_at=expires_at
    )
    CacheService.set_otp(user.id, otp_code)
    send_otp_email(user, otp_code, request, purpose='password_reset')
    messages.success(request, 'A new verification code has been sent to your email.')
    return redirect('password_reset_verify')


@login_required
@two_factor_required
def setup_security_questions_view(request):
    questions = SecurityQuestion.objects.filter(user=request.user)
    existing = {q.question_key for q in questions}

    form = SecurityQuestionForm()
    if request.method == 'POST':
        form = SecurityQuestionForm(request.POST)
        if form.is_valid():
            question_key = form.cleaned_data['question_key']
            answer = form.cleaned_data['answer'].lower().strip()
            answer_hash = hashlib.sha256(answer.encode('utf-8')).hexdigest()

            SecurityQuestion.objects.update_or_create(
                user=request.user,
                question_key=question_key,
                defaults={'answer_hash': answer_hash},
            )
            messages.success(request, 'Security question saved successfully.')
            return redirect('setup_security_questions')

    used_questions = SecurityQuestion.objects.filter(user=request.user)
    return render(request, 'registration/setup_security_questions.html', {
        'form': form,
        'used_questions': used_questions,
        'existing': existing,
    })


@login_required
@two_factor_required
def delete_security_question_view(request, question_key):
    SecurityQuestion.objects.filter(user=request.user, question_key=question_key).delete()
    messages.success(request, 'Security question removed.')
    return redirect('setup_security_questions')
