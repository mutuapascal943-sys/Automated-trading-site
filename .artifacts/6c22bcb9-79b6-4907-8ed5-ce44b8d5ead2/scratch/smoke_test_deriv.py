import asyncio
import json
import websockets

async def smoke_test():
    uri = "wss://api.derivws.com/trading/v1/options/ws/public"
    print(f"Connecting to Deriv public endpoint: {uri}")
    async with websockets.connect(uri) as ws:
        # 1. List active symbols
        print("Fetching active symbols...")
        await ws.send(json.dumps({"active_symbols": "brief", "req_id": 1}))
        resp = await ws.recv()
        data = json.loads(resp)
        symbols = data.get("active_symbols", [])
        print(f"Success! Retrieved {len(symbols)} active symbols.")

        # 2. Fetch 50 M5 candles (granularity 300) for frxEURUSD
        print("Fetching 50 M5 candles for frxEURUSD...")
        await ws.send(json.dumps({
            "ticks_history": "frxEURUSD",
            "style": "candles",
            "granularity": 300,
            "count": 50,
            "end": "latest",
            "req_id": 2
        }))
        resp2 = await ws.recv()
        data2 = json.loads(resp2)
        candles = data2.get("candles", [])
        print(f"Success! Retrieved {len(candles)} M5 candles. Latest close: {candles[-1].get('close') if candles else 'N/A'}")

        # 3. Stream 10 ticks for frxEURUSD
        print("Streaming 10 ticks for frxEURUSD...")
        await ws.send(json.dumps({
            "ticks": "frxEURUSD",
            "subscribe": 1,
            "req_id": 3
        }))
        tick_count = 0
        sub_id = None
        while tick_count < 10:
            msg = await ws.recv()
            msg_data = json.loads(msg)
            if msg_data.get("msg_type") == "tick":
                tick_count += 1
                tick = msg_data.get("tick", {})
                print(f"Tick {tick_count}: price={tick.get('quote')} time={tick.get('epoch')}")
                if sub_id is None:
                    sub_id = msg_data.get("subscription", {}).get("id")

        # 4. Forget subscription and exit cleanly
        if sub_id:
            print(f"Forgetting subscription {sub_id}...")
            await ws.send(json.dumps({"forget": sub_id, "req_id": 4}))
            await ws.recv()
        print("Smoke test completed successfully and exiting cleanly.")

if __name__ == "__main__":
    asyncio.run(smoke_test())
