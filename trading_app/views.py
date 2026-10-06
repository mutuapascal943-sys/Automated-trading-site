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
from .services.email_service import send_otp_email
from .services.otp_service import OTPResult, can_request_otp, consume_otp, invalidate_otp, issue_otp
from .services.cache_service import CacheService
from .services.admin_access import is_authorized_admin

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
            base_username = user.email.split('@')[0]
            username = base_username
            counter = 1
            while User.objects.filter(username=username).exists():
                username = f'{base_username}{counter}'
                counter += 1
            user.username = username
            user.trial_started_at = timezone.now()
            user.save()
            request.session['registration_user_id'] = user.id
            if _issue_email_otp(request, user, 'email_verify', scope='issue'):
                messages.success(request, 'Account created. Check your email for the verification code.')
            else:
                messages.error(request, 'Your account was created, but email verification is not complete. Resend a code when delivery is available.')
            return redirect('setup_2fa')
    return render(request, 'registration/register.html', {'form': form})


def _login_rate_key(request) -> str:
    email = request.POST.get('username', 'unknown')
    ip = request.META.get('REMOTE_ADDR', 'unknown')
    return f'login_{ip}_{email}'


def _issue_email_otp(request, user, purpose: str, scope: str = 'issue') -> bool:
    if not can_request_otp(user.id, purpose, scope=scope):
        messages.error(request, 'Too many verification code requests. Please wait before trying again.')
        return False
    otp, raw_code = issue_otp(user, purpose)
    if not send_otp_email(user, raw_code, purpose=purpose):
        invalidate_otp(otp)
        messages.error(request, 'Email delivery failed. Please try again later.')
        return False
    messages.info(request, 'A verification code has been sent to your email.')
    return True


def _otp_error_message(result: OTPResult) -> str:
    return {
        OTPResult.INVALID: 'Incorrect verification code. Please try again.',
        OTPResult.EXPIRED: 'Verification code expired. Request a new code.',
        OTPResult.RATE_LIMITED: 'Too many incorrect attempts. Request a new code later.',
        OTPResult.USED: 'This verification code has already been used. Request a new code.',
    }.get(result, 'Verification failed. Please request a new code.')


def _complete_login(request, user, remember_me: bool = False, rate_key: str | None = None):
    login(request, user)
    request.session['2fa_verified'] = True
    if is_authorized_admin(user):
        request.session['admin_2fa_verified'] = True
    else:
        request.session.pop('admin_2fa_verified', None)
    for key in ('pending_2fa_user_id', 'pending_2fa_remember_me', 'otp_sent'):
        request.session.pop(key, None)
    if rate_key:
        CacheService.reset_rate_limit(rate_key)
    create_notification(user, 'New Login', 'New sign-in to your account from a web browser.', 'account')
    admin_return_to = request.session.pop('admin_return_to', '')
    if is_authorized_admin(user) and admin_return_to.startswith('/admin/') and not remember_me:
        return redirect(admin_return_to)
    if not remember_me:
        return redirect('dashboard')
    token = secrets.token_hex(32)
    RememberMeToken.objects.create(user=user, token=token, expires_at=timezone.now() + timedelta(days=30))
    response = HttpResponseRedirect(reverse('dashboard'))
    response.set_signed_cookie('remember_me', token, max_age=30 * 24 * 3600,
                               secure=not settings.DEBUG, httponly=True, samesite='Lax')
    return response


def login_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')
    form = LoginForm()
    if request.method == 'POST':
        rate_key = _login_rate_key(request)
        if not CacheService.check_rate_limit(rate_key, max_attempts=settings.MAX_LOGIN_ATTEMPTS, window=300):
            messages.error(request, 'Too many login attempts. Please try again in 5 minutes.')
            return render(request, 'registration/login.html', {'form': form})
        form = LoginForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            remember_me = request.POST.get('remember_me') == 'on'
            next_path = request.POST.get('next', request.GET.get('next', ''))
            if next_path.startswith('/admin/'):
                request.session['admin_return_to'] = next_path
            if user.two_factor_enabled or is_authorized_admin(user):
                request.session['pending_2fa_user_id'] = user.id
                request.session['pending_2fa_remember_me'] = remember_me
                if not _issue_email_otp(request, user, '2fa', scope='issue'):
                    form.add_error(None, 'We could not send your verification code. Please use resend after a short wait.')
                    request.session.pop('pending_2fa_user_id', None)
                    request.session.pop('pending_2fa_remember_me', None)
                    return render(request, 'registration/login.html', {'form': form})
                return redirect('verify_2fa')
            messages.success(request, f'Welcome back, {user.email}!')
            return _complete_login(request, user, remember_me=remember_me, rate_key=rate_key)
    return render(request, 'registration/login.html', {'form': form})


def setup_2fa_view(request):
    pending_registration_id = request.session.get('registration_user_id')
    if pending_registration_id and not request.user.is_authenticated:
        try:
            verification_user = User.objects.get(pk=pending_registration_id)
        except User.DoesNotExist:
            request.session.pop('registration_user_id', None)
            messages.error(request, 'Registration verification expired. Please register again.')
            return redirect('register')
    elif request.user.is_authenticated:
        verification_user = request.user
    else:
        return redirect('login')

    if verification_user.two_factor_enabled:
        messages.info(request, '2FA is already enabled.')
        return redirect('dashboard')
    active_purpose = 'email_verify' if pending_registration_id else '2fa'
    otp_form = OTPForm()

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            result = consume_otp(verification_user, active_purpose, otp_form.cleaned_data['otp_code'])
            if result == OTPResult.VERIFIED:
                if pending_registration_id:
                    verification_user.two_factor_enabled = True
                    verification_user.email_verified = True
                    verification_user.save(update_fields=['two_factor_enabled', 'email_verified'])
                    request.session.pop('registration_user_id', None)
                    create_notification(verification_user, 'Account Created', 'Email verified successfully.', 'account')
                    return _complete_login(request, verification_user)
                verification_user.two_factor_enabled = True
                verification_user.save(update_fields=['two_factor_enabled'])
                request.session['2fa_verified'] = True
                create_notification(verification_user, 'Two-Factor Authentication', 'Email two-factor authentication enabled.', 'account')
                messages.success(request, 'Two-factor authentication enabled successfully.')
                return redirect('dashboard')
            otp_form.add_error('otp_code', _otp_error_message(result))
    else:
        if not EmailOTP.objects.filter(user=verification_user, purpose=active_purpose, is_used=False, expires_at__gt=timezone.now()).exists():
            _issue_email_otp(request, verification_user, active_purpose, scope='issue')

    return render(request, 'registration/setup_2fa.html', {
        'otp_form': otp_form,
        'user_email': verification_user.email,
        'purpose_label': 'email verification' if pending_registration_id else '2FA setup',
        'pending_registration': bool(pending_registration_id),
    })


def verify_2fa_view(request):
    pending_user_id = request.session.get('pending_2fa_user_id')
    pending_login = not request.user.is_authenticated
    if pending_login:
        try:
            user = User.objects.get(pk=pending_user_id)
        except User.DoesNotExist:
            return redirect('login')
    else:
        user = request.user
    if not user.two_factor_enabled and not is_authorized_admin(user):
        return redirect('dashboard')
    otp_form = OTPForm()

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            result = consume_otp(user, '2fa', otp_form.cleaned_data['otp_code'])
            if result == OTPResult.VERIFIED:
                remember_me = request.session.get('pending_2fa_remember_me', False)
                return _complete_login(request, user, remember_me=remember_me)
            otp_form.add_error('otp_code', _otp_error_message(result))
    else:
        if pending_user_id and not EmailOTP.objects.filter(user=user, purpose='2fa', is_used=False, expires_at__gt=timezone.now()).exists():
            _issue_email_otp(request, user, '2fa', scope='resend')

    return render(request, 'registration/verify_2fa.html', {
        'otp_form': otp_form,
        'user_email': user.email,
        'pending_login': pending_login,
    })


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
            if user.two_factor_enabled or is_authorized_admin(user):
                request.session['pending_2fa_user_id'] = user.id
                request.session['pending_2fa_remember_me'] = True
                if not EmailOTP.objects.filter(user=user, purpose='2fa', is_used=False, expires_at__gt=timezone.now()).exists():
                    _issue_email_otp(request, user, '2fa', scope='resend')
                messages.info(request, 'Please verify your identity with 2FA.')
                return redirect('verify_2fa')
            login(request, user)
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
        'user_broker': 'Market analysis',
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


def resend_otp_view(request):
    if request.method != 'POST':
        return redirect('login')
    user_id = request.session.get('pending_2fa_user_id') or request.session.get('registration_user_id')
    if user_id:
        user = User.objects.filter(pk=user_id).first()
        purpose = 'email_verify' if request.session.get('registration_user_id') else '2fa'
    elif request.user.is_authenticated and request.user.two_factor_enabled and not request.session.get('2fa_verified'):
        user = request.user
        purpose = '2fa'
    else:
        return redirect('login')
    if user:
        _issue_email_otp(request, user, purpose, scope='resend')
    return redirect('setup_2fa' if purpose == 'email_verify' else 'verify_2fa')


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
                return redirect('password_reset_request')

            request.session['reset_user_id'] = user.id
            if not _issue_email_otp(request, user, 'password_reset', scope='issue'):
                messages.error(request, 'We could not deliver the recovery code. Please try again later.')
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
            result = consume_otp(user, 'password_reset', form.cleaned_data['otp_code'])
            if result == OTPResult.VERIFIED:
                request.session['reset_verified'] = True
                request.session['reset_verified_at'] = timezone.now().timestamp()
                messages.success(request, 'Code verified. You can now set a new password.')
                return redirect('password_reset_confirm')
            form.add_error('otp_code', _otp_error_message(result))

    return render(request, 'registration/password_reset_verify.html', {'form': form, 'user_email': user.email})


def password_reset_confirm_view(request):
    if request.user.is_authenticated:
        return redirect('dashboard')

    user_id = request.session.get('reset_user_id')
    verified_at = request.session.get('reset_verified_at', 0)
    if not user_id or not request.session.get('reset_verified') or timezone.now().timestamp() - verified_at > 300:
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
            user.remember_me_tokens.all().delete()
            del request.session['reset_user_id']
            del request.session['reset_verified']
            request.session.pop('reset_verified_at', None)

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

    if not _issue_email_otp(request, user, 'password_reset', scope='resend'):
        messages.error(request, 'A new recovery code could not be delivered. Please wait before retrying.')
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
