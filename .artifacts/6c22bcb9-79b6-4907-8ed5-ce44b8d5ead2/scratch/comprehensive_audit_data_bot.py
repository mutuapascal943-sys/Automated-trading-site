import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forex_ai_pro.settings')
django.setup()

import asyncio
import json
import time
import websockets
from datetime import datetime
from django.utils import timezone
from django.test import RequestFactory
from channels.layers import get_channel_layer
from trading_app.models import User, TradingSignal, Subscription, RiskConfig
from trading_app.api_views import bot_control_view, analyze_and_signal_view

def diagnose_active_bot():
    print("\n=== B.1 & B.5 BOT REPRODUCTION WITH ACTIVE ACCESS ===")
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        user = User.objects.create_superuser('adminaudit', 'admin@test.com', 'Testpass123!')

    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.tier = 'BASIC'
    sub.is_active = True
    sub.expires_at = timezone.now() + timezone.timedelta(days=30)
    sub.save()

    rf = RequestFactory()
    markets = ['XAU/USD', 'GBP/USD', 'EUR/USD', 'AUD/USD', 'BTC/USD']

    for mkt in markets:
        print(f"\n--- Testing Start Bot & Analysis for {mkt} ---")
        req_ctrl = rf.post('/api/bot/control/', json.dumps({'action': 'start', 'market': mkt}), content_type='application/json')
        req_ctrl.user = user
        req_ctrl._dont_enforce_csrf_checks = True
        resp_ctrl = bot_control_view(req_ctrl)
        print(f"Bot control START response: status={resp_ctrl.status_code}, data={resp_ctrl.data if hasattr(resp_ctrl, 'data') else resp_ctrl.content}")

        req_an = rf.post('/api/analyze-signal/', json.dumps({'query_type': 'market_analysis', 'prompt': mkt, 'granularity': 300}), content_type='application/json')
        req_an.user = user
        req_an._dont_enforce_csrf_checks = True
        resp_an = analyze_and_signal_view(req_an)
        print(f"Analyze signal response: status={resp_an.status_code}, data={resp_an.data if hasattr(resp_an, 'data') else resp_an.content[:300]}")

    signals = TradingSignal.objects.order_by('-created_at')[:20]
    print(f"\nTotal stored signals: {TradingSignal.objects.count()}")
    for sig in signals:
        print(f"Signal {sig.pk}: {sig.symbol} {sig.signal_type} confidence={sig.confidence} reasoning={sig.reasoning}")

if __name__ == "__main__":
    diagnose_active_bot()
