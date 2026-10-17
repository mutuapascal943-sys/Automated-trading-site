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

async def test_streaming_concurrent():
    print("=== 1. CONCURRENT LIVE STREAMING (ALL 5 MARKETS) ===")
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    symbols = ['frxXAUUSD', 'frxGBPUSD', 'frxEURUSD', 'frxAUDUSD', 'cryBTCUSD']
    start_ts = time.time()
    duration = 30

    tick_data = {sym: [] for sym in symbols}
    sub_map = {}

    async with websockets.connect(uri) as ws:
        for idx, sym in enumerate(symbols):
            req_id = 500 + idx
            await ws.send(json.dumps({"ticks": sym, "subscribe": 1, "req_id": req_id}))
            # Read until subscription response
            while True:
                raw = await asyncio.wait_for(ws.recv(), timeout=3.0)
                msg = json.loads(raw)
                if msg.get("req_id") == req_id:
                    if "subscription" in msg:
                        sub_id = msg["subscription"]["id"]
                        sub_map[sub_id] = sym
                        print(f"Subscribed {sym} -> sub_id={sub_id}")
                    else:
                        print(f"Sub error {sym}: {msg}")
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

    elapsed = time.time() - start_ts
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
            "duplicates": duplicates,
            "avg_latency_sec": round(avg_lat, 3),
            "max_latency_sec": round(max_lat, 3),
            "reconciled_ticks_check": round(rate * elapsed, 1)
        }

    print(json.dumps(table, indent=2))
    print(f"Active subscriptions per symbol: {json.dumps({sym: [sid for sid, s in sub_map.items() if s == sym] for sym in symbols}, indent=2)}")

if __name__ == "__main__":
    asyncio.run(test_streaming_concurrent())
