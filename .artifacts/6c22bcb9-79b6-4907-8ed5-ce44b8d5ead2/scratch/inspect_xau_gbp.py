import asyncio
import json
import websockets

async def inspect():
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    async with websockets.connect(uri) as ws:
        # Active symbols
        await ws.send(json.dumps({"active_symbols": "brief", "req_id": 1}))
        resp = await ws.recv()
        data = json.loads(resp)
        for s in data.get("active_symbols", []):
            if s.get("underlying_symbol") in ["frxXAUUSD", "frxGBPUSD"] or s.get("symbol") in ["frxXAUUSD", "frxGBPUSD"]:
                print("Symbol status:", s)

        # Test subscribe to frxXAUUSD
        await ws.send(json.dumps({"ticks": "frxXAUUSD", "subscribe": 1, "req_id": 2}))
        raw = await ws.recv()
        print("XAUUSD Sub Response:", raw)

        # Test subscribe to frxGBPUSD
        await ws.send(json.dumps({"ticks": "frxGBPUSD", "subscribe": 1, "req_id": 3}))
        raw2 = await ws.recv()
        print("GBPUSD Sub Response:", raw2)

asyncio.run(inspect())
