"""
WebSocket message handler.
Processes client messages and manages subscriptions.
"""
import asyncio
import json
import time
from typing import Dict, Any, Optional
from fastapi import WebSocket, WebSocketDisconnect
from app.websocket.manager import ConnectionManager, ClientState
from app.websocket.models import (
    parse_stream_arg,
    make_stream_key,
    SubscribeResponse,
    UnsubscribeResponse,
    PongResponse,
    ErrorResponse,
)
from app.upstream.pool import UpstreamPool
from app.redis.pubsub import PubSubManager
from app.redis.subscriptions import SubscriptionManager
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("websocket.handler")


class ClientHandler:
    """
    Handles a single client WebSocket connection.
    
    Manages:
    - Message parsing and validation
    - Subscription lifecycle
    - Redis pub/sub integration
    - Error handling
    """
    
    def __init__(
        self,
        client: ClientState,
        connection_manager: ConnectionManager,
        upstream_pool: UpstreamPool,
        pubsub: PubSubManager,
        subscriptions: SubscriptionManager,
    ):
        self.client = client
        self.manager = connection_manager
        self.upstream = upstream_pool
        self.pubsub = pubsub
        self.subscriptions = subscriptions
        
        # Track this client's subscriptions
        self._subscribed_keys: set = set()

    async def handle(self):
        """Main handler loop for the client."""
        client_id = self.client.client_id
        
        try:
            while True:
                # Receive message
                raw = await self.client.websocket.receive_text()
                self.client.update_activity()
                
                # Parse and handle
                try:
                    message = json.loads(raw)
                    await self._handle_message(message)
                except json.JSONDecodeError:
                    await self._send_error("INVALID_JSON", "Invalid JSON message")
                except Exception as e:
                    logger.error(f"Error handling message from {client_id}: {e}")
                    await self._send_error("INTERNAL_ERROR", str(e))
                    
        except WebSocketDisconnect:
            logger.debug(f"Client {client_id} disconnected normally")
        except Exception as e:
            logger.error(f"Client {client_id} error: {e}")
        finally:
            # Cleanup
            await self._cleanup()

    async def _handle_message(self, message: Dict[str, Any]):
        """Route message to appropriate handler."""
        op = message.get("op")
        
        if op == "subscribe":
            await self._handle_subscribe(message.get("args", []))
        elif op == "unsubscribe":
            await self._handle_unsubscribe(message.get("args", []))
        elif op == "ping":
            await self._handle_ping()
        else:
            await self._send_error("UNKNOWN_OP", f"Unknown operation: {op}")

    async def _handle_subscribe(self, args: list):
        """Handle subscription request."""
        client_id = self.client.client_id
        
        if not args:
            await self._send_error("INVALID_ARGS", "No streams specified")
            return
        
        # Check subscription limit
        current_count = len(self._subscribed_keys)
        if current_count + len(args) > settings.MAX_SUBSCRIPTIONS_PER_CLIENT:
            await self._send_error(
                "LIMIT_EXCEEDED",
                f"Max {settings.MAX_SUBSCRIPTIONS_PER_CLIENT} subscriptions allowed"
            )
            return
        
        subscribed = []
        
        for arg in args:
            try:
                stream_type, symbol, interval = parse_stream_arg(arg)
                stream_key = make_stream_key(stream_type, symbol, interval)
                
                # Skip if already subscribed
                if stream_key in self._subscribed_keys:
                    subscribed.append(arg)
                    continue
                
                # Increment global subscription count
                await self.subscriptions.increment(stream_type, symbol)
                
                # Subscribe to Redis pub/sub
                await self.pubsub.subscribe(
                    stream_type,
                    symbol,
                    client_id,
                    self._on_stream_message
                )
                
                # Ensure upstream subscription
                await self.upstream.ensure_subscribed(stream_type, symbol)
                
                # Track locally
                self._subscribed_keys.add(stream_key)
                await self.manager.add_subscription(client_id, stream_key)
                
                subscribed.append(arg)
                
            except ValueError as e:
                logger.warning(f"Invalid subscription arg '{arg}': {e}")
                continue
        
        # Send response
        response = SubscribeResponse(
            success=len(subscribed) > 0,
            args=subscribed,
            message=f"Subscribed to {len(subscribed)} streams"
        )
        await self.client.websocket.send_json(response.model_dump())
        
        logger.info(f"Client {client_id} subscribed to {len(subscribed)} streams")

    async def _handle_unsubscribe(self, args: list):
        """Handle unsubscription request."""
        client_id = self.client.client_id
        
        if not args:
            await self._send_error("INVALID_ARGS", "No streams specified")
            return
        
        unsubscribed = []
        
        for arg in args:
            try:
                stream_type, symbol, interval = parse_stream_arg(arg)
                stream_key = make_stream_key(stream_type, symbol, interval)
                
                # Skip if not subscribed
                if stream_key not in self._subscribed_keys:
                    continue
                
                # Decrement global subscription count
                await self.subscriptions.decrement(stream_type, symbol)
                
                # Unsubscribe from Redis pub/sub
                await self.pubsub.unsubscribe(stream_type, symbol, client_id)
                
                # Maybe unsubscribe upstream
                await self.upstream.ensure_unsubscribed(stream_type, symbol)
                
                # Track locally
                self._subscribed_keys.discard(stream_key)
                await self.manager.remove_subscription(client_id, stream_key)
                
                unsubscribed.append(arg)
                
            except ValueError:
                continue
        
        # Send response
        response = UnsubscribeResponse(
            success=True,
            args=unsubscribed,
            message=f"Unsubscribed from {len(unsubscribed)} streams"
        )
        await self.client.websocket.send_json(response.model_dump())
        
        logger.info(f"Client {client_id} unsubscribed from {len(unsubscribed)} streams")

    async def _handle_ping(self):
        """Handle ping request."""
        response = PongResponse(timestamp=int(time.time() * 1000))
        await self.client.websocket.send_json(response.model_dump())

    async def _on_stream_message(self, channel: str, data: Dict[str, Any]):
        """Callback when a message arrives from Redis pub/sub."""
        try:
            await self.client.websocket.send_json(data)
            self.client.message_count += 1
        except Exception as e:
            logger.debug(f"Failed to send message to client {self.client.client_id}: {e}")
            self.client.error_count += 1

    async def _send_error(self, code: str, message: str):
        """Send an error response to the client."""
        response = ErrorResponse(code=code, message=message)
        try:
            await self.client.websocket.send_json(response.model_dump())
        except Exception:
            pass

    async def _cleanup(self):
        """Clean up client resources on disconnect."""
        client_id = self.client.client_id
        
        # Unsubscribe from all streams
        for stream_key in list(self._subscribed_keys):
            try:
                parts = stream_key.split(":")
                if len(parts) >= 2:
                    stream_type = parts[0]
                    symbol = parts[-1]
                    
                    await self.subscriptions.decrement(stream_type, symbol)
                    await self.pubsub.unsubscribe(stream_type, symbol, client_id)
                    await self.upstream.ensure_unsubscribed(stream_type, symbol)
            except Exception as e:
                logger.error(f"Error cleaning up subscription {stream_key}: {e}")
        
        self._subscribed_keys.clear()
        
        # Unsubscribe from all pubsub
        await self.pubsub.unsubscribe_client(client_id)
        
        # Remove from connection manager
        await self.manager.disconnect(client_id)
        
        logger.debug(f"Client {client_id} cleanup complete")


async def handle_client_websocket(
    websocket: WebSocket,
    manager: ConnectionManager,
    upstream_pool: UpstreamPool,
    pubsub: PubSubManager,
    subscriptions: SubscriptionManager,
):
    """
    Main entry point for handling a WebSocket connection.
    Called by FastAPI for each new connection.
    """
    # Accept connection
    client = await manager.connect(websocket)
    
    if client is None:
        # Connection rejected (limit reached)
        await websocket.close(code=4003, reason="Connection limit reached")
        return
    
    # Create handler and run
    handler = ClientHandler(
        client=client,
        connection_manager=manager,
        upstream_pool=upstream_pool,
        pubsub=pubsub,
        subscriptions=subscriptions,
    )
    
    await handler.handle()
