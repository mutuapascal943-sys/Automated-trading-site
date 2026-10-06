from functools import wraps
from django.shortcuts import redirect
from django.http import JsonResponse


def two_factor_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.two_factor_enabled:
            if not request.session.get('2fa_verified'):
                return redirect('verify_2fa')
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def two_factor_required_api(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.two_factor_enabled:
            if not request.session.get('2fa_verified'):
                return JsonResponse({'error': '2FA verification required'}, status=403)
        return view_func(request, *args, **kwargs)
    return _wrapped_view


class TwoFactorSessionMiddleware:
    """Require completed email 2FA for authenticated browser/API sessions."""

    EXEMPT_PATHS = {
        '/login/', '/logout/', '/register/', '/setup-2fa/', '/verify-2fa/',
        '/resend-otp/',
        '/auto-login/', '/password-reset/', '/password-reset/verify/',
        '/password-reset/confirm/', '/password-reset/resend-otp/',
        '/api/auth/request-otp/', '/api/auth/verify-otp/',
        '/api/auth/password-reset/', '/api/auth/password-reset/verify/',
        '/api/auth/password-reset/confirm/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        user = getattr(request, 'user', None)
        from trading_app.services.admin_access import is_authorized_admin

        if request.session.get('pending_2fa_user_id') and not request.session.get('2fa_verified'):
            if request.path not in self.EXEMPT_PATHS:
                if request.path.startswith('/api/'):
                    return JsonResponse({'error': '2FA verification required', 'status': 'verification_required'}, status=403)
                return redirect('verify_2fa')
        if (
            user is not None
            and user.is_authenticated
            and (user.two_factor_enabled or is_authorized_admin(user))
            and not request.session.get('2fa_verified')
            and request.path not in self.EXEMPT_PATHS
        ):
            if request.path.startswith('/api/'):
                return JsonResponse({'error': '2FA verification required', 'status': 'verification_required'}, status=403)
            return redirect('verify_2fa')
        return self.get_response(request)
