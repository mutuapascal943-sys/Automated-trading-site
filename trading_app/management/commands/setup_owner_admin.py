from getpass import GetPassWarning, getpass
import warnings

from django.contrib.auth.password_validation import validate_password
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from trading_app.services.admin_access import ADMIN_EMAIL
from trading_app.models import User


class Command(BaseCommand):
    help = 'Securely create or update the single authorized owner administrator.'

    def handle(self, *args, **options):
        with warnings.catch_warnings():
            warnings.simplefilter('error', GetPassWarning)
            try:
                password = getpass('New administrator password: ', stream=self.stderr)
                confirmation = getpass('Confirm administrator password: ', stream=self.stderr)
            except (GetPassWarning, EOFError) as exc:
                raise CommandError(
                    'A hidden password prompt is unavailable. Run this command in an interactive PowerShell/Windows Terminal.'
                ) from None
        if not password or password != confirmation:
            raise CommandError('Passwords must be non-empty and match.')
        candidate = User(email=ADMIN_EMAIL, username=ADMIN_EMAIL.split('@')[0])
        try:
            validate_password(password, user=candidate)
        except Exception as exc:
            raise CommandError(str(exc)) from None

        with transaction.atomic():
            matches = User.objects.filter(email__iexact=ADMIN_EMAIL).order_by('pk')
            user = matches.filter(email=ADMIN_EMAIL).first() or matches.first()
            if user is None:
                user = User(email=ADMIN_EMAIL, username=ADMIN_EMAIL.split('@')[0])
            user.email = ADMIN_EMAIL
            if User.objects.exclude(pk=user.pk).filter(username=user.username).exists():
                user.username = f'owner-admin-{user.pk or "new"}'
            user.is_active = True
            user.is_staff = True
            user.is_superuser = True
            user.two_factor_enabled = True
            user.set_password(password)
            user.save()
            User.objects.filter(email__iexact=ADMIN_EMAIL).exclude(pk=user.pk).update(
                is_staff=False, is_superuser=False, two_factor_enabled=True,
            )
        self.stdout.write(self.style.SUCCESS('Authorized administrator configured.'))