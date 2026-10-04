from django.conf import settings
from django.http import JsonResponse
from urllib.parse import urlsplit

from trading_app.services.dev_verification_auth import is_dev_verification_user


class DevVerificationSessionScopeMiddleware:
    """Limit temporary verification sessions to local verification/cleanup APIs."""

    ALLOWED_PATHS = {
        '/api/internal/analysis-verification/',
        '/api/internal/dev/verification-session/cleanup/',
    }

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not is_dev_verification_user(getattr(request, 'user', None)):
            return self.get_response(request)
        if not self._enabled_for_request(request):
            return JsonResponse({'error': 'Development verification session is disabled.'}, status=404)
        if request.path not in self.ALLOWED_PATHS:
            return JsonResponse({'error': 'Development verification session is restricted to verification APIs.'}, status=403)
        return self.get_response(request)

    @staticmethod
    def _enabled_for_request(request):
        host = urlsplit(f'//{request.get_host()}').hostname
        return (
            settings.DEBUG
            and settings.TRADING_ANALYSIS_VERIFICATION_ENABLED
            and host in {'127.0.0.1', 'localhost', '::1'}
            and request.META.get('REMOTE_ADDR') in {'127.0.0.1', '::1'}
        )
