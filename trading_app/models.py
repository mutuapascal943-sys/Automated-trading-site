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

    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = ['username']

    class Meta:
        indexes = [
            models.Index(fields=['email']),
            models.Index(fields=['two_factor_enabled']),
        ]

    def __str__(self):
        return self.email


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


class RAGDocument(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='rag_documents', null=True, blank=True)
    title = models.CharField(max_length=255)
    content = models.TextField()
    source = models.CharField(max_length=50, default='manual', choices=[
        ('manual', 'Manual Upload'),
        ('web', 'Web Scrape'),
        ('api', 'API Import'),
        ('email', 'Email Import'),
    ])
    file_type = models.CharField(max_length=20, blank=True, default='')
    file_size = models.IntegerField(default=0, help_text='Size in bytes')
    chunk_count = models.IntegerField(default=0)
    is_indexed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['user']),
            models.Index(fields=['source']),
            models.Index(fields=['is_indexed']),
        ]

    def __str__(self):
        return self.title


class RAGChunk(models.Model):
    document = models.ForeignKey(RAGDocument, on_delete=models.CASCADE, related_name='chunks')
    chunk_id = models.CharField(max_length=12)
    text = models.TextField()
    start_pos = models.IntegerField(default=0)
    end_pos = models.IntegerField(default=0)
    embedding = models.JSONField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['document']),
            models.Index(fields=['chunk_id']),
        ]

    def __str__(self):
        return f'{self.document.title} - chunk {self.chunk_id}'


class LLMQuery(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='llm_queries', null=True, blank=True)
    query_type = models.CharField(max_length=30, choices=[
        ('market_analysis', 'Market Analysis'),
        ('trading_idea', 'Trading Idea'),
        ('rag_query', 'Knowledge Query'),
        ('sentiment', 'Sentiment Analysis'),
    ])
    prompt = models.TextField()
    response = models.TextField(blank=True, default='')
    context_used = models.JSONField(null=True, blank=True)
    tokens_used = models.IntegerField(default=0)
    latency_ms = models.IntegerField(default=0)
    success = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=['user', 'query_type']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f'{self.query_type} - {self.created_at}'


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
