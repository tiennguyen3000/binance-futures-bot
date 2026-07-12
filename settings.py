"""Validated, fail-safe runtime configuration for the trading bot."""
from __future__ import annotations

import os
from dataclasses import dataclass


def _bool(name: str, default: bool = False) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _positive_float(name: str, default: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


def _positive_int(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return value


@dataclass(frozen=True)
class BotSettings:
    trading_enabled: bool
    api_host: str
    api_port: int
    api_control_token: str
    api_mutations_enabled: bool
    testnet: bool
    leverage: int
    max_positions: int
    risk_per_trade: float
    max_position_notional_pct: float
    scan_interval_seconds: int

    @classmethod
    def from_env(cls) -> "BotSettings":
        token = os.getenv("API_CONTROL_TOKEN", "").strip()
        mutations_requested = _bool("ENABLE_API_MUTATIONS", False)
        return cls(
            trading_enabled=_bool("TRADING_ENABLED", False),
            api_host=os.getenv("API_HOST", "127.0.0.1"),
            api_port=_positive_int("API_PORT", 8765),
            api_control_token=token,
            api_mutations_enabled=bool(token and mutations_requested),
            testnet=_bool("BINANCE_TESTNET", True),
            leverage=_positive_int("TRADE_LEVERAGE", 10),
            max_positions=_positive_int("MAX_POSITIONS", 1),
            risk_per_trade=_positive_float("RISK_PER_TRADE", 0.015),
            max_position_notional_pct=_positive_float("MAX_POSITION_NOTIONAL_PCT", 0.10),
            scan_interval_seconds=_positive_int("SCAN_INTERVAL_SECONDS", 120),
        )
