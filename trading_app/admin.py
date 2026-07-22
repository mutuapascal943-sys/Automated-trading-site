from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, EmailOTP, Trade, TradingSignal, Subscription


class CustomUserAdmin(UserAdmin):
    list_display = ['email', 'broker', 'two_factor_enabled', 'balance', 'is_active', 'date_joined']
    list_filter = ['two_factor_enabled', 'broker', 'is_active']
    search_fields = ['email', 'broker']
    ordering = ['-date_joined']
    fieldsets = UserAdmin.fieldsets + (
        ('Trading Profile', {'fields': ('broker', 'balance', 'broker_api_key', 'broker_api_secret', 'broker_account_id', 'two_factor_enabled')}),
    )


@admin.register(EmailOTP)
class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ['user', 'purpose', 'created_at', 'expires_at', 'is_used']
    list_filter = ['purpose', 'is_used']
    search_fields = ['user__email', 'code']


@admin.register(Trade)
class TradeAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'action', 'volume', 'status', 'pnl', 'user', 'created_at']
    list_filter = ['status', 'action', 'symbol']
    search_fields = ['symbol', 'user__email', 'broker_trade_id']


@admin.register(TradingSignal)
class TradingSignalAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'signal_type', 'confidence', 'risk_level', 'user', 'created_at']
    list_filter = ['signal_type', 'risk_level', 'source']
    search_fields = ['symbol', 'user__email']


@admin.register(Subscription)
class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'tier', 'is_active', 'started_at', 'expires_at']
    list_filter = ['tier', 'is_active']
    search_fields = ['user__email']


try:
    admin.site.unregister(User)
except admin.exceptions.NotRegistered:
    pass
admin.site.register(User, CustomUserAdmin)
