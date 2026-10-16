import os
import django
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'forex_ai_pro.settings')
django.setup()

import asyncio
import json
import websockets
from datetime import datetime, timezone

async def verify_all():
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    symbols = ['frxXAUUSD', 'frxGBPUSD', 'frxEURUSD', 'frxAUDUSD', 'cryBTCUSD']

    tick_results = {}
    candle_results = {}

    async with websockets.connect(uri) as ws:
        req_id = 1
        for sym in symbols:
            # 1. Stream 5 ticks
            await ws.send(json.dumps({"ticks": sym, "subscribe": 1, "req_id": req_id}))
            req_id += 1
            ticks = []
            sub_id = None
            while len(ticks) < 5:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    msg = json.loads(raw)
                    if msg.get("msg_type") == "tick":
                        t = msg.get("tick", {})
                        ticks.append({
                            "symbol": t.get("symbol"),
                            "price": float(t.get("quote", t.get("bid", 0))),
                            "epoch": int(t.get("epoch", 0))
                        })
                        if sub_id is None:
                            sub_id = msg.get("subscription", {}).get("id")
                except asyncio.TimeoutError:
                    break
            if sub_id:
                await ws.send(json.dumps({"forget": sub_id, "req_id": req_id}))
                req_id += 1
                await ws.recv()
            tick_results[sym] = ticks

            # 2. Candles for M1(60), M5(300), M15(900), H1(3600), H4(14400), Daily(86400)
            granularity_results = {}
            for gran in [60, 300, 900, 3600, 14400, 86400]:
                await ws.send(json.dumps({
                    "ticks_history": sym,
                    "style": "candles",
                    "granularity": gran,
                    "count": 50,
                    "end": "latest",
                    "req_id": req_id
                }))
                req_id += 1
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    res = json.loads(raw)
                    candles = res.get("candles", [])
                    if candles:
                        granularity_results[gran] = {
                            "count": len(candles),
                            "first": {"time": datetime.fromtimestamp(candles[0].get("epoch", 0), tz=timezone.utc).isoformat(), "close": float(candles[0].get("close", 0))},
                            "last": {"time": datetime.fromtimestamp(candles[-1].get("epoch", 0), tz=timezone.utc).isoformat(), "close": float(candles[-1].get("close", 0))}
                        }
                    else:
                        granularity_results[gran] = {"count": 0, "error": res.get("error")}
                except Exception as e:
                    granularity_results[gran] = {"error": str(e)}
            candle_results[sym] = granularity_results

    print("=== LIVE PER-MARKET TICK PROOF ===")
    print(json.dumps(tick_results, indent=2))
    print("=== LIVE CANDLE PROOF ===")
    print(json.dumps(candle_results, indent=2))

if __name__ == "__main__":
    asyncio.run(verify_all())
