import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forex_ai_pro.settings')
django.setup()

import time
import json
from django.test import RequestFactory
from trading_app.models import User, TradingSignal, Subscription, PaymentTransaction
from trading_app.api_views import dashboard_stats_view, live_markets_view, analyze_and_signal_view
from trading_app.ml.inference import _get_model, predict_signal
from trading_app.strategies.base import prepare_context
from trading_app.strategies import momentum, support_resistance, fibonacci, ict, choch
from trading_app.trading_bot.technical_analyzer import analyze_technical

def benchmark():
    print("=== PERFORMANCE BENCHMARK & STAGE MEASUREMENT ===")
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        user = User.objects.create_superuser('benchadmin', 'bench@test.com', 'Testpass123!')

    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.tier = 'BASIC'
    sub.is_active = True
    sub.save()

    rf = RequestFactory()
    timings = {}

    # 1. Dashboard API response time
    t0 = time.perf_counter()
    req = rf.get('/api/dashboard/stats/')
    req.user = user
    resp = dashboard_stats_view(req)
    timings['dashboard_api_ms'] = (time.perf_counter() - t0) * 1000

    # 2. Market-list API response time
    t0 = time.perf_counter()
    req = rf.get('/api/markets/')
    req.user = user
    resp = live_markets_view(req)
    timings['market_list_api_ms'] = (time.perf_counter() - t0) * 1000

    # 3. ML Model Initialization & Inference
    t0 = time.perf_counter()
    model_data = _get_model(symbol='EURUSD', granularity=900)
    t_model_load = (time.perf_counter() - t0) * 1000
    timings['ml_model_load_ms'] = t_model_load

    dummy_candles = [{
        'time': 1700000000 + i * 300,
        'open': 1.1, 'high': 1.12, 'low': 1.09, 'close': 1.11 + (i * 0.0001), 'volume': 1000
    } for i in range(100)]

    t0 = time.perf_counter()
    ml_res = predict_signal(dummy_candles, symbol='EURUSD', granularity=900, model_data=model_data)
    timings['ml_inference_ms'] = (time.perf_counter() - t0) * 1000

    # 4. Technical Indicator & SMC Calculations
    t0 = time.perf_counter()
    tech_res = analyze_technical('EUR/USD', dummy_candles)
    timings['technical_smc_ms'] = (time.perf_counter() - t0) * 1000

    # 5. Strategy Evaluation & Consensus
    t0 = time.perf_counter()
    ctx = prepare_context(dummy_candles)
    res_mom = momentum.evaluate(ctx)
    res_sr = support_resistance.evaluate(ctx)
    res_fib = fibonacci.evaluate(ctx)
    res_ict = ict.evaluate(ctx)
    res_choch = choch.evaluate(ctx)
    timings['strategy_evaluation_ms'] = (time.perf_counter() - t0) * 1000

    # 6. Full Analysis Pipeline Request
    t0 = time.perf_counter()
    req = rf.post('/api/analyze-signal/', json.dumps({'query_type': 'market_analysis', 'prompt': 'EUR/USD', 'granularity': 300}), content_type='application/json')
    req.user = user
    req._dont_enforce_csrf_checks = True
    resp = analyze_and_signal_view(req)
    timings['full_analysis_pipeline_ms'] = (time.perf_counter() - t0) * 1000

    print(json.dumps(timings, indent=2))

if __name__ == '__main__':
    benchmark()
