from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from .models import User, EmailOTP, Trade, TradingSignal, Subscription, PredictionRecord


class CustomUserAdmin(UserAdmin):
    list_display = ['id', 'first_name', 'last_name', 'phone', 'email', 'subscription_bypass', 'subscription_access_denied', 'is_active', 'date_joined']
    list_filter = ['two_factor_enabled', 'subscription_bypass', 'subscription_access_denied', 'broker', 'is_active']
    search_fields = ['email', 'first_name', 'last_name', 'phone']
    ordering = ['-date_joined']
    readonly_fields = [
        'username', 'password', 'last_login', 'date_joined', 'email',
        'first_name', 'last_name', 'phone', 'broker', 'balance',
        'two_factor_enabled', 'selected_market', 'is_active', 'is_staff',
        'is_superuser', 'groups', 'user_permissions',
    ]
    fieldsets = UserAdmin.fieldsets + (
        ('Trading Profile', {'fields': ('broker', 'balance', 'broker_api_key', 'broker_api_secret', 'broker_account_id', 'two_factor_enabled', 'selected_market')}),
        ('Subscription Access', {'fields': ('subscription_bypass', 'subscription_access_denied')}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


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
    readonly_fields = [field.name for field in Subscription._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(PredictionRecord)
class PredictionRecordAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'bias', 'confidence', 'resolved', 'prediction_correct', 'user', 'predicted_at']
    list_filter = ['symbol', 'bias', 'resolved', 'prediction_correct']
    search_fields = ['symbol', 'user__email']
    readonly_fields = ['features']


try:
    admin.site.unregister(User)
except admin.exceptions.NotRegistered:
    pass
admin.site.register(User, CustomUserAdmin)
