from django.core.management.base import BaseCommand, CommandError

from trading_app.services.email_diagnostics import check_email_connection, email_configuration_status


class Command(BaseCommand):
    help = 'Check SMTP readiness and connectivity without sending an email or exposing credentials.'

    def handle(self, *args, **options):
        status = email_configuration_status()
        self.stdout.write(f"Backend: {status['backend']}")
        self.stdout.write(f"SMTP configuration ready: {'yes' if status['ready'] else 'no'}")
        for key in (
            'host_configured', 'port_configured', 'tls_enabled', 'username_configured',
            'password_configured', 'from_configured',
        ):
            self.stdout.write(f"{key}: {'yes' if status[key] else 'no'}")
        if not status['ready']:
            raise CommandError('SMTP is not ready. Configure the required EMAIL_* settings in .env.')
        try:
            check_email_connection()
        except Exception as exc:
            raise CommandError(f'SMTP connectivity failed ({type(exc).__name__}).') from None
        self.stdout.write(self.style.SUCCESS('SMTP connection succeeded; no email was sent.'))