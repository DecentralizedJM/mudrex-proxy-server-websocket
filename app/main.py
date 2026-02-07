"""
Mudrex Futures WebSocket Proxy Server
=====================================
Production-ready FastAPI application that proxies Bybit V5 WebSocket
data to clients with Mudrex branding.

Author: DecentralizedJM
"""
import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from fastapi import FastAPI, WebSocket, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from app.config import settings
from app.utils.logging import setup_logging, get_logger
from app.redis import get_redis, close_redis, check_redis_health, PubSubManager, SubscriptionManager
from app.upstream import UpstreamPool
from app.websocket import handle_client_websocket, ConnectionManager

# Initialize logging
logger = setup_logging()

# #region agent log
import pathlib
_DEBUG_LOG_DIR = pathlib.Path(__file__).resolve().parent.parent / ".cursor"
DEBUG_LOG_PATH = _DEBUG_LOG_DIR / "debug.log"
def _debug_log(location: str, message: str, data: dict = None, hypothesis_id: str = None):
    try:
        _DEBUG_LOG_DIR.mkdir(parents=True, exist_ok=True)
        payload = {"id": f"log_{int(time.time()*1000)}", "timestamp": int(time.time() * 1000), "location": location, "message": message, "data": data or {}}
        if hypothesis_id:
            payload["hypothesisId"] = hypothesis_id
        with open(DEBUG_LOG_PATH, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
# #endregion

# =============================================================================
# Global State
# =============================================================================

# Connection manager for WebSocket clients
connection_manager = ConnectionManager()

# Upstream connection pool (Bybit)
upstream_pool: UpstreamPool = None

# Redis pub/sub manager
pubsub_manager: PubSubManager = None

# Subscription reference counter
subscription_manager: SubscriptionManager = None

# Background tasks
_cleanup_task: asyncio.Task = None


# =============================================================================
# Lifecycle Management
# =============================================================================

@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifespan manager.
    Handles startup and shutdown of all components.
    """
    global upstream_pool, pubsub_manager, subscription_manager, _cleanup_task
    
    # =========================================================================
    # Startup
    # =========================================================================
    t0 = time.perf_counter()
    # #region agent log
    _debug_log("main.py:lifespan", "startup_start", {"PORT_env": os.environ.get("PORT"), "settings_PORT": settings.PORT, "HOST": settings.HOST, "t0": t0}, "H1")
    _debug_log("main.py:lifespan", "startup_start", {"t0": t0}, "H2")
    # #endregion
    logger.info("=" * 60)
    logger.info("Starting Mudrex WebSocket Proxy Server")
    logger.info(f"Environment: {settings.ENVIRONMENT}")
    logger.info(f"Debug: {settings.DEBUG}")
    logger.info("=" * 60)
    
    try:
        # Initialize Redis
        logger.info("Connecting to Redis...")
        redis = await get_redis()
        t1 = time.perf_counter()
        # #region agent log
        _debug_log("main.py:lifespan", "after_redis", {"elapsed": t1 - t0}, "H2")
        # #endregion
        
        # Check Redis health
        if not await check_redis_health():
            raise RuntimeError("Redis health check failed")
        logger.info("Redis connection established")
        
        # Initialize managers
        subscription_manager = SubscriptionManager(redis)
        pubsub_manager = PubSubManager()
        
        # Start pub/sub listener
        await pubsub_manager.start()
        t2 = time.perf_counter()
        # #region agent log
        _debug_log("main.py:lifespan", "after_pubsub_start", {"elapsed": t2 - t0}, "H2")
        # #endregion
        logger.info("PubSub manager started")
        
        # Initialize upstream pool
        upstream_pool = UpstreamPool(pubsub_manager, subscription_manager)
        await upstream_pool.start()
        t3 = time.perf_counter()
        # #region agent log
        _debug_log("main.py:lifespan", "after_upstream_start", {"elapsed": t3 - t0}, "H2")
        # #endregion
        logger.info("Upstream pool started")
        
        # Start background cleanup task
        _cleanup_task = asyncio.create_task(_periodic_cleanup())
        
        # Clean up any stale subscriptions from previous run
        await subscription_manager.cleanup_stale()
        
        t4 = time.perf_counter()
        # #region agent log
        _debug_log("main.py:lifespan", "before_yield", {"total_startup_sec": t4 - t0}, "H2")
        # #endregion
        logger.info("Server startup complete")
        logger.info("=" * 60)
        
        yield
        
    except Exception as e:
        logger.error(f"Startup failed: {e}")
        raise
    
    # =========================================================================
    # Shutdown
    # =========================================================================
    logger.info("Shutting down...")

    # Drain clients: close all WebSockets so handlers run cleanup (subscriptions, etc.)
    clients = connection_manager.get_all_clients()
    if clients:
        logger.info(f"Closing {len(clients)} client connections...")
        for client_id, state in clients.items():
            try:
                await state.websocket.close(code=1001, reason="Server going away")
            except Exception:
                pass
        await asyncio.sleep(2.0)  # Allow handlers to run _cleanup

    # Cancel background tasks
    if _cleanup_task:
        _cleanup_task.cancel()
        try:
            await _cleanup_task
        except asyncio.CancelledError:
            pass

    # Stop upstream pool
    if upstream_pool:
        await upstream_pool.stop()
        logger.info("Upstream pool stopped")
    
    # Stop pub/sub
    if pubsub_manager:
        await pubsub_manager.stop()
        logger.info("PubSub manager stopped")
    
    # Close Redis
    await close_redis()
    logger.info("Redis connection closed")
    
    logger.info("Shutdown complete")


async def _periodic_cleanup():
    """Background task for periodic maintenance."""
    while True:
        try:
            await asyncio.sleep(60)  # Run every minute
            
            # Clean up idle connections
            cleaned = await connection_manager.cleanup_idle()
            
            if cleaned > 0:
                logger.info(f"Periodic cleanup: removed {cleaned} idle connections")
                
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.error(f"Cleanup task error: {e}")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="Mudrex Futures WebSocket",
    description="Real-time futures market data stream",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# WebSocket Endpoint
# =============================================================================

@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """
    Main WebSocket endpoint for clients.
    
    Protocol:
    - Send: {"op": "subscribe", "args": ["ticker:BTCUSDT", "ticker:ETHUSDT"]}
    - Send: {"op": "unsubscribe", "args": ["ticker:BTCUSDT"]}
    - Send: {"op": "ping"}
    - Receive: {"stream": "mudrex.futures.ticker.BTCUSDT", "type": "update", "data": {...}}
    """
    # #region agent log
    _debug_log("main.py:websocket_endpoint", "ws_entry", {"ts": time.perf_counter()}, "H5")
    # #endregion
    await handle_client_websocket(
        websocket=websocket,
        manager=connection_manager,
        upstream_pool=upstream_pool,
        pubsub=pubsub_manager,
        subscriptions=subscription_manager,
    )
    # #region agent log
    _debug_log("main.py:websocket_endpoint", "ws_exit", {"ts": time.perf_counter()}, "H5")
    # #endregion


# =============================================================================
# HTTP Endpoints
# =============================================================================

@app.get("/health")
async def health():
    """
    Health check endpoint for load balancers and monitoring.
    """
    # #region agent log
    health_t0 = time.perf_counter()
    _debug_log("main.py:health", "health_entry", {"ts": health_t0}, "H4")
    # #endregion
    redis_healthy = await check_redis_health()
    listener_healthy = pubsub_manager.is_listener_healthy() if pubsub_manager else True
    ok = redis_healthy and listener_healthy
    status = "healthy" if ok else "degraded"
    # #region agent log
    health_t1 = time.perf_counter()
    _debug_log("main.py:health", "health_exit", {"duration_sec": health_t1 - health_t0, "ts": health_t1}, "H4")
    # #endregion
    return JSONResponse(
        status_code=200 if ok else 503,
        content={
            "status": status,
            "redis": "connected" if redis_healthy else "disconnected",
            "pubsub_listener": "ok" if listener_healthy else "degraded",
            "connections": connection_manager.active_count,
        }
    )


@app.get("/stats")
async def stats():
    """
    Server statistics endpoint.
    """
    return {
        "connections": connection_manager.get_stats(),
        "upstream": upstream_pool.get_stats() if upstream_pool else {},
        "pubsub": {
            "total_subscriptions": pubsub_manager.get_total_subscriptions() if pubsub_manager else 0,
            "active_channels": len(pubsub_manager.get_active_channels()) if pubsub_manager else 0,
        },
    }


@app.get("/")
async def root():
    """Root endpoint with API information."""
    return {
        "name": "Mudrex Futures WebSocket",
        "version": "1.0.0",
        "websocket": "/ws",
        "health": "/health",
        "docs": "/docs" if settings.DEBUG else "disabled",
    }


# =============================================================================
# Error Handlers
# =============================================================================

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    logger.error(f"Unhandled exception: {exc}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"error": "Internal server error"}
    )


# =============================================================================
# Development Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn
    
    uvicorn.run(
        "app.main:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
        log_level="debug" if settings.DEBUG else "info",
    )
