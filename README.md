# Mudrex Futures WebSocket Proxy

Production-ready WebSocket proxy server that connects to Bybit V5 API and exposes a **Mudrex-branded** real-time data stream.

![Architecture](https://img.shields.io/badge/Architecture-FastAPI%20%2B%20Redis-blue)
![Python](https://img.shields.io/badge/Python-3.11+-green)
![Deploy](https://img.shields.io/badge/Deploy-Railway-purple)

## Overview

This proxy allows you to:
- Stream real-time futures market data (tickers, klines, trades) under the Mudrex brand
- Connect thousands of clients through efficient multiplexing
- Automatically manage Bybit connections and avoid rate limits
- Scale horizontally with Redis pub/sub

## Quick Start

### 1. Clone and Configure

```bash
git clone https://github.com/DecentralizedJM/mudrex-proxy-server-websocket.git
cd mudrex-proxy-server-websocket
cp .env.example .env
# Edit .env with your Redis URL
```

### 2. Run Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Start with Redis (using Docker)
docker run -d -p 6379:6379 redis:alpine

# Run the server
python -m uvicorn app.main:app --reload
```

### 3. Deploy to Railway

1. Connect your GitHub repo to [Railway](https://railway.app)
2. Add a **Redis** service
3. Set environment variable: `REDIS_URL` (from Redis service)
4. Deploy!

Your WebSocket will be available at: `wss://your-app.up.railway.app/ws`

## WebSocket API

### Connect

```python
import asyncio
import websockets
import json

async def main():
    async with websockets.connect("wss://your-server.up.railway.app/ws") as ws:
        # Subscribe to streams
        await ws.send(json.dumps({
            "op": "subscribe",
            "args": ["ticker:BTCUSDT", "ticker:ETHUSDT"]
        }))
        
        # Receive data
        async for message in ws:
            data = json.loads(message)
            print(data)

asyncio.run(main())
```

### Stream Formats

| Stream Type | Format | Example |
|-------------|--------|---------|
| Ticker | `ticker:{SYMBOL}` | `ticker:BTCUSDT` |
| Kline | `kline:{INTERVAL}:{SYMBOL}` | `kline:1h:BTCUSDT` |
| Trade | `trade:{SYMBOL}` | `trade:BTCUSDT` |

### Operations

| Operation | Request | Response |
|-----------|---------|----------|
| Subscribe | `{"op": "subscribe", "args": ["ticker:BTCUSDT"]}` | `{"op": "subscribe", "success": true, "args": [...]}` |
| Unsubscribe | `{"op": "unsubscribe", "args": ["ticker:BTCUSDT"]}` | `{"op": "unsubscribe", "success": true, "args": [...]}` |
| Ping | `{"op": "ping"}` | `{"op": "pong", "timestamp": 1234567890}` |

### Data Message Format

```json
{
    "stream": "mudrex.futures.ticker.BTCUSDT",
    "type": "update",
    "data": {
        "symbol": "BTCUSDT",
        "price": "44578.50",
        "markPrice": "44575.00",
        "high24h": "45000.00",
        "low24h": "43500.00",
        "volume24h": "112000",
        "change24hPercent": "1.44",
        "bid": {"price": "44578.00", "size": "10"},
        "ask": {"price": "44579.00", "size": "5"},
        "fundingRate": "0.0001"
    },
    "timestamp": 1704063600000,
    "source": "mudrex"
}
```

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     Mudrex WS Proxy                         │
│                                                             │
│  ┌─────────┐    ┌─────────────┐    ┌─────────────────────┐  │
│  │ Client  │────│  FastAPI    │────│  Redis Pub/Sub      │  │
│  │ WS      │    │  Handler    │    │  (Fan-out)          │  │
│  └─────────┘    └─────────────┘    └─────────────────────┘  │
│                        │                     │              │
│                        ▼                     ▼              │
│               ┌─────────────┐    ┌─────────────────────┐    │
│               │ Subscription│    │  Upstream Pool      │    │
│               │ Manager     │    │  (Bybit Client)     │    │
│               └─────────────┘    └─────────────────────┘    │
│                                           │                 │
└───────────────────────────────────────────│─────────────────┘
                                            ▼
                                  ┌───────────────────┐
                                  │   Bybit V5 WS     │
                                  │   (Upstream)      │
                                  └───────────────────┘
```

## Environment Variables

| Variable | Required | Default | Description |
|----------|----------|---------|-------------|
| `REDIS_URL` | ✅ | - | Redis connection URL |
| `DEBUG` | | `false` | Enable debug logging |
| `ENVIRONMENT` | | `production` | Environment name |
| `REDIS_MAX_CONNECTIONS` | | `50` | Redis pool size; use 50–100 for 1000+ clients |
| `MAX_CLIENTS_TOTAL` | | `10000` | Max connected clients |
| `MAX_SUBSCRIPTIONS_PER_CLIENT` | | `100` | Max subscriptions per client |
| `MAX_MESSAGE_RATE_PER_CLIENT` | | `100` | Max client messages per second (receive-side) |
| `CLIENT_IDLE_TIMEOUT` | | `300` | Idle timeout in seconds |
| `FANOUT_CALLBACK_TIMEOUT` | | `5` | Seconds per client send before skipping slow clients |

## HTTP Endpoints

| Endpoint | Description |
|----------|-------------|
| `GET /` | API info |
| `GET /health` | Health check (for load balancers) |
| `GET /stats` | Server statistics |
| `WS /ws` | WebSocket endpoint |

## Project Structure

```
mudrex-ws-proxy/
├── app/
│   ├── __init__.py
│   ├── main.py              # FastAPI entrypoint
│   ├── config.py            # Configuration
│   ├── websocket/           # Client WebSocket handling
│   │   ├── handler.py       # Message handler
│   │   ├── manager.py       # Connection manager
│   │   └── models.py        # Pydantic models
│   ├── upstream/            # Bybit connection
│   │   ├── bybit_client.py  # WebSocket client
│   │   ├── pool.py          # Connection pool
│   │   └── transformer.py   # Data transformation
│   ├── redis/               # Redis integration
│   │   ├── client.py        # Connection pool
│   │   ├── pubsub.py        # Pub/Sub manager
│   │   └── subscriptions.py # Reference counting
│   └── utils/
│       └── logging.py       # Logging setup
├── Dockerfile
├── railway.toml
├── requirements.txt
└── .env.example
```

## Production Features

- ✅ **Automatic Reconnection**: Bybit client reconnects with exponential backoff
- ✅ **Heartbeat**: Keeps Bybit connections alive
- ✅ **Multiplexing**: One Bybit connection serves many clients
- ✅ **Reference Counting**: Only subscribe to symbols that clients need
- ✅ **Idle Cleanup**: Disconnects inactive clients
- ✅ **Health Checks**: Ready for load balancer health probes
- ✅ **Multi-stage Docker**: Optimized container size
- ✅ **JSON Logging**: Production-ready structured logs
- ✅ **Parallel fan-out**: Message delivery to 1000+ clients without serial delay
- ✅ **Rate limiting**: Per-client receive rate limit to protect from abusive clients

### Production deployment (1000+ users)

- **Redis pool**: Set `REDIS_MAX_CONNECTIONS=50` (or 50–100) so subscribe/unsubscribe bursts and subscription-manager ops have enough connections under load.
- **Single worker**: The app runs with one Uvicorn worker by design; WebSockets require a single process for shared connection state. Do not increase `--workers` for the WebSocket server.
- **Load testing**: Before going live at scale, run a load test (e.g. 1000 concurrent connections, subscribe to the same channel, measure latency and drops) using a tool like [Artillery](https://www.artillery.io/) or a custom script.

## License

MIT License - See [LICENSE](LICENSE) for details.

---

Built with ❤️ for the Mudrex community.
