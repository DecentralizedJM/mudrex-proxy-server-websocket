```
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  ███╗   ███╗██╗   ██╗██████╗ ██████╗ ███████╗██╗  ██╗
  ████╗ ████║██║   ██║██╔══██╗██╔══██╗██╔════╝╚██╗██╔╝
  ██╔████╔██║██║   ██║██║  ██║██████╔╝█████╗   ╚███╔╝
  ██║╚██╔╝██║██║   ██║██║  ██║██╔══██╗██╔══╝   ██╔██╗
  ██║ ╚═╝ ██║╚██████╔╝██████╔╝██║  ██║███████╗██╔╝ ██╗
  ╚═╝     ╚═╝ ╚═════╝ ╚═════╝ ╚═╝  ╚═╝╚══════╝╚═╝  ╚═╝
  ░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░░
  ░  >>  W E B S O C K E T  <<  ░  real-time stream  ░
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
  Bybit V5  →  Redis Pub/Sub  →  1000+ clients  |  ticker · kline · trade
  ▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓▓
```

<div align="center">

**[ REAL-TIME CRYPTO FUTURES DATA STREAM ]**

`████████████████████████████████████████████████`

</div>

---

## `> CONNECTION_ENDPOINT`

```bash
$ wss://mudrex-futures-websocket-service.up.railway.app/ws
```

---

## `> INIT_CONNECTION`

<details>
<summary><b>▸ PYTHON_CLIENT</b></summary>

```python
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
╔══════════════════════════════════════════╗
║ MUDREX FUTURES WEBSOCKET CLIENT v1.0 ║
╚══════════════════════════════════════════╝
"""
import asyncio, websockets, json

async def main():
    uri = "wss://mudrex-futures-websocket-service.up.railway.app/ws"
    async with websockets.connect(uri) as ws:
        # >> SUBSCRIBE TO STREAM
        await ws.send(json.dumps({
            "op": "subscribe",
            "args": ["ticker:BTCUSDT", "ticker:ETHUSDT"]
        }))
        # >> LISTEN FOR DATA
        async for msg in ws:
            data = json.loads(msg)
            print(f"[RECV] {data}")

if __name__ == "__main__":
    asyncio.run(main())
```
</details>

<details>
<summary><b>▸ JAVASCRIPT_CLIENT</b></summary>

```javascript
// ═══════════════════════════════════════════
// MUDREX FUTURES WEBSOCKET CLIENT v1.0
// ═══════════════════════════════════════════

const ws = new WebSocket('wss://mudrex-futures-websocket-service.up.railway.app/ws');

ws.onopen = () => {
    console.log('[CONNECTED] Initiating data stream...');
    ws.send(JSON.stringify({ op: 'subscribe', args: ['ticker:BTCUSDT'] }));
};

ws.onmessage = (e) => console.log('[RECV]', JSON.parse(e.data));
ws.onerror = (err) => console.error('[ERROR]', err);
```
</details>

---

## `> OPERATIONS`

```
┌─────────────────┬────────────────────────────────────────────────────┐
│ OPERATION │ PAYLOAD │
├─────────────────┼────────────────────────────────────────────────────┤
│ SUBSCRIBE │ {"op": "subscribe", "args": ["ticker:BTCUSDT"]} │
│ UNSUBSCRIBE │ {"op": "unsubscribe", "args": ["ticker:BTCUSDT"]} │
│ PING │ {"op": "ping"} │
└─────────────────┴────────────────────────────────────────────────────┘
```

**Subscribe Response:**
```json
{
  "op": "subscribe",
  "success": true,
  "args": ["ticker:BTCUSDT"],
  "rejected": null
}
```

**Subscribe Response (with invalid args):**
```json
{
  "op": "subscribe",
  "success": true,
  "args": ["ticker:BTCUSDT"],
  "rejected": [
    {
      "arg": "tickers.*",
      "reason": "Wildcards (e.g. tickers.*) are not supported. Use specific symbols like tickers.BTCUSDT or ticker:ETHUSDT."
    }
  ]
}
```

**Ping Response:**
```json
{
  "op": "pong",
  "timestamp": 1738743363000
}
```

---

## `> STREAM_TYPES`

```
╔═══════════════════════════════════════════════════════════════════╗
║ AVAILABLE STREAMS ║
╠═══════════╦═══════════════════════════╦═══════════════════════════╣
║ TYPE ║ FORMAT ║ EXAMPLE ║
╠═══════════╬═══════════════════════════╬═══════════════════════════╣
║ TICKER ║ ticker:{SYMBOL} ║ ticker:BTCUSDT ║
║ TICKER ║ tickers.{SYMBOL} ║ tickers.ETHUSDT ║
║ KLINE ║ kline:{INTERVAL}:{SYMBOL} ║ kline:1h:BTCUSDT ║
║ TRADE ║ trade:{SYMBOL} ║ trade:BTCUSDT ║
╚═══════════╩═══════════════════════════╩═══════════════════════════╝

KLINE_INTERVALS: 1m | 3m | 5m | 15m | 30m | 1h | 2h | 4h | 6h | 12h | 1d | 1w | 1M

⚠️ WARNING: Wildcards (e.g. tickers.*) are NOT supported. Bybit requires specific symbols.
```

---

## `> DATA_PACKET`

```json
{
  "stream": "mudrex.futures.ticker.BTCUSDT",
  "type": "update",
  "data": {
    "symbol": "BTCUSDT",
    "price": "71334.80",
    "markPrice": "71334.80",
    "indexPrice": "71362.13",
    "high24h": "76500.00",
    "low24h": "70800.00",
    "volume24h": "161937.00",
    "change24hPercent": "-6.69",
    "bid": { "price": "71334.70", "size": "0.242" },
    "ask": { "price": "71334.80", "size": "1.495" },
    "fundingRate": "0.0001"
  },
  "timestamp": 1738743363000,
  "source": "mudrex"
}
```

---

## `> FIELD_REFERENCE`

```
┌──────────────────────┬────────────────────────────────────────────────┐
│ FIELD │ DESCRIPTION │
├──────────────────────┼────────────────────────────────────────────────┤
│ price │ Last traded price │
│ markPrice │ Mark price (liquidation reference) │
│ indexPrice │ Multi-exchange index price │
│ high24h / low24h │ 24-hour high/low │
│ volume24h │ 24-hour volume (base currency) │
│ change24hPercent │ 24-hour price change % │
│ bid / ask │ Best bid/ask with size │
│ fundingRate │ Current funding rate │
│ openInterest │ Open interest (ticker only) │
│ nextFundingTime │ Next funding time (ticker only) │
└──────────────────────┴────────────────────────────────────────────────┘

⚠️ WARNING: Partial updates - only changed fields included per message
```

---

## `> SUPPORTED_SYMBOLS`

```
╭────────────────────────────────────────────────────────────╮
│ BTCUSDT │ ETHUSDT │ SOLUSDT │ XRPUSDT │ DOGEUSDT │
│ AVAXUSDT │ LINKUSDT │ MATICUSDT │ ARBUSDT │ OPUSDT │
│ ...and all other Bybit USDT Perpetual contracts │
╰────────────────────────────────────────────────────────────╯
```

---

## `> KEEPALIVE_PROTOCOL`

```python
# ═══════════════════════════════════════════
# HEARTBEAT DAEMON - PREVENTS TIMEOUT
# ═══════════════════════════════════════════

async def heartbeat(ws):
    """Send ping every 30 seconds to maintain connection"""
    while True:
        await asyncio.sleep(30)
        await ws.send(json.dumps({"op": "ping"}))
        print("[PING] keepalive sent")
```

```python
# ═══════════════════════════════════════════
# AUTO-RECONNECT WITH EXPONENTIAL BACKOFF
# ═══════════════════════════════════════════

async def connect_with_retry():
    delay = 1
    while True:
        try:
            async with websockets.connect(uri) as ws:
                delay = 1  # reset on success
                await handle_messages(ws)
        except Exception as e:
            print(f"[DISCONNECT] Reconnecting in {delay}s...")
            await asyncio.sleep(delay)
            delay = min(delay * 2, 60)
```

---

## `> RATE_LIMITS`

```
┌───────────────────────────┬───────────────┐
│ PARAMETER │ LIMIT │
├───────────────────────────┼───────────────┤
│ MAX_CLIENTS │ 10,000 │
│ SUBS_PER_CLIENT │ 100 │
│ IDLE_TIMEOUT │ 300s │
│ SUBSCRIBE_RATE │ 10/sec │
│ MSG_RATE │ 100/sec │
└───────────────────────────┴───────────────┘
```

---

## `> HTTP_ENDPOINTS`

```bash
# ─── HEALTH CHECK ───
$ curl https://mudrex-futures-websocket-service.up.railway.app/health

# ─── SERVER STATS ───
$ curl https://mudrex-futures-websocket-service.up.railway.app/stats

# ─── API INFO ───
$ curl https://mudrex-futures-websocket-service.up.railway.app/
```

---

## `> ERROR_CODES`

```
┌────────┬─────────────────────────────────────────────────────────┐
│ CODE │ MESSAGE │
├────────┼─────────────────────────────────────────────────────────┤
│ 400 │ INVALID_STREAM_FORMAT / INVALID_OPERATION │
│ 429 │ TOO_MANY_SUBSCRIPTIONS │
│ 503 │ SERVICE_UNAVAILABLE │
└────────┴─────────────────────────────────────────────────────────┘

ERROR_RESPONSE:
{"op": "error", "message": "Invalid stream format", "code": 400}
```

---

```
╔══════════════════════════════════════════════════════════════════╗
║ ║
║ ██████╗ ██╗ ██╗██╗██╗ ████████╗ ██████╗ ██╗ ██╗ ║
║ ██╔══██╗██║ ██║██║██║ ╚══██╔══╝ ██╔══██╗╚██╗ ██╔╝ ║
║ ██████╔╝██║ ██║██║██║ ██║ ██████╔╝ ╚████╔╝ ║
║ ██╔══██╗██║ ██║██║██║ ██║ ██╔══██╗ ╚██╔╝ ║
║ ██████╔╝╚██████╔╝██║███████╗██║ ██████╔╝ ██║ ║
║ ╚═════╝ ╚═════╝ ╚═╝╚══════╝╚═╝ ╚═════╝ ╚═╝ ║
║ ║
║ M U D R E X C O M M U N I T Y ║
║ ║
║ [ February 2026 ] ║
║ ║
╚══════════════════════════════════════════════════════════════════╝
```
