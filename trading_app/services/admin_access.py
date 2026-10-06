from functools import wraps

from django.conf import settings
from django.contrib.auth import REDIRECT_FIELD_NAME
from django.contrib.auth.views import redirect_to_login
from django.http import JsonResponse
from rest_framework.permissions import BasePermission


ADMIN_EMAIL = 'manesapascal@gmail.com'


def is_authorized_admin(user):
    return bool(
        user.is_authenticated
        and user.is_active
        and user.is_staff
        and user.is_superuser
        and user.email.casefold() == ADMIN_EMAIL
    )


def has_verified_admin_session(request):
    return bool(
        is_authorized_admin(request.user)
        and request.session.get('2fa_verified')
        and request.session.get('admin_2fa_verified')
    )


def admin_required(view_func):
    @wraps(view_func)
    def wrapped(request, *args, **kwargs):
        if not request.user.is_authenticated:
            return redirect_to_login(request.get_full_path(), settings.LOGIN_URL, REDIRECT_FIELD_NAME)
        if not has_verified_admin_session(request):
            if request.path.startswith('/api/'):
                return JsonResponse({'error': 'Administrator authorization required.'}, status=403)
            return JsonResponse({'error': 'Administrator authorization required.'}, status=403)
        return view_func(request, *args, **kwargs)
    return wrapped


class AuthorizedAdminPermission(BasePermission):
    def has_permission(self, request, view):
        return has_verified_admin_session(request)