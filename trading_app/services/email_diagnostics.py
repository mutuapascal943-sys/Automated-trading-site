from django.conf import settings
from django.core.mail import get_connection


def email_configuration_status() -> dict:
    """Return non-secret email configuration status for operators and tests."""
    smtp_selected = settings.EMAIL_BACKEND == 'django.core.mail.backends.smtp.EmailBackend'
    return {
        'backend': settings.EMAIL_BACKEND,
        'smtp_selected': smtp_selected,
        'host_configured': bool(settings.EMAIL_HOST),
        'port_configured': bool(settings.EMAIL_PORT),
        'tls_enabled': bool(settings.EMAIL_USE_TLS),
        'username_configured': bool(settings.EMAIL_HOST_USER),
        'password_configured': bool(settings.EMAIL_HOST_PASSWORD),
        'from_configured': bool(settings.DEFAULT_FROM_EMAIL),
        'ready': bool(
            smtp_selected and settings.EMAIL_HOST and settings.EMAIL_PORT
            and settings.EMAIL_HOST_USER and settings.EMAIL_HOST_PASSWORD
            and settings.DEFAULT_FROM_EMAIL
        ),
    }


def check_email_connection() -> bool:
    """Open and close the configured mail connection without sending a message."""
    status = email_configuration_status()
    if not status['ready']:
        return False
    connection = get_connection(fail_silently=False)
    try:
        connection.open()
        return True
    finally:
        connection.close()