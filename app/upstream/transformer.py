"""
Data transformer: Bybit messages -> Mudrex format.
Handles rebranding and data normalization.
"""
import json
from typing import Any, Dict, Optional
from datetime import datetime
from app.utils.logging import get_logger

logger = get_logger("upstream.transformer")


def transform_bybit_ticker(bybit_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transform Bybit Linear/Inverse ticker to Mudrex format.
    
    Bybit Input:
    {
        "topic": "tickers.BTCUSDT",
        "type": "snapshot" | "delta",
        "data": {
            "symbol": "BTCUSDT",
            "tickDirection": "ZeroPlusTick",
            "price24hPcnt": "0.0144",
            "lastPrice": "44578.50",
            "prevPrice24h": "44000.00",
            "highPrice24h": "45000.00",
            "lowPrice24h": "43500.00",
            "prevPrice1h": "44200.00",
            "markPrice": "44575.00",
            "indexPrice": "44580.00",
            "openInterest": "50000",
            "openInterestValue": "2228900000",
            "turnover24h": "5000000000",
            "volume24h": "112000",
            "nextFundingTime": "1704067200000",
            "fundingRate": "0.0001",
            "bid1Price": "44578.00",
            "bid1Size": "10",
            "ask1Price": "44579.00",
            "ask1Size": "5"
        },
        "cs": 87654321,
        "ts": 1704063600000
    }
    
    Mudrex Output:
    {
        "stream": "mudrex.futures.ticker.BTCUSDT",
        "type": "update",
        "data": { ... transformed ... },
        "timestamp": 1704063600000,
        "source": "mudrex"
    }
    """
    try:
        topic = bybit_message.get("topic", "")
        if not topic.startswith("tickers."):
            return None
        
        symbol = topic.replace("tickers.", "")
        data = bybit_message.get("data", {})
        msg_type = bybit_message.get("type", "delta")
        ts = bybit_message.get("ts", int(datetime.utcnow().timestamp() * 1000))
        
        # Parse and format percentage
        change_pct = _format_percent(data.get("price24hPcnt", "0"))
        
        mudrex_data = {
            "symbol": symbol,
            "price": data.get("lastPrice", "0"),
            "markPrice": data.get("markPrice"),
            "indexPrice": data.get("indexPrice"),
            "high24h": data.get("highPrice24h"),
            "low24h": data.get("lowPrice24h"),
            "prevPrice24h": data.get("prevPrice24h"),
            "volume24h": data.get("volume24h"),
            "turnover24h": data.get("turnover24h"),
            "change24hPercent": change_pct,
            "tickDirection": data.get("tickDirection"),
            "bid": {
                "price": data.get("bid1Price"),
                "size": data.get("bid1Size"),
            },
            "ask": {
                "price": data.get("ask1Price"),
                "size": data.get("ask1Size"),
            },
            "openInterest": data.get("openInterest"),
            "openInterestValue": data.get("openInterestValue"),
            "fundingRate": data.get("fundingRate"),
            "nextFundingTime": data.get("nextFundingTime"),
        }
        
        # Remove None values for cleaner output
        mudrex_data = {k: v for k, v in mudrex_data.items() if v is not None}
        
        return {
            "stream": f"mudrex.futures.ticker.{symbol}",
            "type": "snapshot" if msg_type == "snapshot" else "update",
            "data": mudrex_data,
            "timestamp": ts,
            "source": "mudrex"
        }
        
    except Exception as e:
        logger.error(f"Error transforming ticker message: {e}")
        return None


def transform_bybit_kline(bybit_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transform Bybit kline/candlestick message to Mudrex format.
    
    Bybit Input:
    {
        "topic": "kline.1.BTCUSDT",
        "data": [{
            "start": 1704063600000,
            "end": 1704063659999,
            "interval": "1",
            "open": "44500.00",
            "close": "44550.00",
            "high": "44600.00",
            "low": "44450.00",
            "volume": "100.5",
            "turnover": "4475000",
            "confirm": false,
            "timestamp": 1704063650000
        }],
        "ts": 1704063650000,
        "type": "snapshot"
    }
    """
    try:
        topic = bybit_message.get("topic", "")
        if not topic.startswith("kline."):
            return None
        
        # Parse topic: kline.{interval}.{symbol}
        parts = topic.split(".")
        if len(parts) < 3:
            return None
            
        interval = parts[1]
        symbol = parts[2]
        
        data_list = bybit_message.get("data", [])
        if not data_list:
            return None
            
        kline = data_list[0]
        ts = bybit_message.get("ts", int(datetime.utcnow().timestamp() * 1000))
        
        mudrex_data = {
            "symbol": symbol,
            "interval": _map_interval(interval),
            "openTime": kline.get("start"),
            "closeTime": kline.get("end"),
            "open": kline.get("open"),
            "high": kline.get("high"),
            "low": kline.get("low"),
            "close": kline.get("close"),
            "volume": kline.get("volume"),
            "turnover": kline.get("turnover"),
            "confirmed": kline.get("confirm", False),
        }
        
        return {
            "stream": f"mudrex.futures.kline.{interval}.{symbol}",
            "type": "update",
            "data": mudrex_data,
            "timestamp": ts,
            "source": "mudrex"
        }
        
    except Exception as e:
        logger.error(f"Error transforming kline message: {e}")
        return None


def transform_bybit_trade(bybit_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Transform Bybit trade message to Mudrex format.
    
    Bybit Input:
    {
        "topic": "publicTrade.BTCUSDT",
        "type": "snapshot",
        "data": [{
            "T": 1704063600000,
            "s": "BTCUSDT",
            "S": "Buy",
            "v": "0.5",
            "p": "44550.00",
            "L": "ZeroPlusTick",
            "i": "12345678",
            "BT": false
        }],
        "ts": 1704063600000
    }
    """
    try:
        topic = bybit_message.get("topic", "")
        if not topic.startswith("publicTrade."):
            return None
        
        symbol = topic.replace("publicTrade.", "")
        data_list = bybit_message.get("data", [])
        ts = bybit_message.get("ts", int(datetime.utcnow().timestamp() * 1000))
        
        trades = []
        for trade in data_list:
            trades.append({
                "id": trade.get("i"),
                "symbol": symbol,
                "price": trade.get("p"),
                "quantity": trade.get("v"),
                "side": trade.get("S", "").lower(),
                "time": trade.get("T"),
                "isBlockTrade": trade.get("BT", False),
            })
        
        return {
            "stream": f"mudrex.futures.trade.{symbol}",
            "type": "update",
            "data": trades,
            "timestamp": ts,
            "source": "mudrex"
        }
        
    except Exception as e:
        logger.error(f"Error transforming trade message: {e}")
        return None


def transform_message(bybit_message: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """
    Main transformer that routes to the appropriate handler.
    """
    topic = bybit_message.get("topic", "")
    
    # Handle pong/ping responses
    if bybit_message.get("op") == "pong":
        return None
    
    if bybit_message.get("success") is not None:
        # Subscription confirmation
        return None
    
    if topic.startswith("tickers."):
        return transform_bybit_ticker(bybit_message)
    elif topic.startswith("kline."):
        return transform_bybit_kline(bybit_message)
    elif topic.startswith("publicTrade."):
        return transform_bybit_trade(bybit_message)
    else:
        logger.debug(f"Unknown topic: {topic}")
        return None


def _format_percent(decimal_str: str) -> str:
    """Convert Bybit decimal percentage to readable format."""
    try:
        value = float(decimal_str) * 100
        return f"{value:.2f}"
    except (ValueError, TypeError):
        return "0.00"


def _map_interval(bybit_interval: str) -> str:
    """Map Bybit interval to standard notation."""
    interval_map = {
        "1": "1m",
        "3": "3m",
        "5": "5m",
        "15": "15m",
        "30": "30m",
        "60": "1h",
        "120": "2h",
        "240": "4h",
        "360": "6h",
        "720": "12h",
        "D": "1d",
        "W": "1w",
        "M": "1M",
    }
    return interval_map.get(bybit_interval, bybit_interval)
