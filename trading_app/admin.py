from django.contrib import admin
from django.contrib.auth.admin import UserAdmin
from trading_app.admin_site import owner_admin_site
from .models import User, EmailOTP, Trade, TradingSignal, Subscription, PredictionRecord, PaymentTransaction, AdminAuditLog


class CustomUserAdmin(UserAdmin):
    list_display = ['id', 'first_name', 'last_name', 'phone', 'email', 'subscription_bypass', 'bot_bypass', 'subscription_access_denied', 'is_active', 'date_joined']
    list_filter = ['email_verified', 'two_factor_enabled', 'subscription_bypass', 'bot_bypass', 'subscription_access_denied', 'is_active']
    search_fields = ['email', 'first_name', 'last_name', 'phone']
    ordering = ['-date_joined']
    readonly_fields = [
        'username', 'last_login', 'date_joined', 'email',
        'first_name', 'last_name', 'phone', 'broker', 'balance',
        'email_verified', 'two_factor_enabled', 'selected_market', 'is_active', 'is_staff',
        'is_superuser', 'groups', 'user_permissions',
        'subscription_bypass', 'bot_bypass', 'subscription_access_denied',
    ]
    fieldsets = (
        ('Identity', {'fields': ('email', 'username', 'first_name', 'last_name', 'phone')}),
        ('Signal Profile', {'fields': ('balance', 'email_verified', 'two_factor_enabled', 'selected_market')}),
        ('Subscription and Bot Access', {'fields': ('subscription_bypass', 'bot_bypass', 'subscription_access_denied')}),
    )

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class EmailOTPAdmin(admin.ModelAdmin):
    list_display = ['user', 'purpose', 'created_at', 'expires_at', 'is_used']
    list_filter = ['purpose', 'is_used']
    search_fields = ['user__email']
    readonly_fields = [field.name for field in EmailOTP._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class TradeAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'action', 'volume', 'status', 'pnl', 'user', 'created_at']
    list_filter = ['status', 'action', 'symbol']
    search_fields = ['symbol', 'user__email', 'broker_trade_id']


class TradingSignalAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'signal_type', 'confidence', 'risk_level', 'user', 'created_at']
    list_filter = ['signal_type', 'risk_level', 'source']
    search_fields = ['symbol', 'user__email']


class SubscriptionAdmin(admin.ModelAdmin):
    list_display = ['user', 'tier', 'is_active', 'started_at', 'expires_at']
    list_filter = ['tier', 'is_active']
    search_fields = ['user__email']
    readonly_fields = [field.name for field in Subscription._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PaymentTransactionAdmin(admin.ModelAdmin):
    list_display = [
        'id', 'user', 'tier', 'amount_kes', 'status',
        'checkout_request_id', 'mpesa_receipt_number', 'created_at',
    ]
    list_filter = ['status', 'tier', 'created_at']
    search_fields = ['user__email', 'merchant_request_id', 'checkout_request_id', 'mpesa_receipt_number']
    readonly_fields = [field.name for field in PaymentTransaction._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


class PredictionRecordAdmin(admin.ModelAdmin):
    list_display = ['symbol', 'bias', 'confidence', 'resolved', 'prediction_correct', 'user', 'predicted_at']
    list_filter = ['symbol', 'bias', 'resolved', 'prediction_correct']
    search_fields = ['symbol', 'user__email']
    readonly_fields = ['features']


class AdminAuditLogAdmin(admin.ModelAdmin):
    list_display = ['created_at', 'administrator', 'affected_user', 'action']
    list_filter = ['action', 'created_at']
    search_fields = ['administrator__email', 'affected_user__email', 'action']
    readonly_fields = [field.name for field in AdminAuditLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


try:
    admin.site.unregister(User)
except admin.exceptions.NotRegistered:
    pass

owner_admin_site.register(User, CustomUserAdmin)
owner_admin_site.register(EmailOTP, EmailOTPAdmin)
owner_admin_site.register(Trade, TradeAdmin)
owner_admin_site.register(TradingSignal, TradingSignalAdmin)
owner_admin_site.register(Subscription, SubscriptionAdmin)
owner_admin_site.register(PaymentTransaction, PaymentTransactionAdmin)
owner_admin_site.register(PredictionRecord, PredictionRecordAdmin)
owner_admin_site.register(AdminAuditLog, AdminAuditLogAdmin)
