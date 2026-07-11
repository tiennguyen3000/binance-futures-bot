"""
BotController — shared state + Telegram command listener.
Cho phép điều khiển bot qua Telegram commands:
  /start        — Bật giao dịch
  /stop         — Tắt giao dịch
  /testnet      — Chuyển sang testnet (cần restart)
  /live         — Chuyển sang live (cần restart)
  /scan <N>     — Đặt số coin quét (vd: /scan 50)
  /status       — Xem trạng thái bot
  /help         — Danh sách lệnh
"""
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)

# ─── Lazy Telegram config (đọc sau load_dotenv) ────────────────
# QUAN TRỌNG: Dùng function thay vì module-level constant.
# bot_controller được import TRƯỚC khi load_dotenv() gọi ở main.py,
# nên os.getenv() ở module level sẽ trả về "".

def _bot_token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", "")

def _chat_id() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", "")

# ─── State file ─────────────────────────────────────────────────

STATE_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".bot_state")
STATE_FILE = os.path.join(STATE_DIR, "mode.json")


def _save_mode(mode: str):
    """Ghi mode vào file state để dùng ở lần khởi động sau."""
    os.makedirs(STATE_DIR, exist_ok=True)
    try:
        with open(STATE_FILE, "w") as f:
            json.dump({"mode": mode}, f)
        logger.info(f"Saved mode={mode} to {STATE_FILE}")
    except Exception as e:
        logger.warning(f"Failed to save mode: {e}")


def load_saved_mode() -> str:
    """Đọc mode từ file state. Trả về 'TESTNET' nếu không có."""
    try:
        if os.path.isfile(STATE_FILE):
            with open(STATE_FILE) as f:
                data = json.load(f)
                mode = data.get("mode", "TESTNET")
                logger.info(f"Loaded saved mode={mode} from {STATE_FILE}")
                return mode
    except Exception as e:
        logger.warning(f"Failed to load saved mode: {e}")
    return "TESTNET"


# ─── Shared state ───────────────────────────────────────────────

@dataclass
class BotConfig:
    """Trạng thái có thể thay đổi động của bot."""
    trading_enabled: bool = True
    top_n: int = 30
    mode: str = "TESTNET"
    restart_needed: bool = False
    max_positions: int = 1
    max_funding_rate_pct: float = 0.1
    positions: list = None
    total_pnl: float = 0.0
    balance_usdt: float = 0.0


config = BotConfig()


# ─── Send Telegram ──────────────────────────────────────────────

def send_tg(text: str) -> bool:
    """Gửi tin nhắn Telegram — lazy token."""
    token, chat = _bot_token(), _chat_id()
    if not token or not chat:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=8,
        )
        return resp.ok
    except Exception as e:
        logger.warning(f"TG send fail: {e}")
        return False


# ─── Command handler ────────────────────────────────────────────

def _handle_command(text: str) -> Optional[str]:
    """Xử lý command, trả về reply text hoặc None."""
    cmd = text.strip().lower()
    parts = cmd.split()

    if cmd in ("/start", "start"):
        if config.trading_enabled:
            return "✅ Bot đang BẬT giao dịch rồi."
        config.trading_enabled = True
        return "✅ Đã BẬT giao dịch. Bot sẽ vào lệnh khi có tín hiệu."

    if cmd in ("/stop", "stop"):
        if not config.trading_enabled:
            return "⏸ Bot đang TẮT giao dịch rồi."
        config.trading_enabled = False
        return "⏸ Đã TẮT giao dịch. Bot sẽ quét nhưng KHÔNG vào lệnh."

    if cmd in ("/testnet", "testnet"):
        if config.mode == "TESTNET":
            return "✅ Đã ở chế độ TESTNET."
        config.mode = "TESTNET"
        config.restart_needed = True
        _save_mode("TESTNET")
        return "🔄 Chuyển sang TESTNET. Vui lòng RESTART bot để áp dụng."

    if cmd in ("/live", "live"):
        live_env = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env.live")
        if not os.path.isfile(live_env):
            return ("⚠️ <b>Chưa có key Live!</b>\n"
                    "Tạo file <code>.env.live</code> trong thư mục bot với nội dung:\n"
                    "<pre>BINANCE_API_KEY=your_live_key\nBINANCE_API_SECRET=your_live_secret\nBINANCE_TESTNET=false</pre>\n"
                    f"Coi mẫu tại: <code>.env.live.template</code>")
        if config.mode == "LIVE":
            return "✅ Đã ở chế độ LIVE."
        config.mode = "LIVE"
        config.restart_needed = True
        _save_mode("LIVE")
        return "⚠️ Chuyển sang <b>LIVE</b>. Vui lòng RESTART bot để áp dụng."

    if cmd.startswith("/scan") or cmd.startswith("scan"):
        try:
            n = int(parts[1]) if len(parts) > 1 else 30
            config.top_n = max(5, min(100, n))
            return f"🔍 Đã đặt số coin quét = {config.top_n}."
        except (IndexError, ValueError):
            return f"🔍 Số coin hiện tại: {config.top_n}. Dùng: /scan <số> (5-100)"

    if cmd.startswith("/maxpos") or cmd.startswith("maxpos"):
        try:
            n = int(parts[1]) if len(parts) > 1 else config.max_positions
            config.max_positions = max(1, min(5, n))
            return f"📦 Đã đặt số vị thế tối đa = {config.max_positions}."
        except (IndexError, ValueError):
            return f"📦 Số vị thế tối đa hiện tại: {config.max_positions}. Dùng: /maxpos <số> (1-5)"

    if cmd.startswith("/funding") or cmd.startswith("funding"):
        try:
            n = float(parts[1]) if len(parts) > 1 else config.max_funding_rate_pct
            config.max_funding_rate_pct = max(0.001, min(0.5, n))
            return f"💸 Đã đặt funding rate tối đa = {config.max_funding_rate_pct}%. Dùng 0.05-0.5."
        except (IndexError, ValueError):
            return f"💸 Funding rate tối đa hiện tại: {config.max_funding_rate_pct}%. Dùng: /funding <%%> (0.001-0.5)"

    if cmd in ("/status", "status", "/dashboard"):
        mode_emoji = "🧪" if config.mode == "TESTNET" else "🔥"
        trading_emoji = "🟢" if config.trading_enabled else "🔴"
        warn = "\n⚠️ Cần restart để đổi mode!" if config.restart_needed else ""
        pos_count = len(config.positions) if config.positions else 0
        return (
            f"📊 <b>BOT DASHBOARD</b>{warn}\n"
            f"{mode_emoji} Mode: {config.mode}\n"
            f"{trading_emoji} Giao dịch: {'BẬT' if config.trading_enabled else 'TẮT'}\n"
            f"🔍 Quét: top {config.top_n} coins\n"
            f"📦 Vị thế: {pos_count}/{config.max_positions}\n"
            f"💰 Ví: {config.balance_usdt:.2f} USDT\n"
            f"📈 PnL: {config.total_pnl:+.2f} USDT\n"
            f"💸 Funding: &lt;{config.max_funding_rate_pct}%\n"
            f"💵 Vốn: 100 USDT | Đòn bẩy: 10x\n"
            f"📈 SL: ATR×1.5 | TP1: ATR×2 | TP2: ATR×3\n"
            f"🤖 Bot: @tiennk_future_auto_trading_bot"
        )

    if cmd in ("/position", "position", "/positions"):
        if not config.positions:
            return "📭 <b>Không có vị thế nào</b> — bot đang quét tìm tín hiệu."
        lines = ["📦 <b>VỊ THẾ ĐANG MỞ</b>"]
        for p in config.positions:
            emoji = "🟢" if p.get("side") == "LONG" else "🔴"
            lines.append(
                f"{emoji} {p['symbol']} {p['side']}\n"
                f"  • Vào: {p['entry_price']:.2f}\n"
                f"  • SL: {p['sl_price']:.2f} | TP: {p['tp_price']:.2f}\n"
                f"  • PnL: {p.get('unrealized_pnl',0):+.2f} USDT ({p.get('roi_pct',0):+.2f}%)"
            )
        return "\n".join(lines)

    if cmd in ("/pnl", "pnl", "/profit"):
        if not config.positions:
            return f"📊 <b>TỔNG KẾT</b>\n💰 Ví: {config.balance_usdt:.2f} USDT\n📈 PnL: {config.total_pnl:+.2f} USDT\n📭 Không có vị thế mở."
        lines = ["📊 <b>TỔNG KẾT P&L</b>"]
        total_pnl = 0.0
        for p in config.positions:
            pnl = p.get("unrealized_pnl", 0)
            total_pnl += pnl
            emoji = "📈" if pnl >= 0 else "📉"
            lines.append(f"{emoji} {p['symbol']}: {pnl:+.2f} USDT ({p.get('roi_pct',0):+.2f}%)")
        lines.append(f"\n💰 Ví: {config.balance_usdt:.2f} USDT")
        lines.append(f"{'📈' if total_pnl>=0 else '📉'} <b>Tổng PnL: {total_pnl:+.2f} USDT</b>")
        return "\n".join(lines)

    if cmd in ("/help", "help"):
        return (
            "🤖 <b>HƯỚNG DẪN ĐIỀU KHIỂN BOT</b>\n\n"
            "/start — Bật giao dịch\n"
            "/stop — Tắt giao dịch\n"
            "/testnet — Chuyển testnet (cần restart)\n"
            "/live — Chuyển live (cần restart)\n"
            "/scan 50 — Đặt số coin quét\n"
            "/maxpos 2 — Đặt số vị thế tối đa (1-5)\n"
            "/funding 0.05 — Lọc funding rate (0.001-0.5%)\n"
            "/position — Xem vị thế đang mở\n"
            "/pnl — Tổng kết lãi lỗ\n"
            "/status — Dashboard tổng quan\n"
            "/help — Danh sách lệnh"
        )

    return None


# ─── Polling thread ─────────────────────────────────────────────

_last_update_id: int = 0


def polling_loop(stop_event: threading.Event):
    """Chạy trong thread riêng, poll Telegram API để nhận commands."""
    global _last_update_id

    token, chat = _bot_token(), _chat_id()
    if not token or not chat:
        logger.info("Telegram polling disabled: TELEGRAM_BOT_TOKEN/CHAT_ID not configured")
        return

    logger.info("Telegram command listener started (polling)")
    while not stop_event.is_set():
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": _last_update_id + 1, "timeout": 30,
                        "allowed_updates": ["message"]},
                timeout=35,
            )
            if not resp.ok:
                time.sleep(5)
                continue

            for update in resp.json().get("result", []):
                _last_update_id = update.get("update_id", _last_update_id)
                msg = update.get("message", {})
                cid = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                if cid != chat:
                    continue

                reply = _handle_command(text)
                if reply:
                    send_tg(reply)
                    logger.info(f"TG command '{text}' → replied")

        except requests.Timeout:
            continue
        except Exception as e:
            logger.warning(f"TG polling error: {e}")
            time.sleep(5)

    logger.info("Telegram command listener stopped")


def start_polling() -> threading.Event:
    """Khởi động polling thread. Trả về stop_event."""
    stop_event = threading.Event()
    t = threading.Thread(target=polling_loop, args=(stop_event,), daemon=True)
    t.start()
    return stop_event
