"""
Redis Pub/Sub manager for message fan-out across server instances.
Handles subscription multiplexing and message distribution.
"""
import asyncio
import json
from typing import Callable, Dict, Set, Optional, Any
from contextlib import asynccontextmanager
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from app.redis.client import get_redis, RedisRetryMixin
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("redis.pubsub")

# Type alias for message callbacks: (channel, data, pre_serialized|None)
# When pre_serialized is set, callback should send_text(pre_serialized) to avoid N× json.dumps.
MessageCallback = Callable[[str, Dict[str, Any], Optional[str]], Any]


class PubSubManager(RedisRetryMixin):
    """
    Manages Redis Pub/Sub for distributing messages to connected clients.
    
    Features:
    - Automatic subscription management
    - Message fan-out to multiple callbacks per channel
    - Graceful error handling and reconnection
    - Efficient channel multiplexing
    """
    
    # Channel prefix for Mudrex streams
    CHANNEL_PREFIX = "mudrex:stream:"
    
    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        
        # Channel -> {client_id -> callback}
        self._subscriptions: Dict[str, Dict[int, MessageCallback]] = {}
        
        # Listener task
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False
        
        # Lock for thread-safe subscription management
        self._lock = asyncio.Lock()

        # Listener resilience: auto-restart on unexpected exceptions
        self._listener_retries = 0
        self._listener_degraded = False
        self.LISTENER_MAX_RETRIES = 10
        self.LISTENER_RETRY_SLEEP = 2.0

    async def start(self):
        """Start the Pub/Sub manager (listener starts on first subscription)."""
        if self._running:
            logger.warning("PubSubManager already running")
            return
        
        self._redis = await get_redis()
        self._pubsub = self._redis.pubsub()
        self._running = True
        self._listener_started = False
        
        # Note: Listener task starts when first subscription is made
        # This avoids "pubsub connection not set" error
        logger.info("PubSubManager started")

    async def stop(self):
        """Stop the Pub/Sub manager gracefully."""
        self._running = False
        
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
            self._listener_task = None
        
        if self._pubsub:
            await self._pubsub.close()
            self._pubsub = None
        
        self._subscriptions.clear()
        logger.info("PubSubManager stopped")

    def _make_channel(self, stream_type: str, symbol: str) -> str:
        """Create a Redis channel name."""
        return f"{self.CHANNEL_PREFIX}{stream_type}:{symbol}"

    async def subscribe(
        self,
        stream_type: str,
        symbol: str,
        client_id: int,
        callback: MessageCallback
    ):
        """
        Subscribe a client to a stream.
        
        Args:
            stream_type: Type of stream (e.g., "ticker", "kline")
            symbol: Trading symbol (e.g., "BTCUSDT")
            client_id: Unique client identifier
            callback: Async function to call when messages arrive
        """
        channel = self._make_channel(stream_type, symbol)
        
        async with self._lock:
            is_new_channel = channel not in self._subscriptions
            
            if is_new_channel:
                self._subscriptions[channel] = {}
                
                # Subscribe to the Redis channel
                if self._pubsub:
                    await self._pubsub.subscribe(channel)
                    logger.debug(f"Subscribed to Redis channel: {channel}")
                    
                    # Start listener on first subscription
                    if not self._listener_started:
                        self._listener_task = asyncio.create_task(self._listen())
                        self._listener_started = True
                        logger.debug("Started PubSub listener on first subscription")
            
            # Register the callback
            self._subscriptions[channel][client_id] = callback
            logger.debug(f"Client {client_id} subscribed to {channel}")

    async def unsubscribe(
        self,
        stream_type: str,
        symbol: str,
        client_id: int
    ):
        """
        Unsubscribe a client from a stream.
        """
        channel = self._make_channel(stream_type, symbol)
        
        async with self._lock:
            if channel not in self._subscriptions:
                return
            
            # Remove the client callback
            self._subscriptions[channel].pop(client_id, None)
            
            # If no more clients, unsubscribe from Redis channel
            if not self._subscriptions[channel]:
                del self._subscriptions[channel]
                
                if self._pubsub:
                    await self._pubsub.unsubscribe(channel)
                    logger.debug(f"Unsubscribed from Redis channel: {channel}")

    async def unsubscribe_client(self, client_id: int):
        """
        Remove a client from all subscriptions.
        Called when a client disconnects.
        """
        async with self._lock:
            channels_to_remove = []
            
            for channel, clients in self._subscriptions.items():
                if client_id in clients:
                    del clients[client_id]
                    
                    if not clients:
                        channels_to_remove.append(channel)
            
            # Unsubscribe from empty channels
            for channel in channels_to_remove:
                del self._subscriptions[channel]
                if self._pubsub:
                    await self._pubsub.unsubscribe(channel)
                    logger.debug(f"Unsubscribed from Redis channel (empty): {channel}")

    async def publish(self, stream_type: str, symbol: str, data: Dict[str, Any]):
        """
        Publish a message to a stream.
        Called by the upstream manager when Bybit sends data.
        """
        channel = self._make_channel(stream_type, symbol)
        
        try:
            if self._redis:
                message = json.dumps(data)
                await self._redis.publish(channel, message)
        except RedisError as e:
            logger.error(f"Failed to publish to {channel}: {e}")

    def is_listener_healthy(self) -> bool:
        """True if listener task is running and not degraded."""
        if self._listener_degraded:
            return False
        if self._listener_task is None:
            return True  # Not started yet
        return not self._listener_task.done()

    async def _listen(self):
        """
        Background task that listens for Redis Pub/Sub messages.
        Auto-restarts on unexpected exceptions so the listener never silently dies.
        """
        logger.info("PubSub listener started")

        while self._running and self._pubsub:
            try:
                async for message in self._pubsub.listen():
                    if not self._running:
                        break
                    if message and message.get("type") == "message":
                        await self._handle_message(message)

            except asyncio.CancelledError:
                logger.info("PubSub listener cancelled")
                break
            except RedisError as e:
                logger.error(f"PubSub Redis error: {e}")
                await asyncio.sleep(1.0)
            except Exception as e:
                self._listener_retries += 1
                logger.error(f"PubSub listener error (retry {self._listener_retries}/{self.LISTENER_MAX_RETRIES}): {e}", exc_info=True)
                if self._listener_retries >= self.LISTENER_MAX_RETRIES:
                    self._listener_degraded = True
                    logger.critical("PubSub listener degraded after max retries; no message delivery until restart")
                    break
                await asyncio.sleep(self.LISTENER_RETRY_SLEEP)

        logger.info("PubSub listener stopped")

    async def _handle_message(self, message: Dict):
        """Process an incoming Pub/Sub message."""
        channel = message.get("channel", "")
        raw_data = message.get("data", "")
        
        try:
            data = json.loads(raw_data)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON in message: {raw_data[:100]}")
            return
        
        # Get callbacks for this channel
        callbacks = self._subscriptions.get(channel, {})
        if not callbacks:
            return

        # Pre-serialize once so 1000 clients don't each call json.dumps
        pre_serialized = json.dumps(data)

        # Build coroutines for parallel fan-out (avoid sequential delay for 1000+ clients)
        timeout = getattr(settings, "FANOUT_CALLBACK_TIMEOUT", 5.0)

        async def _invoke(client_id: int, cb: MessageCallback):
            result = cb(channel, data, pre_serialized)
            if asyncio.iscoroutine(result):
                await result

        async def _invoke_with_timeout(client_id: int, cb: MessageCallback):
            await asyncio.wait_for(_invoke(client_id, cb), timeout=timeout)

        coros = [_invoke_with_timeout(cid, cb) for cid, cb in list(callbacks.items())]
        results = await asyncio.gather(*coros, return_exceptions=True)
        for client_id, result in zip(callbacks.keys(), results):
            if isinstance(result, Exception):
                if isinstance(result, asyncio.TimeoutError):
                    logger.warning(f"Fan-out timeout for client {client_id} (>{timeout}s)")
                else:
                    logger.error(f"Callback error for client {client_id}: {result}")

    def get_subscription_count(self, stream_type: str, symbol: str) -> int:
        """Get the number of clients subscribed to a stream."""
        channel = self._make_channel(stream_type, symbol)
        return len(self._subscriptions.get(channel, {}))

    def get_total_subscriptions(self) -> int:
        """Get total number of active subscriptions."""
        return sum(len(clients) for clients in self._subscriptions.values())

    def get_active_channels(self) -> Set[str]:
        """Get set of all active channel names."""
        return set(self._subscriptions.keys())
