"""Local, authenticated REST control plane for the trading bot."""
from __future__ import annotations

import hmac
import json
import logging
import os
from http.server import BaseHTTPRequestHandler, HTTPServer
from threading import Thread
from typing import Mapping

from settings import BotSettings

logger = logging.getLogger(__name__)

try:
    from bot_controller import config as bot_cfg, send_tg, _handle_command
except ImportError:
    from dataclasses import dataclass
    @dataclass
    class _Config:
        trading_enabled: bool = False
        top_n: int = 30
        mode: str = "TESTNET"
        restart_needed: bool = False
        max_positions: int = 1
        max_funding_rate_pct: float = 0.1
    bot_cfg = _Config()
    def send_tg(_: str) -> None: pass
    def _handle_command(_: str) -> str: return "Controller unavailable"

_positions_fn = lambda: []
_balance_fn = lambda: 0.0
_executor_ref = None


def set_fetchers(positions_fn, balance_fn, executor=None):
    global _positions_fn, _balance_fn, _executor_ref
    _positions_fn, _balance_fn, _executor_ref = positions_fn, balance_fn, executor


def is_mutation_authorized(headers: Mapping[str, str]) -> bool:
    settings = BotSettings.from_env()
    if not settings.api_mutations_enabled:
        return False
    supplied = headers.get("Authorization", "")
    expected = f"Bearer {settings.api_control_token}"
    return hmac.compare_digest(supplied, expected)


class APIHandler(BaseHTTPRequestHandler):
    def _json(self, data: dict, status: int = 200) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def _read_body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length", 0))
            return json.loads(self.rfile.read(length)) if length else {}
        except (ValueError, json.JSONDecodeError):
            return {}

    def do_OPTIONS(self):
        self.send_response(405)
        self.end_headers()

    def do_GET(self):
        path = self.path.rstrip("/")
        if path in ("", "/status"):
            positions = _positions_fn()
            return self._json({"status": "ok", "trading_enabled": bot_cfg.trading_enabled, "mode": bot_cfg.mode, "top_n": bot_cfg.top_n, "max_positions": bot_cfg.max_positions, "active_positions": len(positions), "balance_usdt": round(_balance_fn(), 2), "restart_needed": bot_cfg.restart_needed, "positions": positions})
        if path == "/positions":
            return self._json({"positions": _positions_fn()})
        if path == "/balance":
            return self._json({"balance_usdt": round(_balance_fn(), 2)})
        if path == "/config":
            return self._json({"trading_enabled": bot_cfg.trading_enabled, "mode": bot_cfg.mode, "top_n": bot_cfg.top_n, "restart_needed": bot_cfg.restart_needed})
        return self._json({"error": f"Unknown endpoint: {path}"}, 404)

    def do_POST(self):
        if not is_mutation_authorized(self.headers):
            return self._json({"error": "Mutating API disabled or unauthorized"}, 403)
        path, body = self.path.rstrip("/"), self._read_body()
        logger.warning("Authenticated API mutation: %s", path)
        if path == "/start":
            reply = _handle_command("start")
            return self._json({"reply": reply, "trading_enabled": bot_cfg.trading_enabled})
        if path == "/stop":
            reply = _handle_command("stop")
            return self._json({"reply": reply, "trading_enabled": bot_cfg.trading_enabled})
        if path == "/scan":
            reply = _handle_command(f"/scan {body.get('top_n', body.get('n', 30))}")
            return self._json({"reply": reply, "top_n": bot_cfg.top_n})
        if path == "/switch":
            target = body.get("mode", "").upper()
            if target not in {"LIVE", "TESTNET"}:
                return self._json({"error": "mode must be LIVE or TESTNET"}, 400)
            return self._json({"reply": _handle_command("/live" if target == "LIVE" else "/testnet"), "mode": bot_cfg.mode})
        if path == "/close":
            symbol = body.get("symbol", "").upper()
            if not symbol or not _executor_ref:
                return self._json({"error": "Missing symbol or executor unavailable"}, 400)
            return self._json(_executor_ref.close_position(symbol))
        return self._json({"error": f"Unknown endpoint: {path}"}, 404)

    def log_message(self, fmt, *args):
        logger.debug("API: " + fmt, *args)


def run_api_server(host: str = "127.0.0.1", port: int = 8765):
    server = HTTPServer((host, port), APIHandler)
    logger.info("REST control plane listening at http://%s:%s", host, port)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def start_api_thread(host: str = "127.0.0.1", port: int = 8765) -> Thread:
    thread = Thread(target=run_api_server, args=(host, port), daemon=True, name="bot-api")
    thread.start()
    return thread
