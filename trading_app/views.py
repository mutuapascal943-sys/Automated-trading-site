import io
import base64
import pyotp
import qrcode
from django.shortcuts import render, redirect, get_object_or_404
from django.contrib.auth import login, logout, authenticate, update_session_auth_hash
from django.contrib.auth.decorators import login_required
from django.contrib import messages
from django.urls import reverse
from django.conf import settings
from django_otp import user_has_device, devices_for_user
from django_otp.plugins.otp_totp.models import TOTPDevice
from .forms import RegisterForm, LoginForm, OTPForm
from .models import User
from .decorators import two_factor_required


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
            device = TOTPDevice.objects.create(
                user=user,
                name='default',
                confirmed=False,
            )
            login(request, user)
            messages.success(request, 'Congratulations! Your account has been created successfully.')
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
            login(request, user)
            if user.two_factor_enabled:
                return redirect('verify_2fa')
            messages.success(request, f'Welcome back, {user.email}!')
            return redirect('dashboard')
    return render(request, 'registration/login.html', {'form': form})


@login_required
def setup_2fa_view(request):
    device = TOTPDevice.objects.filter(user=request.user, confirmed=False).first()
    if not device:
        device = TOTPDevice.objects.create(
            user=request.user, name='default', confirmed=False
        )
    totp = pyotp.TOTP(device.key)
    provisioning_uri = totp.provisioning_uri(
        name=request.user.email,
        issuer_name='Forex AI Pro'
    )
    qr = qrcode.make(provisioning_uri)
    buf = io.BytesIO()
    qr.save(buf, format='PNG')
    qr_b64 = base64.b64encode(buf.getvalue()).decode()

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            code = otp_form.cleaned_data['otp_code']
            if device.verify_token(code):
                device.confirmed = True
                device.save()
                request.user.two_factor_enabled = True
                request.user.save()
                messages.success(request, 'Two-factor authentication enabled successfully!')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid code. Please try again.')
    else:
        otp_form = OTPForm()

    return render(request, 'registration/setup_2fa.html', {
        'qr_b64': qr_b64,
        'otp_form': otp_form,
        'secret_key': device.key,
    })


@login_required
def verify_2fa_view(request):
    if not request.user.two_factor_enabled:
        return redirect('dashboard')
    device = TOTPDevice.objects.filter(user=request.user, confirmed=True).first()
    if not device:
        return redirect('setup_2fa')

    if request.method == 'POST':
        otp_form = OTPForm(request.POST)
        if otp_form.is_valid():
            code = otp_form.cleaned_data['otp_code']
            if device.verify_token(code):
                request.session['2fa_verified'] = True
                messages.success(request, f'Welcome back, {request.user.email}!')
                return redirect('dashboard')
            else:
                messages.error(request, 'Invalid authentication code.')
    else:
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
    context = {
        'user_email': user.email,
        'user_broker': user.broker,
        'balance': float(user.balance),
        'page_title': 'Dashboard',
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
        TOTPDevice.objects.filter(user=user).delete()
        user.two_factor_enabled = False
        user.save()
        messages.success(request, 'Two-factor authentication disabled.')
    else:
        return redirect('setup_2fa')
    return redirect('settings_profile')
