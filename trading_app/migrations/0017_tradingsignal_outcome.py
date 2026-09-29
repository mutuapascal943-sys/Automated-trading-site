from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('trading_app', '0016_user_subscription_bypass'),
    ]

    operations = [
        migrations.AddField(
            model_name='tradingsignal',
            name='outcome',
            field=models.CharField(
                choices=[('PENDING', 'Pending'), ('WIN', 'Win'), ('LOSS', 'Loss'), ('UNKNOWN', 'Unknown')],
                default='PENDING',
                max_length=10,
            ),
        ),
        migrations.AddField(
            model_name='tradingsignal',
            name='outcome_price',
            field=models.DecimalField(blank=True, decimal_places=5, max_digits=14, null=True),
        ),
        migrations.AddField(
            model_name='tradingsignal',
            name='outcome_resolved_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
    ]