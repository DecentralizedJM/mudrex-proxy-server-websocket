"""
Redis connection management with connection pooling.
Production-ready with retry logic and health checks.
"""
import asyncio
from typing import Optional
import redis.asyncio as aioredis
from redis.asyncio.connection import ConnectionPool
from redis.exceptions import RedisError, ConnectionError as RedisConnectionError
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("redis.client")

# Global connection pool
_pool: Optional[ConnectionPool] = None
_client: Optional[aioredis.Redis] = None


async def get_redis_pool() -> ConnectionPool:
    """
    Get or create the Redis connection pool.
    Uses a singleton pattern for connection reuse.
    """
    global _pool
    
    if _pool is None:
        logger.info(f"Creating Redis connection pool (max_connections={settings.REDIS_MAX_CONNECTIONS})")
        _pool = ConnectionPool.from_url(
            settings.REDIS_URL,
            max_connections=settings.REDIS_MAX_CONNECTIONS,
            socket_timeout=settings.REDIS_SOCKET_TIMEOUT,
            retry_on_timeout=settings.REDIS_RETRY_ON_TIMEOUT,
            decode_responses=True,
            encoding="utf-8",
        )
    
    return _pool


async def get_redis() -> aioredis.Redis:
    """
    Get a Redis client instance from the connection pool.
    """
    global _client
    
    if _client is None:
        pool = await get_redis_pool()
        _client = aioredis.Redis(connection_pool=pool)
        logger.info("Redis client initialized")
    
    return _client


async def close_redis():
    """
    Close the Redis connection pool gracefully.
    Should be called during application shutdown.
    """
    global _pool, _client
    
    if _client is not None:
        await _client.close()
        _client = None
        logger.info("Redis client closed")
    
    if _pool is not None:
        await _pool.disconnect()
        _pool = None
        logger.info("Redis connection pool closed")


async def check_redis_health() -> bool:
    """
    Check if Redis is healthy and responsive.
    Returns True if Redis is available, False otherwise.
    """
    try:
        client = await get_redis()
        result = await asyncio.wait_for(client.ping(), timeout=2.0)
        return result is True
    except (RedisError, RedisConnectionError, asyncio.TimeoutError) as e:
        logger.error(f"Redis health check failed: {e}")
        return False


class RedisRetryMixin:
    """
    Mixin providing retry logic for Redis operations.
    """
    
    MAX_RETRIES = 3
    RETRY_DELAY = 0.5
    
    async def _retry_operation(self, operation, *args, **kwargs):
        """Execute a Redis operation with retry logic."""
        last_error = None
        
        for attempt in range(self.MAX_RETRIES):
            try:
                return await operation(*args, **kwargs)
            except (RedisError, RedisConnectionError) as e:
                last_error = e
                logger.warning(f"Redis operation failed (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                
                if attempt < self.MAX_RETRIES - 1:
                    await asyncio.sleep(self.RETRY_DELAY * (attempt + 1))
        
        raise last_error
