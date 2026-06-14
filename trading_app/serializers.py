from rest_framework import serializers
from .models import (
    User, Trade, TradingSignal, RAGDocument,
    RAGChunk, LLMQuery, Subscription,
)


class UserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ['id', 'email', 'broker', 'two_factor_enabled', 'balance',
                  'broker_api_key', 'broker_account_id', 'date_joined']
        read_only_fields = ['id', 'date_joined']
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
            'volume': {'required': True, 'min_value': 0.01},
        }


class TradingSignalSerializer(serializers.ModelSerializer):
    class Meta:
        model = TradingSignal
        fields = '__all__'
        read_only_fields = ['user', 'created_at']


class RAGDocumentSerializer(serializers.ModelSerializer):
    chunk_count = serializers.IntegerField(read_only=True)
    file_size_display = serializers.SerializerMethodField()

    class Meta:
        model = RAGDocument
        fields = ['id', 'title', 'content', 'source', 'file_type', 'file_size',
                  'file_size_display', 'chunk_count', 'is_indexed', 'created_at', 'updated_at']
        read_only_fields = ['id', 'chunk_count', 'is_indexed', 'created_at', 'updated_at']

    def get_file_size_display(self, obj):
        if obj.file_size < 1024:
            return f'{obj.file_size} B'
        elif obj.file_size < 1024 * 1024:
            return f'{obj.file_size / 1024:.1f} KB'
        return f'{obj.file_size / (1024 * 1024):.1f} MB'


class LLMQuerySerializer(serializers.ModelSerializer):
    class Meta:
        model = LLMQuery
        fields = '__all__'
        read_only_fields = ['user', 'response', 'tokens_used', 'latency_ms',
                            'success', 'created_at']


class LLMQueryCreateSerializer(serializers.Serializer):
    query_type = serializers.ChoiceField(choices=[
        'market_analysis', 'trading_idea', 'rag_query', 'sentiment',
    ])
    prompt = serializers.CharField(max_length=10000)
    market_data = serializers.CharField(required=False, allow_blank=True)


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


class BrokerConfigSerializer(serializers.Serializer):
    api_key = serializers.CharField(max_length=500)
    api_secret = serializers.CharField(max_length=500)
    account_id = serializers.CharField(max_length=100, required=False)


class OTPVerifySerializer(serializers.Serializer):
    code = serializers.CharField(max_length=6, min_length=6)
    purpose = serializers.ChoiceField(choices=['2fa', 'password_reset', 'email_verify'])
