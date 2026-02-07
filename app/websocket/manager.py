"""
WebSocket connection manager.
Tracks all connected clients and their state.
"""
import asyncio
import json
import time
from typing import Dict, Optional, Set, Any
from dataclasses import dataclass, field
from fastapi import WebSocket
from app.config import settings
from app.utils.logging import get_logger

logger = get_logger("websocket.manager")

# #region agent log
import pathlib as _pathlib
_DEBUG_LOG_PATH = _pathlib.Path(__file__).resolve().parent.parent.parent / ".cursor" / "debug.log"
def _debug_log(location: str, message: str, data: dict = None, hypothesis_id: str = None):
    try:
        _DEBUG_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {"id": f"log_{int(time.time()*1000)}", "timestamp": int(time.time() * 1000), "location": location, "message": message, "data": data or {}}
        if hypothesis_id:
            payload["hypothesisId"] = hypothesis_id
        with open(_DEBUG_LOG_PATH, "a") as f:
            f.write(json.dumps(payload) + "\n")
    except Exception:
        pass
# #endregion


@dataclass
class ClientState:
    """State for a connected client."""
    websocket: WebSocket
    client_id: int
    connected_at: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)
    subscriptions: Set[str] = field(default_factory=set)
    message_count: int = 0
    error_count: int = 0
    _receive_count: int = field(default=0, repr=False)
    _receive_window_start: float = field(default_factory=time.time, repr=False)

    def update_activity(self):
        self.last_activity = time.time()

    def check_receive_rate(self) -> bool:
        """Return True if under rate limit, False if exceeded. Call once per received message."""
        now = time.time()
        if now - self._receive_window_start >= 1.0:
            self._receive_window_start = now
            self._receive_count = 0
        self._receive_count += 1
        return self._receive_count <= settings.MAX_MESSAGE_RATE_PER_CLIENT

    def is_idle(self, timeout: float) -> bool:
        return (time.time() - self.last_activity) > timeout
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "client_id": self.client_id,
            "connected_at": self.connected_at,
            "last_activity": self.last_activity,
            "subscriptions": list(self.subscriptions),
            "message_count": self.message_count,
            "uptime_seconds": time.time() - self.connected_at,
        }


class ConnectionManager:
    """
    Manages all connected WebSocket clients.
    
    Features:
    - Client tracking and state management
    - Connection limits enforcement
    - Idle connection cleanup
    - Broadcast messaging
    """
    
    def __init__(self):
        self._clients: Dict[int, ClientState] = {}
        self._lock = asyncio.Lock()
        
        # Stats
        self._total_connections = 0
        self._total_messages = 0

    @property
    def active_count(self) -> int:
        """Number of currently connected clients."""
        return len(self._clients)

    async def connect(self, websocket: WebSocket) -> Optional[ClientState]:
        """
        Accept a new WebSocket connection.
        Limit check is under lock; accept() runs outside lock so concurrent
        handshakes are not serialized (avoids timeouts under load).
        """
        # #region agent log
        t0 = time.perf_counter()
        _debug_log("manager.py:connect", "before_lock", {"active": self.active_count, "t0": t0}, "H3")
        # #endregion
        async with self._lock:
            if self.active_count >= settings.MAX_CLIENTS_TOTAL:
                logger.warning(f"Connection limit reached ({settings.MAX_CLIENTS_TOTAL})")
                return None
        # Accept outside lock so multiple clients can complete handshake in parallel
        await websocket.accept()
        # #region agent log
        t2 = time.perf_counter()
        _debug_log("manager.py:connect", "after_accept", {"total_sec": t2 - t0}, "H3")
        # #endregion
        client_id = id(websocket)
        state = ClientState(websocket=websocket, client_id=client_id)
        async with self._lock:
            self._clients[client_id] = state
            self._total_connections += 1
        logger.info(f"Client {client_id} connected. Active: {self.active_count}")
        return state

    async def disconnect(self, client_id: int):
        """
        Handle client disconnection.
        """
        async with self._lock:
            state = self._clients.pop(client_id, None)
            
            if state:
                logger.info(
                    f"Client {client_id} disconnected. "
                    f"Active: {self.active_count}, "
                    f"Messages: {state.message_count}"
                )

    def get_client(self, client_id: int) -> Optional[ClientState]:
        """Get client state by ID."""
        return self._clients.get(client_id)

    def get_all_clients(self) -> Dict[int, ClientState]:
        """Get all connected clients."""
        return self._clients.copy()

    async def add_subscription(self, client_id: int, stream_key: str) -> bool:
        """
        Add a subscription to a client.
        
        Returns:
            True if added, False if limit reached
        """
        state = self._clients.get(client_id)
        if not state:
            return False
        
        if len(state.subscriptions) >= settings.MAX_SUBSCRIPTIONS_PER_CLIENT:
            return False
        
        state.subscriptions.add(stream_key)
        state.update_activity()
        return True

    async def remove_subscription(self, client_id: int, stream_key: str):
        """Remove a subscription from a client."""
        state = self._clients.get(client_id)
        if state:
            state.subscriptions.discard(stream_key)
            state.update_activity()

    def get_subscriptions(self, client_id: int) -> Set[str]:
        """Get all subscriptions for a client."""
        state = self._clients.get(client_id)
        return state.subscriptions.copy() if state else set()

    async def send_to_client(self, client_id: int, message: Dict[str, Any]) -> bool:
        """
        Send a message to a specific client.
        
        Returns:
            True if sent successfully, False otherwise
        """
        state = self._clients.get(client_id)
        if not state:
            return False
        
        try:
            await state.websocket.send_json(message)
            state.message_count += 1
            self._total_messages += 1
            return True
        except Exception as e:
            state.error_count += 1
            logger.debug(f"Failed to send to client {client_id}: {e}")
            return False

    async def broadcast(self, message: Dict[str, Any], exclude: Optional[Set[int]] = None):
        """
        Broadcast a message to all connected clients.
        """
        exclude = exclude or set()
        tasks = []
        for client_id, state in list(self._clients.items()):
            if client_id in exclude:
                continue
            async def send_one(ws=state.websocket, st=state):
                try:
                    await ws.send_json(message)
                    st.message_count += 1
                except Exception:
                    pass
            tasks.append(send_one())
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def cleanup_idle(self) -> int:
        """
        Disconnect idle clients.
        
        Returns:
            Number of clients disconnected
        """
        idle_clients = []
        
        for client_id, state in list(self._clients.items()):
            if state.is_idle(settings.CLIENT_IDLE_TIMEOUT):
                idle_clients.append(client_id)
        
        for client_id in idle_clients:
            state = self._clients.get(client_id)
            if state:
                try:
                    await state.websocket.close(code=4000, reason="Idle timeout")
                except Exception:
                    pass
                # Do not call disconnect() here: handler's finally block will run on
                # WebSocketDisconnect and perform full subscription teardown (_cleanup).
        
        if idle_clients:
            logger.info(f"Cleaned up {len(idle_clients)} idle connections")
        
        return len(idle_clients)

    def get_stats(self) -> Dict[str, Any]:
        """Get connection manager statistics."""
        subscription_counts = [len(s.subscriptions) for s in self._clients.values()]
        
        return {
            "active_connections": self.active_count,
            "total_connections_lifetime": self._total_connections,
            "total_messages_sent": self._total_messages,
            "avg_subscriptions_per_client": (
                sum(subscription_counts) / len(subscription_counts)
                if subscription_counts else 0
            ),
            "max_limit": settings.MAX_CLIENTS_TOTAL,
        }
