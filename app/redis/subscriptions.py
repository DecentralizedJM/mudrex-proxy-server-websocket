"""
Subscription reference counting in Redis.
Tracks how many clients are subscribed to each symbol.
Used to manage upstream Bybit connections efficiently.
"""
import asyncio
from typing import Dict, Set, Optional
import redis.asyncio as aioredis
from redis.exceptions import RedisError
from app.redis.client import get_redis, RedisRetryMixin
from app.utils.logging import get_logger

logger = get_logger("redis.subscriptions")


class SubscriptionManager(RedisRetryMixin):
    """
    Manages subscription reference counts in Redis.
    
    This allows the proxy to know:
    - Which symbols have active subscribers
    - When to subscribe/unsubscribe from Bybit upstream
    - Global subscription state across multiple server instances
    """
    
    # Redis key for the subscription hash
    HASH_KEY = "mudrex:subscriptions"
    
    # Redis key for tracking upstream subscriptions
    UPSTREAM_KEY = "mudrex:upstream:subscribed"
    
    def __init__(self, redis: Optional[aioredis.Redis] = None):
        self._redis = redis
        self._lock = asyncio.Lock()

    async def _get_redis(self) -> aioredis.Redis:
        if self._redis is None:
            self._redis = await get_redis()
        return self._redis

    async def increment(self, stream_type: str, symbol: str) -> int:
        """
        Increment the subscription count for a symbol.
        Returns the new count.
        
        Args:
            stream_type: Type of stream (e.g., "ticker")
            symbol: Trading symbol (e.g., "BTCUSDT")
        
        Returns:
            New subscription count for this symbol
        """
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            count = await redis.hincrby(self.HASH_KEY, key, 1)
            logger.debug(f"Subscription count for {key}: {count}")
            return count
        except RedisError as e:
            logger.error(f"Failed to increment subscription for {key}: {e}")
            return 1  # Assume at least 1 subscriber

    async def decrement(self, stream_type: str, symbol: str) -> int:
        """
        Decrement the subscription count for a symbol.
        Removes the key if count reaches 0.
        Returns the new count.
        """
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            
            async with self._lock:
                count = await redis.hincrby(self.HASH_KEY, key, -1)
                
                if count <= 0:
                    # Remove the key if no subscribers
                    await redis.hdel(self.HASH_KEY, key)
                    count = 0
                    
                    # Also remove from upstream tracking
                    await redis.srem(self.UPSTREAM_KEY, key)
                
                logger.debug(f"Subscription count for {key}: {count}")
                return count
                
        except RedisError as e:
            logger.error(f"Failed to decrement subscription for {key}: {e}")
            return 0

    async def get_count(self, stream_type: str, symbol: str) -> int:
        """Get the current subscription count for a symbol."""
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            count = await redis.hget(self.HASH_KEY, key)
            return int(count) if count else 0
        except RedisError as e:
            logger.error(f"Failed to get subscription count for {key}: {e}")
            return 0

    async def get_all_active(self) -> Dict[str, int]:
        """
        Get all symbols with active subscriptions.
        
        Returns:
            Dict mapping "stream_type:symbol" to subscription count
        """
        try:
            redis = await self._get_redis()
            data = await redis.hgetall(self.HASH_KEY)
            return {k: int(v) for k, v in data.items()}
        except RedisError as e:
            logger.error(f"Failed to get all subscriptions: {e}")
            return {}

    async def get_active_symbols(self, stream_type: str = "ticker") -> Set[str]:
        """
        Get all symbols with active subscriptions for a stream type.
        
        Args:
            stream_type: Type of stream (default: "ticker")
            
        Returns:
            Set of symbol names
        """
        try:
            all_active = await self.get_all_active()
            prefix = f"{stream_type}:"
            return {
                key.replace(prefix, "")
                for key in all_active.keys()
                if key.startswith(prefix)
            }
        except Exception as e:
            logger.error(f"Failed to get active symbols: {e}")
            return set()

    async def mark_upstream_subscribed(self, stream_type: str, symbol: str):
        """Mark a symbol as subscribed on the upstream (Bybit) connection."""
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            await redis.sadd(self.UPSTREAM_KEY, key)
        except RedisError as e:
            logger.error(f"Failed to mark upstream subscribed for {key}: {e}")

    async def mark_upstream_unsubscribed(self, stream_type: str, symbol: str):
        """Mark a symbol as unsubscribed from upstream."""
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            await redis.srem(self.UPSTREAM_KEY, key)
        except RedisError as e:
            logger.error(f"Failed to mark upstream unsubscribed for {key}: {e}")

    async def is_upstream_subscribed(self, stream_type: str, symbol: str) -> bool:
        """Check if a symbol is already subscribed on upstream."""
        key = f"{stream_type}:{symbol}"
        
        try:
            redis = await self._get_redis()
            return await redis.sismember(self.UPSTREAM_KEY, key)
        except RedisError as e:
            logger.error(f"Failed to check upstream subscription for {key}: {e}")
            return False

    async def get_upstream_subscribed(self) -> Set[str]:
        """Get all symbols currently subscribed on upstream."""
        try:
            redis = await self._get_redis()
            return await redis.smembers(self.UPSTREAM_KEY)
        except RedisError as e:
            logger.error(f"Failed to get upstream subscriptions: {e}")
            return set()

    async def cleanup_stale(self):
        """
        Clean up stale subscriptions.
        Called periodically or on server restart.
        """
        try:
            redis = await self._get_redis()
            
            # Get all subscriptions
            all_subs = await redis.hgetall(self.HASH_KEY)
            
            # Remove any with count <= 0
            stale_keys = [k for k, v in all_subs.items() if int(v) <= 0]
            
            if stale_keys:
                await redis.hdel(self.HASH_KEY, *stale_keys)
                logger.info(f"Cleaned up {len(stale_keys)} stale subscriptions")
                
        except RedisError as e:
            logger.error(f"Failed to cleanup stale subscriptions: {e}")
