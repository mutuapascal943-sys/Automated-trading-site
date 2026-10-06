import hashlib
import hmac

from django.conf import settings
from django.db import migrations, models
from django.utils import timezone


def hash_legacy_otps(apps, schema_editor):
    EmailOTP = apps.get_model('trading_app', 'EmailOTP')
    secret = settings.SECRET_KEY.encode()
    now = timezone.now()
    for otp in EmailOTP.objects.all().iterator():
        if otp.expires_at <= now:
            otp.is_used = True
            otp.save(update_fields=['is_used'])
            continue
        payload = f'{otp.user_id}:{otp.purpose}:{otp.code}'.encode()
        otp.code = hmac.new(secret, payload, hashlib.sha256).hexdigest()
        otp.save(update_fields=['code'])


class Migration(migrations.Migration):
    dependencies = [
        ('trading_app', '0018_paymenttransaction'),
    ]

    operations = [
        migrations.AlterField(
            model_name='emailotp',
            name='code',
            field=models.CharField(max_length=64),
        ),
        migrations.AddField(
            model_name='emailotp',
            name='failed_attempts',
            field=models.PositiveSmallIntegerField(default=0),
        ),
        migrations.RunPython(hash_legacy_otps, migrations.RunPython.noop),
    ]
