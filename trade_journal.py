"""Durable local journal for order intents and exchange identifiers."""
from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any


class TradeJournal:
    def __init__(self, path: str = ".bot_state/trade_journal.sqlite3"):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        with self._connect() as conn:
            conn.execute(
                """CREATE TABLE IF NOT EXISTS order_intents (
                    client_order_id TEXT PRIMARY KEY,
                    symbol TEXT NOT NULL,
                    purpose TEXT NOT NULL,
                    status TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    exchange_id TEXT,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                )"""
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def record_intent(self, client_order_id: str, symbol: str, purpose: str, payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO order_intents(client_order_id, symbol, purpose, status, payload, exchange_id) VALUES (?, ?, ?, 'pending', ?, NULL)",
                (client_order_id, symbol, purpose, json.dumps(payload, sort_keys=True)),
            )

    def update(self, client_order_id: str, status: str, exchange_id: str | None = None) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE order_intents SET status=?, exchange_id=?, updated_at=CURRENT_TIMESTAMP WHERE client_order_id=?",
                (status, exchange_id, client_order_id),
            )
