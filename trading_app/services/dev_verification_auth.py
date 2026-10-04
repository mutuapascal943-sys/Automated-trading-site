from __future__ import annotations

import uuid
from urllib.parse import urlsplit

from django.conf import settings
from django.contrib.auth import login, logout
from django.db import IntegrityError, transaction
from django.http import JsonResponse
from django.middleware.csrf import get_token
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods, require_POST

from trading_app.models import User

DEV_USERNAME_PREFIX = 'dev-analysis-verification-'
DEV_EMAIL_DOMAIN = 'dev-verification.invalid'


def is_dev_verification_user(user) -> bool:
    return bool(
        user
        and getattr(user, 'is_authenticated', False)
        and user.get_username().startswith(DEV_USERNAME_PREFIX)
        and user.email.endswith(f'@{DEV_EMAIL_DOMAIN}')
    )


def _local_request(request) -> bool:
    host = urlsplit(f'//{request.get_host()}').hostname
    remote = request.META.get('REMOTE_ADDR', '')
    return host in {'127.0.0.1', 'localhost', '::1'} and remote in {'127.0.0.1', '::1'}


def _feature_enabled() -> bool:
    return settings.DEBUG and settings.TRADING_ANALYSIS_VERIFICATION_ENABLED


@csrf_protect
@require_http_methods(['GET', 'POST'])
def create_dev_verification_session(request):
    """Create a temporary loopback-only session for read-only verification."""
    if not _feature_enabled() or not _local_request(request):
        return JsonResponse({'error': 'Development verification authentication is unavailable.'}, status=404)
    if request.method == 'GET':
        return JsonResponse({
            'status': 'development_verification_auth_available',
            'csrf_token': get_token(request),
            'issue_session_method': 'POST',
            'analysis_endpoint': '/api/internal/analysis-verification/',
            'cleanup_endpoint': '/api/internal/dev/verification-session/cleanup/',
            'session_expires_in_seconds': 600,
        })

    username = f'{DEV_USERNAME_PREFIX}{uuid.uuid4().hex}'
    email = f'{username}@{DEV_EMAIL_DOMAIN}'
    user = User(
        username=username,
        email=email,
        is_staff=True,
        is_superuser=False,
        is_active=True,
        selected_market='Volatility 75 Index',
    )
    user.set_unusable_password()
    try:
        with transaction.atomic():
            user.save(force_insert=True)
    except IntegrityError:
        return JsonResponse({'error': 'Temporary verification identity collision; retry the request.'}, status=409)
    login(request, user, backend='django.contrib.auth.backends.ModelBackend')
    request.session['dev_verification_user_id'] = user.pk
    request.session.set_expiry(600)
    return JsonResponse({
        'status': 'development_verification_session_created',
        'csrf_token': get_token(request),
        'expires_in_seconds': 600,
        'analysis_endpoint': '/api/internal/analysis-verification/',
        'cleanup_endpoint': '/api/internal/dev/verification-session/cleanup/',
        'instructions': 'POST persist=false analysis to the analysis endpoint, then POST cleanup.',
    }, status=201)


@require_POST
@csrf_protect
def cleanup_dev_verification_session(request):
    if not _feature_enabled() or not _local_request(request):
        return JsonResponse({'error': 'Development verification authentication is unavailable.'}, status=404)

    user_id = request.session.get('dev_verification_user_id')
    if user_id is None:
        return JsonResponse({'error': 'No development verification session is active.'}, status=400)
    try:
        user = User.objects.get(pk=user_id)
    except User.DoesNotExist:
        user = None
    if user is not None and is_dev_verification_user(user):
        user.delete()
    logout(request)
    return JsonResponse({'status': 'development_verification_session_cleaned_up'})
