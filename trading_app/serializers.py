from decimal import Decimal
from rest_framework import serializers
from .models import (
    User, Trade, TradingSignal, Subscription, RiskConfig,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'broker', 'two_factor_enabled', 'balance',
                  'broker_api_key', 'broker_account_id', 'date_joined']
        read_only_fields = ['id', 'date_joined', 'balance', 'two_factor_enabled',
                            'broker_api_key', 'broker_account_id']
        extra_kwargs = {
            'broker_api_key': {'write_only': True},
        }


class TradeSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trade
        fields = '__all__'
        read_only_fields = ['user', 'created_at', 'executed_at', 'closed_at']


class TradeCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Trade
        fields = ['symbol', 'action', 'volume', 'entry_price', 'stop_loss',
                  'take_profit', 'order_type']
        extra_kwargs = {
            'volume': {'required': True, 'min_value': Decimal('0.01')},
        }


class TradingSignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradingSignal
        fields = '__all__'
        read_only_fields = ['user', 'created_at']


class SubscriptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Subscription
        fields = '__all__'
        read_only_fields = ['user', 'started_at']


class MarketDataSerializer(serializers.Serializer):
    symbol = serializers.CharField(max_length=20)
    price = serializers.DecimalField(max_digits=20, decimal_places=5)
    change = serializers.DecimalField(max_digits=10, decimal_places=2)
    high = serializers.DecimalField(max_digits=20, decimal_places=5, required=False)
    low = serializers.DecimalField(max_digits=20, decimal_places=5, required=False)
    volume = serializers.IntegerField(required=False)


class RiskConfigSerializer(serializers.ModelSerializer):
    class Meta:
        model = RiskConfig
        exclude = ['user', 'updated_at']


class BrokerConfigSerializer(serializers.Serializer):
    api_key = serializers.CharField(max_length=500, required=False, allow_blank=True)
    api_secret = serializers.CharField(max_length=500, required=False, allow_blank=True)
    account_id = serializers.CharField(max_length=100, required=False)
    paper_mode = serializers.BooleanField(required=False)
    broker = serializers.CharField(max_length=100, required=False)


class OTPVerifySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)
    purpose = serializers.ChoiceField(choices=['2fa', 'password_reset', 'email_verify'])
