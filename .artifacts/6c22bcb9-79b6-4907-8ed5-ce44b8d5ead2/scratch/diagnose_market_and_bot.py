import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forex_ai_pro.settings')
django.setup()

import asyncio
import json
import time
from django.test import RequestFactory
from trading_app.models import User, TradingSignal
from trading_app.api_views import analyze_and_signal_view

def diagnose_bot():
    print("\n=== PART 2: BOT DIAGNOSTICS ===")
    user = User.objects.filter(is_superuser=False).first()
    if not user:
        user = User.objects.create_user(
            username='auditbot', email='audit_bot_test@example.com', password='Testpass123!', email_verified=True
        )

    rf = RequestFactory()
    markets = ['XAU/USD', 'GBP/USD', 'EUR/USD', 'AUD/USD', 'BTC/USD']
    for mkt in markets:
        print(f"\nTesting analysis API endpoint for {mkt} (M5 / 300s)...")
        try:
            req = rf.post('/api/analyze-signal/', json.dumps({'query_type': 'market_analysis', 'prompt': mkt, 'granularity': 300}), content_type='application/json')
            req.user = user
            req._dont_enforce_csrf_checks = True
            response = analyze_and_signal_view(req)
            print(f"Result for {mkt}: status_code={response.status_code}, data={response.data if hasattr(response, 'data') else response.content[:200]}")
        except Exception as e:
            print(f"ERROR analyzing {mkt}: {type(e).__name__}: {e}")

    signals = TradingSignal.objects.order_by('-created_at')[:20]
    print(f"\nRecent signals count: {signals.count()}")
    for sig in signals:
        print(f"Signal {sig.pk}: {sig.symbol} {sig.signal_type} confidence={sig.confidence} reasoning={sig.reasoning[:100]}")

if __name__ == "__main__":
    diagnose_bot()
