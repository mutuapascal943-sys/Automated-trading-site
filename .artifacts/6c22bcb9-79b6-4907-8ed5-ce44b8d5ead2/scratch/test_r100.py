import asyncio
import json
import websockets

async def test():
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    async with websockets.connect(uri) as ws:
        for sym in ["R_100", "1HZ100V", "R75", "R_75"]:
            await ws.send(json.dumps({"ticks_history": sym, "style": "candles", "granularity": 300, "count": 5, "end": "latest", "req_id": 1}))
            resp = await ws.recv()
            print(f"Symbol {sym} response:", resp[:200])

asyncio.run(test())
