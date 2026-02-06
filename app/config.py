"""
Configuration loader from environment variables.
Production-ready with validation and sensible defaults.
"""
from pydantic_settings import BaseSettings, SettingsConfigDict
from pydantic import field_validator
from typing import Optional, Any
import os


class Settings(BaseSettings):
    """
    Application settings loaded from environment variables.
    All settings have sensible defaults for local development.
    """
    
    @field_validator("BYBIT_TESTNET", "LOG_JSON", "DEBUG", "REDIS_RETRY_ON_TIMEOUT", mode="before")
    @classmethod
    def strip_whitespace(cls, v: Any) -> Any:
        if isinstance(v, str):
            return v.strip()
        return v
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )

    # ==========================================================================
    # Server Configuration
    # ==========================================================================
    HOST: str = "0.0.0.0"
    PORT: int = 8000
    DEBUG: bool = False
    ENVIRONMENT: str = "production"  # "development", "staging", "production"

    # ==========================================================================
    # Redis Configuration (Required for production)
    # ==========================================================================
    REDIS_URL: str = "redis://localhost:6379"
    REDIS_MAX_CONNECTIONS: int = 50  # Use 50–100 for 1000+ concurrent clients
    REDIS_SOCKET_TIMEOUT: float = 5.0
    REDIS_RETRY_ON_TIMEOUT: bool = True

    # ==========================================================================
    # Bybit Upstream Configuration
    # ==========================================================================
    BYBIT_WS_URL: str = "wss://stream.bybit.com/v5/public/linear"
    BYBIT_TESTNET: bool = False
    BYBIT_CATEGORY: str = "linear"  # "linear", "inverse", "spot"
    
    # Subscription limits
    BYBIT_MAX_SYMBOLS_PER_SUBSCRIPTION: int = 10  # Per subscription request
    BYBIT_MAX_SYMBOLS_PER_CONNECTION: int = 500  # Total per connection
    
    # Reconnection settings
    BYBIT_RECONNECT_DELAY_INITIAL: float = 1.0
    BYBIT_RECONNECT_DELAY_MAX: float = 60.0
    BYBIT_RECONNECT_DELAY_MULTIPLIER: float = 2.0
    BYBIT_PING_INTERVAL: float = 20.0  # Bybit requires ping every 20s
    BYBIT_PING_TIMEOUT: float = 10.0

    # ==========================================================================
    # Rate Limiting & Client Limits
    # ==========================================================================
    MAX_SUBSCRIPTIONS_PER_CLIENT: int = 100
    MAX_CLIENTS_TOTAL: int = 10000
    MAX_MESSAGE_RATE_PER_CLIENT: int = 100  # Messages per second
    CLIENT_IDLE_TIMEOUT: float = 300.0  # 5 minutes

    # ==========================================================================
    # Logging
    # ==========================================================================
    LOG_LEVEL: str = "INFO"
    LOG_FORMAT: str = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    LOG_JSON: bool = False  # Set True for production JSON logs

    # ==========================================================================
    # PubSub fan-out (production scale)
    # ==========================================================================
    FANOUT_CALLBACK_TIMEOUT: float = 5.0  # Seconds per client send; slow clients are skipped after this

    # ==========================================================================
    # Health & Metrics
    # ==========================================================================
    HEALTH_CHECK_ENABLED: bool = True
    METRICS_ENABLED: bool = True

    @property
    def is_production(self) -> bool:
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        return self.ENVIRONMENT == "development"


# Global settings instance
settings = Settings()
