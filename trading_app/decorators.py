from functools import wraps
from django.shortcuts import redirect


def two_factor_required(view_func):
    @wraps(view_func)
    def _wrapped_view(request, *args, **kwargs):
        if request.user.is_authenticated and request.user.two_factor_enabled:
            if not request.session.get('2fa_verified'):
                return redirect('verify_2fa')
        return view_func(request, *args, **kwargs)
    return _wrapped_view
