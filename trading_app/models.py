from django.contrib.auth.models import AbstractUser
from django.db import models
from django.utils import timezone
from decimal import Decimal


class User(AbstractUser):
    email = models.EmailField(unique=True)
    broker = models.CharField(max_length=100, blank=True, default='')
    two_factor_enabled = models.BooleanField(default=False)
    balance = models.DecimalField(max_digits=14, decimal_places=2, default=Decimal('12430.00'))
    trial_started_at = models.DateTimeField(null=True, blank=True)
    broker_api_key = models.CharField(max_length=500, blank=True, default='')
    broker_api_secret = models.CharField(max_length=500, blank=True, default='')
    broker_account_id = models.CharField(max_length=100, blank=True, default='')
    daily_trades_count = models.IntegerField(default=0)
    last_trade_date = models.DateField(null=True, blank=True)
    paper_mode = models.BooleanField(default=True, help_text='Paper/demo mode — no real broker execution')
    stake_amount = models.DecimalField(max_digits=12, decimal_places=2, default=Decimal('10.00'),
                                       help_text='Stake amount per trade in account currency')
    watchlist = models.JSONField(default=list, blank=True, help_text='User-preferred trading symbols')
    selected_market = models.CharField(max_length=30, blank=True, default='EUR/USD',
        help_text='The market currently selected in the bot panel — the bot only trades this symbol')
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    phone = models.CharField(max_length=20, blank=True, default='')
    bio = models.TextField(max_length=500, blank=True, default='')

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['two_factor_enabled']),
        ]

    def __str__(self):
        return self.email


class RiskConfig(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='risk_config')
    risk_per_trade = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal('2.0'),
                                         help_text='Max risk per trade as % of balance')
    max_daily_trades = models.IntegerField(default=5, help_text='Max trades per day')
    max_drawdown = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal('10.0'),
                                       help_text='Max drawdown % before trading is halted')
    daily_profit_target = models.DecimalField(max_digits=4, decimal_places=1, default=Decimal('5.0'),
                                              help_text='Daily profit target %')
    max_position_size = models.DecimalField(max_digits=10, decimal_places=2, default=Decimal('1.0'),
                                            help_text='Max volume per position (lots)')
    max_exposure_percent = models.DecimalField(max_digits=5, decimal_places=1, default=Decimal('20.0'),
                                               help_text='Max total exposure as % of balance')
    min_confidence = models.IntegerField(default=60, help_text='Minimum signal confidence % to auto-execute')
    trailing_stop_percent = models.DecimalField(max_digits=5, decimal_places=2, null=True, blank=True,
                                                default=None, help_text='Trailing stop % (blank to disable)')
    max_open_positions = models.IntegerField(default=5, help_text='Max simultaneous open positions')
    auto_execute = models.BooleanField(default=True, help_text='Auto-execute signals without manual confirmation')
    trading_enabled = models.BooleanField(default=True, help_text='Master toggle for all trading')
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Risk Configuration'

    def __str__(self):
        return f'{self.user.email} risk config'

    @classmethod
    def get_for_user(cls, user) -> 'RiskConfig':
        obj, _ = cls.objects.get_or_create(user=user)
        return obj


class EmailOTP(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='otps')
    code = models.CharField(max_length=6)
    purpose = models.CharField(max_length=20, default='2fa', choices=[
        ('2fa', 'Two-Factor Authentication'),
        ('password_reset', 'Password Reset'),
        ('email_verify', 'Email Verification'),
    ])
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    is_used = models.BooleanField(default=False)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'purpose', 'is_used']),
            models.Index(fields=['expires_at']),
        ]

    def is_expired(self):
        return timezone.now() > self.expires_at

    def is_valid(self):
        return not self.is_used and not self.is_expired()

    def __str__(self):
        return f'{self.user.email} - {self.purpose} - {self.code}'


class Trade(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='trades')
    symbol = models.CharField(max_length=20)
    action = models.CharField(max_length=4, choices=[('BUY', 'Buy'), ('SELL', 'Sell')])
    volume = models.DecimalField(max_digits=10, decimal_places=2)
    entry_price = models.DecimalField(max_digits=14, decimal_places=5)
    exit_price = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    stop_loss = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    take_profit = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    pnl = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    status = models.CharField(max_length=20, default='PENDING', choices=[
        ('PENDING', 'Pending'),
        ('OPEN', 'Open'),
        ('CLOSED', 'Closed'),
        ('CANCELLED', 'Cancelled'),
        ('FAILED', 'Failed'),
    ])
    order_type = models.CharField(max_length=20, default='MARKET', choices=[
        ('MARKET', 'Market'),
        ('LIMIT', 'Limit'),
        ('STOP', 'Stop'),
    ])
    executed_at = models.DateTimeField(null=True, blank=True)
    closed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    is_live = models.BooleanField(default=False)
    broker_trade_id = models.CharField(max_length=100, blank=True, default='')
    current_price = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True, help_text='Current market price for paper trades')
    unrealized_pnl = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True, help_text='Unrealized P&L for open paper trades')

    class Meta:
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['symbol']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'{self.symbol} {self.action} - {self.status}'


class TradingSignal(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='signals', null=True, blank=True)
    symbol = models.CharField(max_length=20)
    signal_type = models.CharField(max_length=10, choices=[('BUY', 'Buy'), ('SELL', 'Sell'), ('HOLD', 'Hold')])
    confidence = models.IntegerField(default=0, help_text='Confidence percentage 0-100')
    entry_price = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    stop_loss = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    take_profit = models.DecimalField(max_digits=14, decimal_places=5, null=True, blank=True)
    reasoning = models.TextField(blank=True, default='')
    risk_level = models.CharField(max_length=10, default='MEDIUM', choices=[
        ('LOW', 'Low'), ('MEDIUM', 'Medium'), ('HIGH', 'High'),
    ])
    source = models.CharField(max_length=50, default='AI', choices=[
        ('AI', 'AI Analysis'), ('MANUAL', 'Manual'), ('SYSTEM', 'System'),
    ])
    is_executed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'signal_type']),
            models.Index(fields=['symbol', 'created_at']),
        ]

    def __str__(self):
        return f'{self.symbol} {self.signal_type} ({self.confidence}%)'


class PredictionRecord(models.Model):
    """Stores each live ML prediction with its feature vector and realized
    outcome, so the model can be retrained on real trading results."""

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='predictions', null=True, blank=True)
    symbol = models.CharField(max_length=30)
    granularity = models.IntegerField(default=900)
    horizon = models.IntegerField(default=4)
    bias = models.CharField(max_length=10, choices=[
        ('bullish', 'Bullish'), ('bearish', 'Bearish'),
    ])
    confidence = models.FloatField(default=0.0)
    probability = models.FloatField(default=0.5)
    features = models.JSONField(default=dict, blank=True)
    candle_time = models.BigIntegerField(default=0, help_text='Epoch of the entry candle close')
    predicted_at = models.DateTimeField(auto_now_add=True)
    resolved = models.BooleanField(default=False)
    realized_target = models.IntegerField(null=True, blank=True,
        help_text='1 = price rose over horizon, 0 = fell')
    prediction_correct = models.BooleanField(null=True, blank=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=['symbol', 'resolved']),
            models.Index(fields=['predicted_at']),
        ]

    def __str__(self):
        return f'{self.symbol} {self.bias} ({self.confidence:.0%})'


class Subscription(models.Model):
    TIERS = [
        ('FREE', 'Free'),
        ('BASIC', 'Basic'),
        ('PRO', 'Pro'),
    ]
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='subscription')
    tier = models.CharField(max_length=10, default='FREE', choices=TIERS)
    is_active = models.BooleanField(default=True)
    started_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField(null=True, blank=True)
    stripe_subscription_id = models.CharField(max_length=100, blank=True, default='')

    def __str__(self):
        return f'{self.user.email} - {self.tier}'


SECURITY_QUESTIONS = [
    ('pet', 'What was the name of your first pet?'),
    ('school', 'What was the name of your elementary school?'),
    ('city', 'In which city were you born?'),
    ('mother', 'What is your mother\'s maiden name?'),
    ('book', 'What was your favorite book as a child?'),
    ('movie', 'What was your favorite movie as a teenager?'),
    ('car', 'What was the make of your first car?'),
    ('teacher', 'What was the name of your favorite teacher?'),
]


class SecurityQuestion(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='security_questions')
    question_key = models.CharField(max_length=20, choices=SECURITY_QUESTIONS)
    answer_hash = models.CharField(max_length=128)

    class Meta:
        unique_together = ['user', 'question_key']
        indexes = [
            models.Index(fields=['user']),
        ]

    def __str__(self):
        return f'{self.user.email} - {self.get_question_key_display()}'


class Notification(models.Model):
    NOTIFICATION_TYPES = [
        ('system', 'System'),
        ('trade', 'Trade'),
        ('signal', 'Signal'),
        ('account', 'Account'),
    ]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    title = models.CharField(max_length=200)
    message = models.TextField(blank=True, default='')
    notification_type = models.CharField(max_length=20, choices=NOTIFICATION_TYPES, default='system')
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'is_read']),
            models.Index(fields=['created_at']),
        ]

    @classmethod
    def create_notification(cls, user, title, message='', notification_type='system'):
        dup = cls.objects.filter(
            user=user, title=title, message=message,
            notification_type=notification_type, is_read=False,
            created_at__gte=timezone.now() - timezone.timedelta(hours=1),
        ).exists()
        if dup:
            return None
        return cls.objects.create(
            user=user, title=title, message=message, notification_type=notification_type
        )

    def __str__(self):
        return f'{self.user.email} - {self.title}'


class RememberMeToken(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='remember_me_tokens')
    token = models.CharField(max_length=64, unique=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        indexes = [
            models.Index(fields=['token']),
            models.Index(fields=['expires_at']),
        ]

    def is_valid(self):
        return timezone.now() < self.expires_at

    def __str__(self):
        return f'{self.user.email} - remember_me'
