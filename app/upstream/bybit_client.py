"""
Bybit WebSocket client with automatic reconnection and heartbeat.
Production-ready upstream connection manager.
"""
import asyncio
import json
import time
from typing import Callable, Set, Optional, List, Dict, Any
from contextlib import asynccontextmanager
import websockets
from websockets.exceptions import (
    ConnectionClosed, 
    ConnectionClosedError, 
    InvalidStatusCode
)
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("upstream.bybit_client")

# Type for message callback
MessageCallback = Callable[[Dict[str, Any]], Any]


class BybitWebSocketClient:
    """
    Bybit V5 WebSocket client with:
    - Automatic reconnection with exponential backoff
    - Heartbeat/ping mechanism
    - Subscription management
    - Rate limiting awareness
    """
    
    def __init__(
        self,
        on_message: MessageCallback,
        category: str = "linear",
        testnet: bool = False
    ):
        self.on_message = on_message
        self.category = category
        self.testnet = testnet
        
        # Connection state
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._running = False
        self._connected = False
        
        # Subscribed topics
        self._subscribed: Set[str] = set()
        self._pending_subscribe: Set[str] = set()
        
        # Reconnection state
        self._reconnect_delay = settings.BYBIT_RECONNECT_DELAY_INITIAL
        self._reconnect_task: Optional[asyncio.Task] = None
        
        # Tasks
        self._listener_task: Optional[asyncio.Task] = None
        self._heartbeat_task: Optional[asyncio.Task] = None
        
        # Stats
        self._last_message_time = 0
        self._messages_received = 0
        self._reconnect_count = 0

    @property
    def url(self) -> str:
        """Get the appropriate WebSocket URL."""
        if self.testnet:
            return f"wss://stream-testnet.bybit.com/v5/public/{self.category}"
        return f"wss://stream.bybit.com/v5/public/{self.category}"

    @property
    def is_connected(self) -> bool:
        return self._connected and self._ws is not None

    async def start(self):
        """Start the WebSocket client (non-blocking)."""
        if self._running:
            logger.warning("Client already running")
            return
            
        self._running = True
        logger.info(f"Starting Bybit client for {self.category}")
        
        # Run connection in background task so start() returns immediately
        self._connection_task = asyncio.create_task(self._connect_loop())

    async def stop(self):
        """Stop the WebSocket client gracefully."""
        self._running = False
        self._connected = False
        
        # Cancel tasks
        tasks_to_cancel = [
            self._listener_task, 
            self._heartbeat_task, 
            self._reconnect_task,
            getattr(self, '_connection_task', None)
        ]
        for task in tasks_to_cancel:
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        
        # Close WebSocket
        if self._ws:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None
        
        logger.info(f"Bybit client stopped for {self.category}")

    async def _connect_loop(self):
        """Background task that maintains the WebSocket connection."""
        while self._running:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Connection loop error: {e}")
            
            # Handle reconnection
            if self._running:
                await self._schedule_reconnect()

    async def _connect_once(self):
        """Establish a single WebSocket connection."""
        try:
            logger.info(f"Connecting to {self.url}")
            
            self._ws = await websockets.connect(
                self.url,
                ping_interval=None,  # We handle pings ourselves
                ping_timeout=None,
                close_timeout=5.0,
                max_size=10 * 1024 * 1024,  # 10MB max message size
            )
            
            self._connected = True
            self._reconnect_delay = settings.BYBIT_RECONNECT_DELAY_INITIAL
            logger.info(f"Connected to Bybit {self.category}")
            
            # Resubscribe to previous topics
            if self._subscribed:
                await self._resubscribe()
            
            # Start background tasks
            self._listener_task = asyncio.create_task(self._listen())
            self._heartbeat_task = asyncio.create_task(self._heartbeat())
            
            # Wait for listener to complete (disconnect triggers this)
            await self._listener_task
            
        except (ConnectionClosed, ConnectionClosedError) as e:
            logger.warning(f"Connection closed: {e}")
        except InvalidStatusCode as e:
            logger.error(f"Invalid status code: {e}")
        except Exception as e:
            logger.error(f"Connection error: {e}")
        finally:
            self._connected = False

    async def _schedule_reconnect(self):
        """Schedule a reconnection with exponential backoff."""
        self._reconnect_count += 1
        
        logger.info(f"Reconnecting in {self._reconnect_delay:.1f}s (attempt {self._reconnect_count})")
        await asyncio.sleep(self._reconnect_delay)
        
        # Increase delay with exponential backoff
        self._reconnect_delay = min(
            self._reconnect_delay * settings.BYBIT_RECONNECT_DELAY_MULTIPLIER,
            settings.BYBIT_RECONNECT_DELAY_MAX
        )

    async def _listen(self):
        """Listen for incoming WebSocket messages."""
        try:
            async for raw_message in self._ws:
                self._last_message_time = time.time()
                self._messages_received += 1
                
                try:
                    message = json.loads(raw_message)
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    logger.warning(f"Invalid JSON: {raw_message[:200]}")
                except Exception as e:
                    logger.error(f"Message handling error: {e}")
                    
        except (ConnectionClosed, ConnectionClosedError) as e:
            logger.warning(f"Connection closed while listening: {e}")
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"Listener error: {e}")

    async def _handle_message(self, message: Dict[str, Any]):
        """Process an incoming message."""
        # Handle responses to our requests
        if "success" in message:
            if message.get("success"):
                logger.debug(f"Request successful: {message.get('op')}")
            else:
                logger.error(f"Request failed: {message.get('ret_msg')}")
            return
        
        # Handle pong
        if message.get("op") == "pong":
            return
        
        # Forward data messages to callback
        if "topic" in message:
            try:
                result = self.on_message(message)
                if asyncio.iscoroutine(result):
                    await result
            except Exception as e:
                logger.error(f"Callback error: {e}")

    async def _heartbeat(self):
        """Send periodic pings to keep connection alive."""
        try:
            while self._running and self._connected:
                await asyncio.sleep(settings.BYBIT_PING_INTERVAL)
                
                if self._ws and self._connected:
                    try:
                        ping_msg = json.dumps({"op": "ping"})
                        await asyncio.wait_for(
                            self._ws.send(ping_msg),
                            timeout=settings.BYBIT_PING_TIMEOUT
                        )
                        logger.debug("Ping sent")
                    except asyncio.TimeoutError:
                        logger.warning("Ping timeout")
                    except Exception as e:
                        logger.warning(f"Ping failed: {e}")
                        
        except asyncio.CancelledError:
            pass

    async def subscribe(self, topics: List[str]):
        """
        Subscribe to topics.
        
        Args:
            topics: List of topics like ["tickers.BTCUSDT", "tickers.ETHUSDT"]
        """
        # Filter out already subscribed
        new_topics = [t for t in topics if t not in self._subscribed]
        if not new_topics:
            return
        
        # Batch subscriptions (max 10 per request)
        for i in range(0, len(new_topics), settings.BYBIT_MAX_SYMBOLS_PER_SUBSCRIPTION):
            batch = new_topics[i:i + settings.BYBIT_MAX_SYMBOLS_PER_SUBSCRIPTION]
            await self._send_subscribe(batch)
            
            # Track subscribed topics
            self._subscribed.update(batch)
            
            # Small delay between batches to avoid rate limiting
            if i + settings.BYBIT_MAX_SYMBOLS_PER_SUBSCRIPTION < len(new_topics):
                await asyncio.sleep(0.1)

    async def _send_subscribe(self, topics: List[str]):
        """Send subscription request."""
        if not self._ws or not self._connected:
            self._pending_subscribe.update(topics)
            return
            
        msg = json.dumps({
            "op": "subscribe",
            "args": topics
        })
        
        try:
            await self._ws.send(msg)
            logger.info(f"Subscribed to {len(topics)} topics")
        except Exception as e:
            logger.error(f"Subscribe failed: {e}")
            self._pending_subscribe.update(topics)

    async def unsubscribe(self, topics: List[str]):
        """Unsubscribe from topics."""
        topics_to_remove = [t for t in topics if t in self._subscribed]
        if not topics_to_remove:
            return
            
        msg = json.dumps({
            "op": "unsubscribe",
            "args": topics_to_remove
        })
        
        try:
            if self._ws and self._connected:
                await self._ws.send(msg)
            
            self._subscribed.difference_update(topics_to_remove)
            logger.info(f"Unsubscribed from {len(topics_to_remove)} topics")
        except Exception as e:
            logger.error(f"Unsubscribe failed: {e}")

    async def _resubscribe(self):
        """Resubscribe to all topics after reconnection."""
        all_topics = list(self._subscribed | self._pending_subscribe)
        self._pending_subscribe.clear()
        
        if not all_topics:
            return
            
        logger.info(f"Resubscribing to {len(all_topics)} topics")
        
        # Clear and resubscribe
        self._subscribed.clear()
        await self.subscribe(all_topics)

    def get_stats(self) -> Dict[str, Any]:
        """Get client statistics."""
        return {
            "connected": self.is_connected,
            "category": self.category,
            "subscribed_count": len(self._subscribed),
            "messages_received": self._messages_received,
            "reconnect_count": self._reconnect_count,
            "last_message_ago": time.time() - self._last_message_time if self._last_message_time else None,
        }
