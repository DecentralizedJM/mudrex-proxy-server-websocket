"""
Pydantic models for WebSocket protocol messages.
Defines the Mudrex WebSocket API contract.
"""
from pydantic import BaseModel, Field, field_validator
from typing import List, Optional, Literal, Any, Dict
from enum import Enum


class StreamType(str, Enum):
    """Available stream types."""
    TICKER = "ticker"
    KLINE = "kline"
    TRADE = "trade"


class MessageOp(str, Enum):
    """WebSocket operation types."""
    SUBSCRIBE = "subscribe"
    UNSUBSCRIBE = "unsubscribe"
    PING = "ping"
    PONG = "pong"


# =============================================================================
# Client -> Server Messages
# =============================================================================

class SubscribeRequest(BaseModel):
    """
    Client subscription request.
    
    Example:
    {
        "op": "subscribe",
        "args": ["ticker:BTCUSDT", "ticker:ETHUSDT"]
    }
    
    Stream format: "{type}:{symbol}" or "{type}:{interval}:{symbol}" for klines
    """
    op: Literal["subscribe"]
    args: List[str] = Field(..., min_length=1, max_length=100)

    @field_validator("args")
    @classmethod
    def validate_args(cls, v: List[str]) -> List[str]:
        validated = []
        for arg in v:
            parts = arg.split(":")
            if len(parts) >= 2:
                validated.append(arg)
            elif arg.startswith("tickers.") and len(arg) > len("tickers."):
                validated.append(arg)
            else:
                raise ValueError(f"Invalid stream format: {arg}. Expected 'type:symbol' or 'tickers.SYMBOL'")
        return validated


class UnsubscribeRequest(BaseModel):
    """Client unsubscription request."""
    op: Literal["unsubscribe"]
    args: List[str] = Field(..., min_length=1, max_length=100)


class PingRequest(BaseModel):
    """Client ping request."""
    op: Literal["ping"]


# =============================================================================
# Server -> Client Messages
# =============================================================================

class SubscribeResponse(BaseModel):
    """Response to subscription request."""
    op: Literal["subscribe"] = "subscribe"
    success: bool
    args: List[str]
    message: Optional[str] = None


class UnsubscribeResponse(BaseModel):
    """Response to unsubscription request."""
    op: Literal["unsubscribe"] = "unsubscribe"
    success: bool
    args: List[str]
    message: Optional[str] = None


class PongResponse(BaseModel):
    """Response to ping request."""
    op: Literal["pong"] = "pong"
    timestamp: int


class ErrorResponse(BaseModel):
    """Error response."""
    op: Literal["error"] = "error"
    code: str
    message: str


# =============================================================================
# Data Models
# =============================================================================

class TickerData(BaseModel):
    """Ticker stream data."""
    symbol: str
    price: str
    markPrice: Optional[str] = None
    indexPrice: Optional[str] = None
    high24h: Optional[str] = None
    low24h: Optional[str] = None
    volume24h: Optional[str] = None
    turnover24h: Optional[str] = None
    change24hPercent: Optional[str] = None
    bid: Optional[Dict[str, str]] = None
    ask: Optional[Dict[str, str]] = None
    openInterest: Optional[str] = None
    fundingRate: Optional[str] = None
    nextFundingTime: Optional[str] = None


class KlineData(BaseModel):
    """Kline stream data."""
    symbol: str
    interval: str
    openTime: int
    closeTime: int
    open: str
    high: str
    low: str
    close: str
    volume: str
    turnover: Optional[str] = None
    confirmed: bool = False


class TradeData(BaseModel):
    """Trade stream data."""
    id: str
    symbol: str
    price: str
    quantity: str
    side: str  # "buy" or "sell"
    time: int
    isBlockTrade: bool = False


class StreamMessage(BaseModel):
    """
    Generic stream data message.
    
    Example:
    {
        "stream": "mudrex.futures.ticker.BTCUSDT",
        "type": "update",
        "data": { ... },
        "timestamp": 1704063600000,
        "source": "mudrex"
    }
    """
    stream: str
    type: Literal["snapshot", "update"]
    data: Any
    timestamp: int
    source: Literal["mudrex"] = "mudrex"


# =============================================================================
# Utility Functions
# =============================================================================

def parse_stream_arg(arg: str) -> tuple[str, str, Optional[str]]:
    """
    Parse a stream argument into components.
    
    Args:
        arg: Stream argument like "ticker:BTCUSDT", "kline:1h:BTCUSDT", or "tickers.BTCUSDT"
    
    Returns:
        Tuple of (stream_type, symbol, interval_or_none)
    """
    # Accept legacy dot format: tickers.SYMBOL -> ticker stream
    if arg.startswith("tickers.") and len(arg) > len("tickers."):
        symbol = arg[len("tickers.") :]
        return "ticker", symbol, None

    parts = arg.split(":")
    if len(parts) == 2:
        stream_type, symbol = parts[0], parts[1]
    elif len(parts) == 3:
        stream_type, symbol = parts[0], parts[2]
    else:
        raise ValueError(f"Invalid stream format: {arg}")
    valid_types = {e.value for e in StreamType}
    if stream_type not in valid_types:
        raise ValueError(f"Invalid stream type: {stream_type}. Expected one of {sorted(valid_types)}")
    if len(parts) == 2:
        return stream_type, symbol, None
    return stream_type, symbol, parts[1]


def make_stream_key(stream_type: str, symbol: str, interval: Optional[str] = None) -> str:
    """Create a stream key for internal tracking."""
    if interval:
        return f"{stream_type}:{interval}:{symbol}"
    return f"{stream_type}:{symbol}"
