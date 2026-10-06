from django.contrib.admin import AdminSite
from django.contrib.auth import logout
from django.http import HttpResponseForbidden
from django.shortcuts import redirect
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.views.decorators.cache import never_cache
from django.views.decorators.csrf import csrf_protect

from trading_app.services.admin_access import has_verified_admin_session, is_authorized_admin


class OwnerAdminSite(AdminSite):
    site_header = 'ATS Owner Administration'
    site_title = 'ATS Administration'
    index_title = 'System management'

    def has_permission(self, request):
        return has_verified_admin_session(request)

    def admin_view(self, view, cacheable=False):
        def wrapped(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect(f"{reverse('login')}?next={request.get_full_path()}")
            if not has_verified_admin_session(request):
                return HttpResponseForbidden('Administrator access is restricted.')
            return view(request, *args, **kwargs)

        wrapped = csrf_protect(wrapped)
        return wrapped if cacheable else never_cache(wrapped)

    def login(self, request, extra_context=None):
        if has_verified_admin_session(request):
            next_path = request.GET.get('next', '')
            if next_path.startswith('/admin/') and not next_path.startswith('//'):
                return redirect(next_path)
            return redirect(reverse('owner_admin:owner_dashboard'))
        if request.user.is_authenticated and is_authorized_admin(request.user):
            logout(request)
        if request.user.is_authenticated:
            return HttpResponseForbidden('Administrator access is restricted.')
        if request.method in ('GET', 'POST'):
            return redirect(f"{reverse('login')}?next={request.path}")
        return HttpResponseForbidden('Administrator access is restricted.')

    def get_urls(self):
        from trading_app.admin_views import owner_dashboard_view

        return [
            path('overview/', self.admin_view(owner_dashboard_view), name='owner_dashboard'),
        ] + super().get_urls()


owner_admin_site = OwnerAdminSite(name='owner_admin')