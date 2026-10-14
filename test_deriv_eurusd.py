import asyncio
import json
import websockets

async def test():
    url = "wss://api.derivws.com/trading/v1/options/ws/public"
    async with websockets.connect(url, open_timeout=10) as ws:
        await ws.send(json.dumps({
            "ticks": "frxEURUSD",
            "subscribe": 1,
            "req_id": 1
        }))
        print(await asyncio.wait_for(ws.recv(), timeout=10))

asyncio.run(test())

