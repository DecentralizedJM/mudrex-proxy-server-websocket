"""
Tests for the data transformer module.
"""
import pytest
from app.upstream.transformer import (
    transform_bybit_ticker,
    transform_bybit_kline,
    transform_message,
    _format_percent,
    _map_interval,
)


class TestFormatPercent:
    """Tests for percentage formatting."""
    
    def test_positive_percent(self):
        assert _format_percent("0.0144") == "1.44"
    
    def test_negative_percent(self):
        assert _format_percent("-0.0250") == "-2.50"
    
    def test_zero_percent(self):
        assert _format_percent("0") == "0.00"
    
    def test_invalid_percent(self):
        assert _format_percent("invalid") == "0.00"
    
    def test_none_percent(self):
        assert _format_percent(None) == "0.00"


class TestMapInterval:
    """Tests for interval mapping."""
    
    def test_minute_intervals(self):
        assert _map_interval("1") == "1m"
        assert _map_interval("5") == "5m"
        assert _map_interval("15") == "15m"
    
    def test_hour_intervals(self):
        assert _map_interval("60") == "1h"
        assert _map_interval("240") == "4h"
    
    def test_day_week_month(self):
        assert _map_interval("D") == "1d"
        assert _map_interval("W") == "1w"
        assert _map_interval("M") == "1M"
    
    def test_unknown_interval(self):
        assert _map_interval("unknown") == "unknown"


class TestTransformBybitTicker:
    """Tests for ticker transformation."""
    
    def test_valid_ticker(self):
        bybit_msg = {
            "topic": "tickers.BTCUSDT",
            "type": "snapshot",
            "data": {
                "symbol": "BTCUSDT",
                "lastPrice": "44578.50",
                "highPrice24h": "45000.00",
                "lowPrice24h": "43500.00",
                "volume24h": "112000",
                "price24hPcnt": "0.0144",
                "bid1Price": "44578.00",
                "ask1Price": "44579.00",
            },
            "ts": 1704063600000
        }
        
        result = transform_bybit_ticker(bybit_msg)
        
        assert result is not None
        assert result["stream"] == "mudrex.futures.ticker.BTCUSDT"
        assert result["type"] == "snapshot"
        assert result["source"] == "mudrex"
        assert result["timestamp"] == 1704063600000
        assert result["data"]["symbol"] == "BTCUSDT"
        assert result["data"]["price"] == "44578.50"
        assert result["data"]["change24hPercent"] == "1.44"
    
    def test_invalid_topic(self):
        bybit_msg = {"topic": "invalid.BTCUSDT", "data": {}}
        assert transform_bybit_ticker(bybit_msg) is None
    
    def test_delta_type(self):
        bybit_msg = {
            "topic": "tickers.ETHUSDT",
            "type": "delta",
            "data": {"symbol": "ETHUSDT", "lastPrice": "3000.00"},
            "ts": 1704063600000
        }
        
        result = transform_bybit_ticker(bybit_msg)
        assert result["type"] == "update"


class TestTransformBybitKline:
    """Tests for kline transformation."""
    
    def test_valid_kline(self):
        bybit_msg = {
            "topic": "kline.1.BTCUSDT",
            "type": "snapshot",
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
                "confirm": False,
            }],
            "ts": 1704063650000
        }
        
        result = transform_bybit_kline(bybit_msg)
        
        assert result is not None
        assert result["stream"] == "mudrex.futures.kline.1.BTCUSDT"
        assert result["data"]["symbol"] == "BTCUSDT"
        assert result["data"]["interval"] == "1m"
        assert result["data"]["open"] == "44500.00"
        assert result["data"]["confirmed"] == False


class TestTransformMessage:
    """Tests for main transform router."""
    
    def test_routes_ticker(self):
        msg = {"topic": "tickers.BTCUSDT", "data": {"lastPrice": "100"}, "ts": 123}
        result = transform_message(msg)
        assert result is not None
        assert "ticker" in result["stream"]
    
    def test_routes_kline(self):
        msg = {
            "topic": "kline.1.BTCUSDT",
            "data": [{"start": 1, "end": 2, "open": "1", "high": "1", "low": "1", "close": "1", "volume": "1"}],
            "ts": 123
        }
        result = transform_message(msg)
        assert result is not None
        assert "kline" in result["stream"]
    
    def test_ignores_pong(self):
        msg = {"op": "pong"}
        assert transform_message(msg) is None
    
    def test_ignores_success(self):
        msg = {"success": True, "op": "subscribe"}
        assert transform_message(msg) is None
