import asyncio
import json
import websockets

async def main():
    url = "wss://api.derivws.com/trading/v1/options/ws/public"
    async with websockets.connect(url, open_timeout=15) as ws:
        await ws.send(json.dumps({"ticks": "1HZ100V", "subscribe": 1}))
        for _ in range(3):
            print(await asyncio.wait_for(ws.recv(), timeout=15))

asyncio.run(main())
