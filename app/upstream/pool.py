"""
Upstream connection pool manager.
Manages Bybit WebSocket connections and routes messages to Redis.
"""
import asyncio
from typing import Dict, Set, Optional, Any
from app.upstream.bybit_client import BybitWebSocketClient
from app.upstream.transformer import transform_message
from app.redis.pubsub import PubSubManager
from app.redis.subscriptions import SubscriptionManager
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("upstream.pool")


class UpstreamPool:
    """
    Manages a pool of Bybit upstream connections.
    
    Features:
    - Single connection per category (linear, inverse, spot)
    - Automatic subscription management based on client demand
    - Message transformation and publishing to Redis
    - Graceful error handling and recovery
    """
    
    def __init__(self, pubsub: PubSubManager, subscriptions: SubscriptionManager):
        self.pubsub = pubsub
        self.subscriptions = subscriptions
        
        # Bybit clients by category
        self._clients: Dict[str, BybitWebSocketClient] = {}
        
        # Track which symbols we've subscribed to on Bybit
        self._upstream_subscribed: Dict[str, Set[str]] = {}  # category -> set of topics
        
        # Lock for subscription management
        self._lock = asyncio.Lock()
        
        # Running state
        self._running = False

    async def start(self):
        """Start the upstream pool."""
        if self._running:
            return
            
        self._running = True
        logger.info("Starting upstream pool")
        
        # Create default linear client
        await self._create_client("linear")
        
        logger.info("Upstream pool started")

    async def stop(self):
        """Stop all upstream connections."""
        self._running = False
        
        for category, client in self._clients.items():
            logger.info(f"Stopping {category} client")
            await client.stop()
        
        self._clients.clear()
        self._upstream_subscribed.clear()
        logger.info("Upstream pool stopped")

    async def _create_client(self, category: str) -> BybitWebSocketClient:
        """Create a new Bybit client for a category."""
        if category in self._clients:
            return self._clients[category]
        
        client = BybitWebSocketClient(
            on_message=self._on_upstream_message,
            category=category,
            testnet=settings.BYBIT_TESTNET
        )
        
        self._clients[category] = client
        self._upstream_subscribed[category] = set()
        
        await client.start()
        return client

    async def ensure_subscribed(
        self,
        stream_type: str,
        symbol: str,
        category: str = "linear"
    ):
        """
        Ensure we're subscribed to a symbol on Bybit.
        Called when a client subscribes to a stream.
        
        Args:
            stream_type: Type of stream (e.g., "ticker")
            symbol: Trading symbol (e.g., "BTCUSDT")
            category: Bybit category (e.g., "linear")
        """
        topic = self._make_topic(stream_type, symbol)
        
        async with self._lock:
            # Check if already subscribed upstream
            if category not in self._upstream_subscribed:
                self._upstream_subscribed[category] = set()
            
            if topic in self._upstream_subscribed[category]:
                return
            
            # Ensure client exists
            if category not in self._clients:
                await self._create_client(category)
            
            client = self._clients[category]
            
            # Subscribe on Bybit
            await client.subscribe([topic])
            self._upstream_subscribed[category].add(topic)
            
            # Mark in Redis
            await self.subscriptions.mark_upstream_subscribed(stream_type, symbol)
            
            logger.info(f"Subscribed upstream: {topic}")

    async def ensure_unsubscribed(
        self,
        stream_type: str,
        symbol: str,
        category: str = "linear"
    ):
        """
        Unsubscribe from a symbol on Bybit if no more clients need it.
        Called when a client unsubscribes.
        """
        topic = self._make_topic(stream_type, symbol)
        
        async with self._lock:
            # Check if anyone still needs this subscription
            count = await self.subscriptions.get_count(stream_type, symbol)
            
            if count > 0:
                # Other clients still need it
                return
            
            # Unsubscribe from Bybit
            if category in self._clients and topic in self._upstream_subscribed.get(category, set()):
                client = self._clients[category]
                await client.unsubscribe([topic])
                self._upstream_subscribed[category].discard(topic)
                
                # Mark in Redis
                await self.subscriptions.mark_upstream_unsubscribed(stream_type, symbol)
                
                logger.info(f"Unsubscribed upstream: {topic}")

    async def _on_upstream_message(self, message: Dict[str, Any]):
        """
        Handle message from Bybit.
        Transform and publish to Redis.
        """
        try:
            # Transform to Mudrex format
            transformed = transform_message(message)
            
            if transformed is None:
                return
            
            # Extract stream info for routing
            stream = transformed.get("stream", "")
            parts = stream.split(".")
            
            # Expected format: mudrex.futures.{type}.{symbol} or mudrex.futures.{type}.{interval}.{symbol}
            if len(parts) >= 4:
                stream_type = parts[2]  # ticker, kline, trade
                symbol = parts[-1]      # Always last
                
                # Publish to Redis
                await self.pubsub.publish(stream_type, symbol, transformed)
                
        except Exception as e:
            logger.error(f"Error handling upstream message: {e}")

    def _make_topic(self, stream_type: str, symbol: str) -> str:
        """Create Bybit topic from stream type and symbol."""
        topic_map = {
            "ticker": f"tickers.{symbol}",
            "trade": f"publicTrade.{symbol}",
            "kline": f"kline.1.{symbol}",  # Default to 1m klines
        }
        return topic_map.get(stream_type, f"tickers.{symbol}")

    def make_topic_for_kline(self, symbol: str, interval: str) -> str:
        """Create Bybit kline topic with interval."""
        # Map standard intervals to Bybit format
        interval_map = {
            "1m": "1", "3m": "3", "5m": "5", "15m": "15", "30m": "30",
            "1h": "60", "2h": "120", "4h": "240", "6h": "360", "12h": "720",
            "1d": "D", "1w": "W", "1M": "M"
        }
        bybit_interval = interval_map.get(interval, interval)
        return f"kline.{bybit_interval}.{symbol}"

    def get_stats(self) -> Dict[str, Any]:
        """Get pool statistics."""
        stats = {
            "running": self._running,
            "clients": {},
        }
        
        for category, client in self._clients.items():
            stats["clients"][category] = client.get_stats()
        
        return stats

    async def get_subscribed_symbols(self, category: str = "linear") -> Set[str]:
        """Get all symbols currently subscribed on upstream."""
        topics = self._upstream_subscribed.get(category, set())
        
        symbols = set()
        for topic in topics:
            # Extract symbol from topic
            if topic.startswith("tickers."):
                symbols.add(topic.replace("tickers.", ""))
            elif topic.startswith("publicTrade."):
                symbols.add(topic.replace("publicTrade.", ""))
            elif topic.startswith("kline."):
                parts = topic.split(".")
                if len(parts) >= 3:
                    symbols.add(parts[2])
        
        return symbols
