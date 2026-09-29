import django.utils.timezone
from django.db import migrations, models


def start_legacy_trials(apps, schema_editor):
    User = apps.get_model('trading_app', 'User')
    User.objects.filter(trial_started_at__isnull=True).update(trial_started_at=models.F('date_joined'))


class Migration(migrations.Migration):
    dependencies = [
        ('trading_app', '0015_predictionrecord_entry_price_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='user',
            name='subscription_bypass',
            field=models.BooleanField(
                default=False,
                help_text='Admin grant for bot access without an active paid subscription or trial',
            ),
        ),
        migrations.AddField(
            model_name='user',
            name='subscription_access_denied',
            field=models.BooleanField(
                default=False,
                help_text='Admin revocation of bot access, including during an active trial or subscription',
            ),
        ),
        migrations.RunPython(start_legacy_trials, migrations.RunPython.noop),
        migrations.AlterField(
            model_name='user',
            name='trial_started_at',
            field=models.DateTimeField(blank=True, default=django.utils.timezone.now, null=True),
        ),
    ]