"""Thread-safe bot state and explicit position lifecycle."""
from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from enum import Enum
from typing import Optional

logger = logging.getLogger(__name__)


class BotState(Enum):
    SAFE_HALT = "safe_halt"
    IDLE = "idle"
    SCANNING = "scanning"
    ENTERING = "entering"
    HOLDING = "holding"
    EXITING = "exiting"


class PositionStatus(Enum):
    OPENING = "opening"
    OPEN = "open"
    CLOSING = "closing"
    UNKNOWN = "unknown"


@dataclass
class Position:
    symbol: str
    side: str
    entry_price: float
    quantity: float
    sl_price: float
    tp_price: float
    order_id_entry: Optional[str] = None
    order_id_sl: Optional[str] = None
    order_id_tp: Optional[str] = None
    pnl: float = 0.0
    roi_pct: float = 0.0
    status: PositionStatus = PositionStatus.OPEN


class StateManager:
    """Owns in-memory position state; all check-and-mutate operations are locked."""

    def __init__(self, max_positions: int = 1):
        self.max_positions = max_positions
        self._state = BotState.IDLE
        self._positions: list[Position] = []
        self._lock = threading.RLock()

    @property
    def state(self) -> BotState:
        with self._lock:
            return self._state

    def set_state(self, state: BotState) -> None:
        with self._lock:
            self._state = state

    def can_open(self) -> bool:
        with self._lock:
            return self._state != BotState.SAFE_HALT and len(self._positions) < self.max_positions

    def has_position(self, symbol: str) -> bool:
        with self._lock:
            return any(p.symbol == symbol for p in self._positions)

    def get_position(self, symbol: str) -> Optional[Position]:
        with self._lock:
            return next((p for p in self._positions if p.symbol == symbol), None)

    def get_positions(self) -> list[Position]:
        with self._lock:
            return list(self._positions)

    def add_position(self, position: Position) -> bool:
        with self._lock:
            if not self.can_open() or self.has_position(position.symbol):
                return False
            self._positions.append(position)
            self._state = BotState.HOLDING if len(self._positions) >= self.max_positions else BotState.IDLE
            return True

    def begin_close(self, symbol: str) -> Optional[Position]:
        with self._lock:
            position = self.get_position(symbol)
            if not position or position.status == PositionStatus.CLOSING:
                return None
            position.status = PositionStatus.CLOSING
            self._state = BotState.EXITING
            return position

    def cancel_close(self, symbol: str) -> None:
        with self._lock:
            position = self.get_position(symbol)
            if position:
                position.status = PositionStatus.OPEN
                self._state = BotState.HOLDING if len(self._positions) >= self.max_positions else BotState.IDLE

    def remove_position(self, symbol: str) -> bool:
        with self._lock:
            for index, position in enumerate(self._positions):
                if position.symbol == symbol:
                    self._positions.pop(index)
                    self._state = BotState.IDLE if self.can_open() else BotState.HOLDING
                    return True
            return False

    def update_position_pnl(self, symbol: str, pnl: float, roi_pct: float) -> None:
        with self._lock:
            position = self.get_position(symbol)
            if position:
                position.pnl, position.roi_pct = pnl, roi_pct

    def record_unknown_exposure(self, position: Position, reason: str) -> None:
        """Retain a local record when the exchange cannot prove an exit occurred."""
        with self._lock:
            existing = self.get_position(position.symbol)
            if existing:
                existing.status = PositionStatus.UNKNOWN
            else:
                position.status = PositionStatus.UNKNOWN
                self._positions.append(position)
            self._state = BotState.SAFE_HALT
            logger.critical("Unknown exchange exposure retained for %s: %s", position.symbol, reason)

    def safe_halt(self, reason: str) -> None:
        with self._lock:
            self._state = BotState.SAFE_HALT
            logger.critical("Bot entered SAFE_HALT: %s", reason)
