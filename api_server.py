"""
REST API server cho live trading — kiểm soát bot qua HTTP.
Dùng Python stdlib (http.server + json), không cần thêm dependencies.

Endpoints:
  GET  /status        — Dashboard tổng quan
  POST /start         — Bật giao dịch
  POST /stop          — Tắt giao dịch
  POST /scan          — Set số coin quét (body: {"top_n": 50})
  GET  /positions     — Danh sách vị thế đang mở
  GET  /balance       — Số dư USDT
  GET  /config        — Cấu hình hiện tại
"""
import json
import logging
import os
from http.server import HTTPServer, BaseHTTPRequestHandler
from threading import Thread

logger = logging.getLogger(__name__)

# ─── Shared state (import từ bot_controller) ────────────────────

try:
    from bot_controller import config as bot_cfg, send_tg, _handle_command
except ImportError:
    # Fallback standalone
    from dataclasses import dataclass
    @dataclass
    class _C:
        trading_enabled: bool = True
        top_n: int = 30
        mode: str = "TESTNET"
        restart_needed: bool = False
        max_positions: int = 1
    bot_cfg = _C()
    def send_tg(t): pass
    def _handle_command(t): return None


# ─── Position fetcher (injected by main.py) ─────────────────────

_positions_fn = lambda: []
_balance_fn = lambda: 0.0
_executor_ref = None


def set_fetchers(positions_fn, balance_fn, executor=None):
    """Gắn hàm lấy dữ liệu từ main loop."""
    global _positions_fn, _balance_fn, _executor_ref
    _positions_fn = positions_fn
    _balance_fn = balance_fn
    _executor_ref = executor


# ─── HTTP Handler ───────────────────────────────────────────────

class APIHandler(BaseHTTPRequestHandler):
    """Xử lý request REST API."""

    def _json(self, data: dict, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode())

    def _read_body(self) -> dict:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        body = self.rfile.read(length)
        try:
            return json.loads(body)
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        path = self.path.rstrip("/")

        if path == "/status" or path == "":
            positions = _positions_fn()
            self._json({
                "status": "ok",
                "trading_enabled": bot_cfg.trading_enabled,
                "mode": bot_cfg.mode,
                "top_n": bot_cfg.top_n,
                "max_positions": bot_cfg.max_positions,
                "active_positions": len(positions),
                "balance_usdt": round(_balance_fn(), 2),
                "restart_needed": bot_cfg.restart_needed,
                "positions": positions,
            })

        elif path == "/positions":
            self._json({"positions": _positions_fn()})

        elif path == "/balance":
            self._json({"balance_usdt": round(_balance_fn(), 2)})

        elif path == "/config":
            self._json({
                "trading_enabled": bot_cfg.trading_enabled,
                "mode": bot_cfg.mode,
                "top_n": bot_cfg.top_n,
                "restart_needed": bot_cfg.restart_needed,
            })

        else:
            self._json({"error": f"Unknown endpoint: {path}"}, 404)

    def do_POST(self):
        path = self.path.rstrip("/")
        body = self._read_body()

        if path == "/start":
            reply = _handle_command("start")
            self._json({"reply": reply, "trading_enabled": bot_cfg.trading_enabled})
            if bot_cfg.trading_enabled:
                send_tg("▶️ Bật giao dịch qua API")

        elif path == "/stop":
            reply = _handle_command("stop")
            self._json({"reply": reply, "trading_enabled": bot_cfg.trading_enabled})
            if not bot_cfg.trading_enabled:
                send_tg("⏸ Tắt giao dịch qua API")

        elif path == "/scan":
            n = body.get("top_n", body.get("n", 30))
            reply = _handle_command(f"/scan {n}")
            self._json({"reply": reply, "top_n": bot_cfg.top_n})

        elif path == "/switch":
            target = body.get("mode", "").upper()
            if target == "LIVE":
                reply = _handle_command("/live")
            elif target == "TESTNET":
                reply = _handle_command("/testnet")
            else:
                reply = f"Invalid mode: {target}. Use LIVE or TESTNET."
            self._json({"reply": reply, "mode": bot_cfg.mode})

        elif path == "/close":
            symbol = body.get("symbol", "")
            if not symbol or not _executor_ref:
                self._json({"error": "Missing 'symbol' or executor not ready"}, 400)
            else:
                try:
                    result = _executor_ref.close_position(symbol)
                    self._json(result)
                except Exception as e:
                    self._json({"error": str(e)}, 500)

        else:
            self._json({"error": f"Unknown endpoint: {path}"}, 404)

    def log_message(self, fmt, *args):
        logger.debug(f"API: {args[0]} {args[1]} -> {args[2]}")


# ─── Server ─────────────────────────────────────────────────────

def run_api_server(host: str = "0.0.0.0", port: int = 8765):
    """Chạy HTTP server (blocking)."""
    server = HTTPServer((host, port), APIHandler)
    logger.info(f"🌐 REST API running at http://{host}:{port}")
    logger.info(f"   Endpoints: GET /status, /positions, /balance, /config")
    logger.info(f"              POST /start, /stop, /scan, /switch, /close")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


def start_api_thread(host: str = "0.0.0.0", port: int = 8765) -> Thread:
    """Khởi động API server trong thread riêng."""
    t = Thread(target=run_api_server, args=(host, port), daemon=True)
    t.start()
    return t
