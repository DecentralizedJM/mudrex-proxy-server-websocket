#!/usr/bin/env python3
"""
Terminal client: connect to Mudrex Futures WebSocket and stream data.
With --retry (default), reconnects with exponential backoff if server is down/slow.
Usage:
  python scripts/connect_ws.py
  python scripts/connect_ws.py --stream ticker:ETHUSDT
  python scripts/connect_ws.py --url ws://127.0.0.1:8000/ws
  python scripts/connect_ws.py --no-retry   # one attempt, then exit
"""
import argparse
import asyncio
import json
import sys

try:
    import websockets
except ImportError:
    print("Install: pip install websockets", file=sys.stderr)
    sys.exit(1)

DEFAULT_URI = "wss://mudrex-futures-websocket-service.up.railway.app/ws"
DEFAULT_STREAMS = ["ticker:BTCUSDT"]
OPEN_TIMEOUT = 45  # Allow cold start (e.g. Railway wake-up ~30–60s)
RECONNECT_DELAY_INITIAL = 1
RECONNECT_DELAY_MAX = 60
RECONNECT_DELAY_MULTIPLIER = 2


async def run_once(uri: str, streams: list[str]) -> None:
    """Connect, subscribe, and stream until disconnect. Raises on connection failure."""
    async with websockets.connect(uri, open_timeout=OPEN_TIMEOUT) as ws:
        print("Connected. Subscribing:", streams, flush=True)
        await ws.send(json.dumps({"op": "subscribe", "args": streams}))
        async for raw in ws:
            try:
                msg = json.loads(raw)
                op = msg.get("op")
                if op == "subscribe":
                    print("[SUBSCRIBE]", msg.get("success"), "args:", msg.get("args"), "rejected:", msg.get("rejected"))
                elif op == "pong":
                    print("[PONG]", msg.get("timestamp"))
                elif op == "error":
                    print("[ERROR]", msg)
                elif "stream" in msg:
                    stream = msg.get("stream", "")
                    data = msg.get("data", {})
                    if "price" in data:
                        print(f"[{stream}] price={data.get('price')} mark={data.get('markPrice')} 24h%={data.get('change24hPercent')}")
                    else:
                        print(f"[{stream}]", json.dumps(data)[:120] + "..." if len(json.dumps(data)) > 120 else data)
                else:
                    print(raw[:200])
            except json.JSONDecodeError:
                print(raw[:200])


async def run(uri: str, streams: list[str], retry: bool):
    delay = RECONNECT_DELAY_INITIAL
    while True:
        print(f"Connecting to {uri} ...", flush=True)
        try:
            await run_once(uri, streams)
            # Normal disconnect (e.g. server closed)
            delay = RECONNECT_DELAY_INITIAL
            if not retry:
                return
            print("[DISCONNECT] Connection closed. Reconnecting...", flush=True)
        except (TimeoutError, ConnectionRefusedError, OSError) as e:
            print(f"[DISCONNECT] {e}", flush=True)
            if not retry:
                print("Server may be sleeping or down. Try --url ws://127.0.0.1:8000/ws for local.", file=sys.stderr)
                sys.exit(1)
            print(f"Reconnecting in {delay}s...", flush=True)
            await asyncio.sleep(delay)
            delay = min(delay * RECONNECT_DELAY_MULTIPLIER, RECONNECT_DELAY_MAX)
        except Exception as e:
            print(f"Error: {e}", file=sys.stderr)
            raise


def main():
    p = argparse.ArgumentParser(description="Mudrex WebSocket terminal client")
    p.add_argument("--url", default=DEFAULT_URI, help="WebSocket URL")
    p.add_argument("--stream", action="append", dest="streams", help="Stream(s), e.g. ticker:BTCUSDT (repeat for multiple)")
    p.add_argument("--retry", action="store_true", default=True, help="Reconnect with backoff if server is down (default)")
    p.add_argument("--no-retry", action="store_false", dest="retry", help="Exit after one failed connection")
    args = p.parse_args()
    streams = args.streams or DEFAULT_STREAMS
    asyncio.run(run(args.url, streams, args.retry))


if __name__ == "__main__":
    main()
