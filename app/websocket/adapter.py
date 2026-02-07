"""
Thin adapter that wraps a ``websockets`` ServerConnection / WebSocketServerProtocol
so that the rest of the codebase (handler, manager) can keep calling the same
methods it used with FastAPI's WebSocket: receive_text(), send_json(), send_text(),
close(), accept().
"""

import json
from typing import Any, Dict


class WebSocketAdapter:
    """
    Duck-type compatible replacement for ``fastapi.WebSocket``.

    The websockets library's server connection exposes ``recv()``, ``send()``,
    and ``close()`` – this class maps the FastAPI-style names onto those.
    ``accept()`` is a no-op because the handshake is already complete when
    the websockets handler is called.
    """

    def __init__(self, connection) -> None:
        self._conn = connection

    # ------------------------------------------------------------------
    # FastAPI-compatible interface
    # ------------------------------------------------------------------

    async def accept(self) -> None:
        """No-op – websockets.serve already completed the handshake."""
        pass

    async def receive_text(self) -> str:
        """Receive a text frame (or decode a binary frame to str)."""
        msg = await self._conn.recv()
        if isinstance(msg, bytes):
            return msg.decode("utf-8")
        return msg

    async def send_json(self, data: Dict[str, Any]) -> None:
        """Serialize *data* to JSON and send as a text frame."""
        await self._conn.send(json.dumps(data))

    async def send_text(self, text: str) -> None:
        """Send a pre-serialized text frame."""
        await self._conn.send(text)

    async def close(self, code: int = 1000, reason: str = "") -> None:
        """Initiate the closing handshake."""
        await self._conn.close(code, reason)

    # Expose remote_address for logging (best-effort)
    @property
    def remote_address(self):
        return getattr(self._conn, "remote_address", None)
