"""
State manager — tracks bot state and active positions.
Max 2 concurrent positions enforced.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
import logging

logger = logging.getLogger(__name__)


class BotState(Enum):
    """Overall bot operational state."""
    IDLE = "idle"
    SCANNING = "scanning"
    ENTERING = "entering"
    HOLDING = "holding"       # Max positions reached
    EXITING = "exiting"


@dataclass
class Position:
    """Represents an active trading position."""
    symbol: str
    side: str               # "LONG" or "SHORT"
    entry_price: float
    quantity: float
    sl_price: float
    tp_price: float
    order_id_entry: Optional[str] = None
    order_id_sl: Optional[str] = None
    order_id_tp: Optional[str] = None
    pnl: float = 0.0
    roi_pct: float = 0.0


class StateManager:
    """
    Manages bot state and tracks up to N concurrent positions.
    Thread-safe for single-threaded async usage.
    """

    def __init__(self, max_positions: int = 2):
        self.max_positions = max_positions
        self._state = BotState.IDLE
        self._positions: list[Position] = []
        logger.info(f"StateManager initialized (max_positions={max_positions})")

    # ---- State ----

    @property
    def state(self) -> BotState:
        return self._state

    def set_state(self, state: BotState):
        old = self._state
        self._state = state
        logger.debug(f"State: {old.value} → {state.value}")

    # ---- Position queries ----

    def can_open(self) -> bool:
        """Check if we can open a new position."""
        return len(self._positions) < self.max_positions

    def has_position(self, symbol: str) -> bool:
        return any(p.symbol == symbol for p in self._positions)

    def get_position(self, symbol: str) -> Optional[Position]:
        for p in self._positions:
            if p.symbol == symbol:
                return p
        return None

    def get_positions(self) -> list[Position]:
        return list(self._positions)

    def position_count(self) -> int:
        return len(self._positions)

    # ---- Position management ----

    def add_position(self, position: Position) -> bool:
        """Add a position. Returns False if at max capacity."""
        if not self.can_open():
            logger.warning(f"Cannot add {position.symbol}: max {self.max_positions} positions reached")
            return False

        if self.has_position(position.symbol):
            logger.warning(f"Cannot add {position.symbol}: already tracked")
            return False

        self._positions.append(position)
        logger.info(f"Position added: {position.side} {position.symbol} @ {position.entry_price}")
        logger.info(f"  Qty={position.quantity:.4f} | SL={position.sl_price:.2f} | TP={position.tp_price:.2f}")

        if not self.can_open():
            self.set_state(BotState.HOLDING)

        return True

    def remove_position(self, symbol: str) -> bool:
        """Remove a position by symbol."""
        for i, p in enumerate(self._positions):
            if p.symbol == symbol:
                removed = self._positions.pop(i)
                logger.info(f"Position removed: {removed.side} {removed.symbol} (PnL={removed.pnl:.2f})")
                self.set_state(BotState.SCANNING if self.can_open() else BotState.HOLDING)
                return True
        logger.warning(f"Position {symbol} not found, cannot remove")
        return False

    def update_position_pnl(self, symbol: str, pnl: float, roi_pct: float):
        """Update the running PnL for a position (called periodically)."""
        p = self.get_position(symbol)
        if p:
            p.pnl = pnl
            p.roi_pct = roi_pct

    def clear_positions(self):
        """Remove all tracked positions (e.g. on bot restart)."""
        count = len(self._positions)
        self._positions.clear()
        self.set_state(BotState.IDLE)
        logger.info(f"Cleared {count} positions")

    def __repr__(self) -> str:
        return (
            f"StateManager(state={self._state.value}, "
            f"positions={len(self._positions)}/{self.max_positions})"
        )
