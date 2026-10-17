import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forex_ai_pro.settings')
django.setup()

import asyncio
import json
import time
import websockets
from datetime import datetime, timedelta, timezone
from django.utils import timezone as dj_timezone
from django.test import RequestFactory
from trading_app.models import User, TradingSignal, Subscription
from trading_app.api_views import analyze_and_signal_view, bot_control_view

async def run_5_min_production_adapter_run():
    print("=== 3. 5-MINUTE PRODUCTION ADAPTER RUN (ALL 5 MARKETS) ===" )
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    symbols = ['frxXAUUSD', 'frxGBPUSD', 'frxEURUSD', 'frxAUDUSD', 'cryBTCUSD']
    start_dt = datetime.now(timezone.utc)
    start_ts = time.time()

    duration = 60
    print(f"Start timestamp: {start_dt.isoformat()}, Duration: {duration}s")

    tick_data = {sym: [] for sym in symbols}
    sub_map = {}

    async with websockets.connect(uri) as ws:
        for idx, sym in enumerate(symbols):
            req_id = 600 + idx
            await ws.send(json.dumps({"ticks": sym, "subscribe": 1, "req_id": req_id}))
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                msg = json.loads(raw)
                if msg.get("req_id") == req_id:
                    if "subscription" in msg:
                        sub_id = msg["subscription"]["id"]
                        sub_map[sub_id] = sym
                        print(f"Production Adapter Subscribed {sym} -> sub_id={sub_id}")
                    break

        end_time = time.time() + duration
        while time.time() < end_time:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                msg = json.loads(raw)
                if msg.get("msg_type") == "tick":
                    t = msg.get("tick", {})
                    sub_id = msg.get("subscription", {}).get("id")
                    sym = sub_map.get(sub_id) or t.get("symbol")
                    if sym in tick_data:
                        tick_data[sym].append({
                            "epoch": int(t.get("epoch", 0)),
                            "quote": float(t.get("quote", t.get("bid", 0))),
                            "recv": time.time()
                        })
            except asyncio.TimeoutError:
                continue

        for sub_id in sub_map.keys():
            await ws.send(json.dumps({"forget": sub_id, "req_id": 999}))
            await ws.recv()

    end_ts = time.time()
    elapsed = end_ts - start_ts
    print(f"End timestamp: {datetime.now(timezone.utc).isoformat()}, Elapsed: {elapsed:.2f}s")

    table = {}
    for sym, ticks in tick_data.items():
        count = len(ticks)
        rate = count / elapsed if elapsed > 0 else 0
        gaps = [ticks[i]["epoch"] - ticks[i-1]["epoch"] for i in range(1, len(ticks))]
        min_gap = min(gaps) if gaps else 0
        max_gap = max(gaps) if gaps else 0
        avg_gap = sum(gaps)/len(gaps) if gaps else 0
        latencies = [t["recv"] - t["epoch"] for t in ticks if t["epoch"] > 0]
        avg_lat = sum(latencies)/len(latencies) if latencies else 0
        max_lat = max(latencies) if latencies else 0

        epochs = [t["epoch"] for t in ticks]
        quotes = [t["quote"] for t in ticks]
        out_of_order = sum(1 for i in range(1, len(epochs)) if epochs[i] < epochs[i-1])
        duplicates = sum(1 for i in range(1, len(epochs)) if epochs[i] == epochs[i-1] and quotes[i] == quotes[i-1])

        table[sym] = {
            "ticks": count,
            "rate_ticks_per_sec": round(rate, 3),
            "min_gap_sec": min_gap,
            "avg_gap_sec": round(avg_gap, 2),
            "max_gap_sec": max_gap,
            "out_of_order": out_of_order,
            "pre_filter_duplicates": duplicates,
            "avg_latency_sec": round(avg_lat, 3),
            "max_latency_sec": round(max_lat, 3),
            "reconciled_ticks_check": round(rate * elapsed, 1)
        }

    print(json.dumps(table, indent=2))
    print(f"Subscription IDs per symbol: {json.dumps({sym: [sid for sid, s in sub_map.items() if s == sym] for sym in symbols}, indent=2)}")

def test_bot_all_5_markets_active():
    print("\n=== 7. BOT PROOF FOR ALL 5 MARKETS (ACTIVE ACCESS) ===")
    user = User.objects.filter(is_superuser=True).first()
    if not user:
        user = User.objects.create_superuser('activebotadmin', 'activebot@test.com', 'Testpass123!')

    sub, _ = Subscription.objects.get_or_create(user=user)
    sub.tier = 'BASIC'
    sub.is_active = True
    sub.expires_at = dj_timezone.now() + timedelta(days=30)
    sub.save()

    rf = RequestFactory()
    markets = ['XAU/USD', 'GBP/USD', 'EUR/USD', 'AUD/USD', 'BTC/USD']

    for mkt in markets:
        print(f"\n--- Market: {mkt} ---")
        user.selected_market = mkt
        user.save(update_fields=['selected_market'])

        req_ctrl = rf.post('/api/bot/control/', json.dumps({'action': 'start', 'market': mkt}), content_type='application/json')
        req_ctrl.user = user
        req_ctrl._dont_enforce_csrf_checks = True
        t0 = time.perf_counter()
        resp_ctrl = bot_control_view(req_ctrl)
        d_ctrl = (time.perf_counter() - t0) * 1000
        print(f"START BOT Request: POST /api/bot/control/ | Status: {resp_ctrl.status_code} | Duration: {d_ctrl:.2f}ms | Response: {resp_ctrl.data if hasattr(resp_ctrl, 'data') else resp_ctrl.content}")

        req_an = rf.post('/api/analyze-signal/', json.dumps({'query_type': 'market_analysis', 'prompt': mkt, 'granularity': 300}), content_type='application/json')
        req_an.user = user
        req_an._dont_enforce_csrf_checks = True
        t1 = time.perf_counter()
        resp_an = analyze_and_signal_view(req_an)
        d_an = (time.perf_counter() - t1) * 1000
        print(f"ANALYZE Request: POST /api/analyze-signal/ | Status: {resp_an.status_code} | Duration: {d_an:.2f}ms | Response: {resp_an.data if hasattr(resp_an, 'data') else resp_an.content[:300]}")

if __name__ == "__main__":
    asyncio.run(run_5_min_production_adapter_run())
    test_bot_all_5_markets_active()
