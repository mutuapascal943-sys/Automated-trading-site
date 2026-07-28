from functools import wraps
from django.shortcuts import redirect
from rest_framework.response import Response
from rest_framework import status


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
                return Response(
                    {'error': '2FA verification required'},
                    status=status.HTTP_403_FORBIDDEN,
                )
        return view_func(request, *args, **kwargs)
    return _wrapped_view


def broker_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and not request.user.broker_configured:
            return redirect('broker_setup')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
