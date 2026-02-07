"""
Mudrex Futures WebSocket Proxy Server – standalone entrypoint
=============================================================
Replaces FastAPI/uvicorn with a single-process ``websockets.serve()`` server.

* HTTP requests (``/health``, ``/ready``, ``/stats``, ``/``) are handled
  inside ``process_request`` and never reach the WebSocket handler.
* Only ``/ws`` proceeds through the WebSocket handshake.
* Startup / shutdown mirrors the old FastAPI lifespan in ``app/main.py``.

Run:
    python -m app.standalone_server
"""

import asyncio
import json
import os
import signal
import time

from websockets.legacy.server import serve

from app.config import settings
from app.utils.logging import setup_logging, get_logger
from app.redis import (
    get_redis,
    close_redis,
    check_redis_health,
    PubSubManager,
    SubscriptionManager,
)
from app.upstream import UpstreamPool
from app.websocket.manager import ConnectionManager
from app.websocket.handler import ClientHandler
from app.websocket.adapter import WebSocketAdapter

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logger = setup_logging()

# ---------------------------------------------------------------------------
# Global state (same role as the globals in the old app/main.py)
# ---------------------------------------------------------------------------
connection_manager = ConnectionManager()
upstream_pool: UpstreamPool = None
pubsub_manager: PubSubManager = None
subscription_manager: SubscriptionManager = None
_cleanup_task: asyncio.Task = None


# =========================================================================
# process_request  –  handle plain HTTP before the WS handshake
# =========================================================================
# Legacy API: process_request(path: str, request_headers: Headers)
#   -> (status, headers, body)   to abort handshake with an HTTP response
#   -> None                      to continue with WebSocket

async def process_request(path, request_headers):
    """Route HTTP requests; return None only for /ws so the WS upgrade proceeds."""

    # Strip query string for routing
    route = path.split("?")[0]

    if route == "/ready":
        return _json_response(200, {"ok": True})

    if route == "/health":
        return await _health_response()

    if route == "/stats":
        return _stats_response()

    if route == "/":
        return _json_response(200, {
            "name": "Mudrex Futures WebSocket",
            "version": "1.0.0",
            "websocket": "/ws",
            "health": "/health",
            "ready": "/ready",
        })

    if route == "/ws":
        return None  # proceed with WebSocket handshake

    # Everything else → 404
    return _json_response(404, {"error": "Not found"})


# ---------------------------------------------------------------------------
# HTTP response helpers (legacy tuple format)
# ---------------------------------------------------------------------------

def _json_response(status_code, body_dict):
    """Return the 3-tuple ``(status, headers, body)`` expected by the legacy API."""
    body = json.dumps(body_dict).encode()
    headers = [("Content-Type", "application/json"), ("Content-Length", str(len(body)))]
    return (status_code, headers, body)


async def _health_response():
    try:
        redis_healthy = await check_redis_health()
        listener_healthy = pubsub_manager.is_listener_healthy() if pubsub_manager else True
        ok = redis_healthy and listener_healthy
        status_str = "healthy" if ok else "degraded"
        code = 200 if ok else 503
        return _json_response(code, {
            "status": status_str,
            "redis": "connected" if redis_healthy else "disconnected",
            "pubsub_listener": "ok" if listener_healthy else "degraded",
            "connections": connection_manager.active_count,
        })
    except Exception as exc:
        logger.error(f"[HEALTH] error: {exc}", exc_info=True)
        return _json_response(503, {"status": "error", "detail": str(exc)})


def _stats_response():
    return _json_response(200, {
        "connections": connection_manager.get_stats(),
        "upstream": upstream_pool.get_stats() if upstream_pool else {},
        "pubsub": {
            "total_subscriptions": pubsub_manager.get_total_subscriptions() if pubsub_manager else 0,
            "active_channels": len(pubsub_manager.get_active_channels()) if pubsub_manager else 0,
        },
    })


# =========================================================================
# WebSocket handler  (called only for /ws after handshake completes)
# =========================================================================

async def ws_handler(websocket):
    """Handle one WebSocket connection from a client."""
    wrapped = WebSocketAdapter(websocket)

    client = await connection_manager.connect(wrapped)
    if client is None:
        await wrapped.close(code=4003, reason="Connection limit reached")
        return

    handler = ClientHandler(
        client=client,
        connection_manager=connection_manager,
        upstream_pool=upstream_pool,
        pubsub=pubsub_manager,
        subscriptions=subscription_manager,
    )

    await handler.handle()


# =========================================================================
# Startup / shutdown
# =========================================================================

async def startup():
    """Initialize Redis, PubSub, upstream pool – same as old lifespan startup."""
    global upstream_pool, pubsub_manager, subscription_manager, _cleanup_task

    logger.info("=" * 60)
    logger.info("Starting Mudrex WebSocket Proxy Server")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Debug: {settings.DEBUG}")
    logger.info("=" * 60)

    # Redis
    logger.info("Connecting to Redis...")
    redis = await get_redis()
    if not await check_redis_health():
        raise RuntimeError("Redis health check failed")
    logger.info("Redis connection established")

    # Managers
    subscription_manager = SubscriptionManager(redis)
    pubsub_manager = PubSubManager()
    await pubsub_manager.start()
    logger.info("PubSub manager started")

    # Upstream
    upstream_pool = UpstreamPool(pubsub_manager, subscription_manager)
    await upstream_pool.start()
    logger.info("Upstream pool started")

    # Background cleanup
    _cleanup_task = asyncio.create_task(_periodic_cleanup())

    await subscription_manager.cleanup_stale()
    logger.info("Server startup complete")
    logger.info("=" * 60)


async def shutdown():
    """Drain clients, stop components, close Redis."""
    logger.info("Shutting down...")

    # Close all client connections
    clients = connection_manager.get_all_clients()
    if clients:
        logger.info(f"Closing {len(clients)} client connections...")
        for client_id, state in clients.items():
            try:
                await state.websocket.close(code=1001, reason="Server going away")
            except Exception:
                pass
        await asyncio.sleep(2.0)

    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

    if upstream_pool:
        await upstream_pool.stop()
        logger.info("Upstream pool stopped")

    if pubsub_manager:
        await pubsub_manager.stop()
        logger.info("PubSub manager stopped")

    await close_redis()
    logger.info("Redis connection closed")
    logger.info("Shutdown complete")


async def _periodic_cleanup():
    """Background task for periodic maintenance."""
    while True:
        try:
            await asyncio.sleep(60)
            cleaned = await connection_manager.cleanup_idle()
            if cleaned > 0:
                logger.info(f"Periodic cleanup: removed {cleaned} idle connections")
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup task error: {e}")


# =========================================================================
# Main
# =========================================================================

async def main():
    port = int(os.environ.get("PORT", settings.PORT))

    await startup()

    stop = asyncio.get_running_loop().create_future()

    # Graceful shutdown on SIGTERM / SIGINT
    def _signal_handler():
        if not stop.done():
            stop.set_result(None)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _signal_handler)

    logger.info(f"Listening on ws://0.0.0.0:{port}")
    logger.info(f"Health check at http://0.0.0.0:{port}/health")

    async with serve(
        ws_handler,
        "0.0.0.0",
        port,
        process_request=process_request,
        ping_interval=20,
        ping_timeout=10,
    ):
        await stop  # blocks until signal

    await shutdown()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Server stopped by user")
