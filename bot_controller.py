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
import logging
import json
import os
import threading
import time
from dataclasses import dataclass, field
from typing import Optional
import requests

logger = logging.getLogger(__name__)

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
from dataclasses import dataclass, field
from typing import Optional
import requests

logger = logging.getLogger(__name__)

# ─── Telegram polling ───────────────────────────────────────────

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")


@dataclass
class BotConfig:
    """Trạng thái có thể thay đổi động của bot."""
    trading_enabled: bool = True
    top_n: int = 30
    mode: str = "TESTNET"          # "TESTNET" or "LIVE"
    restart_needed: bool = False   # Báo hiệu cần restart để đổi mode


# Shared instance
config = BotConfig()
_last_update_id: int = 0
_polling_active = False


def send_tg(text: str) -> bool:
    """Gửi tin nhắn Telegram."""
    if not BOT_TOKEN or not CHAT_ID:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage",
            json={"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML",
                  "disable_web_page_preview": True},
            timeout=8,
        )
        return resp.ok
    except Exception as e:
        logger.warning(f"TG send fail: {e}")
        return False


def _handle_command(text: str) -> Optional[str]:
    """Xử lý command, trả về reply text hoặc None."""
    cmd = text.strip().lower()
    parts = cmd.split()

    if cmd == "/start" or cmd == "start":
        if config.trading_enabled:
            return "✅ Bot đang BẬT giao dịch rồi."
        config.trading_enabled = True
        return "✅ Đã BẬT giao dịch. Bot sẽ vào lệnh khi có tín hiệu."

    elif cmd in ("/stop", "stop"):
        if not config.trading_enabled:
            return "⏸ Bot đang TẮT giao dịch rồi."
        config.trading_enabled = False
        return "⏸ Đã TẮT giao dịch. Bot sẽ quét nhưng KHÔNG vào lệnh."

    elif cmd == "/testnet" or cmd == "testnet":
        if config.mode == "TESTNET":
            return "✅ Đã ở chế độ TESTNET."
        config.mode = "TESTNET"
        config.restart_needed = True
        _save_mode("TESTNET")
        return "🔄 Chuyển sang TESTNET. Vui lòng RESTART bot để áp dụng."

    elif cmd == "/live" or cmd == "live":
        # Kiểm tra file .env.live có tồn tại không
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

    elif cmd.startswith("/scan") or cmd.startswith("scan"):
        try:
            n = int(parts[1]) if len(parts) > 1 else 30
            n = max(5, min(100, n))  # clamp 5-100
            config.top_n = n
            return f"🔍 Đã đặt số coin quét = {n}. Áp dụng ngay cho chu kỳ sau."
        except (IndexError, ValueError):
            return f"🔍 Số coin hiện tại: {config.top_n}. Dùng: /scan <số> (5-100)"

    elif cmd in ("/status", "status", "/dashboard"):
        mode_emoji = "🧪" if config.mode == "TESTNET" else "🔥"
        trading_emoji = "🟢" if config.trading_enabled else "🔴"
        restart_warn = "\n⚠️ Cần restart để đổi mode!" if config.restart_needed else ""
        return (
            f"📊 <b>BOT DASHBOARD</b>{restart_warn}\n"
            f"{mode_emoji} Mode: {config.mode}\n"
            f"{trading_emoji} Giao dịch: {'BẬT' if config.trading_enabled else 'TẮT'}\n"
            f"🔍 Quét: top {config.top_n} coins\n"
            f"💵 Vốn: 100 USDT | Đòn bẩy: 10x\n"
            f"📈 SL: ATR×1.5 | TP1: ATR×2 | TP2: ATR×3\n"
            f"🤖 Bot: @tiennk_future_auto_trading_bot"
        )

    elif cmd in ("/help", "help", "/start"):
        return (
            "🤖 <b>HƯỚNG DẪN ĐIỀU KHIỂN BOT</b>\n\n"
            "/start — Bật giao dịch\n"
            "/stop — Tắt giao dịch\n"
            "/testnet — Chuyển testnet (cần restart)\n"
            "/live — Chuyển live (cần restart)\n"
            "/scan 50 — Đặt số coin quét\n"
            "/status — Dashboard tổng quan\n"
            "/help — Danh sách lệnh"
        )

    return None


def polling_loop(stop_event: threading.Event):
    """
    Chạy trong thread riêng, poll Telegram API để nhận commands.
    """
    global _last_update_id, _polling_active

    if not BOT_TOKEN:
        logger.info("Telegram polling disabled: no BOT_TOKEN")
        return

    _polling_active = True
    logger.info("Telegram command listener started (polling)")

    while not stop_event.is_set():
        try:
            resp = requests.get(
                f"https://api.telegram.org/bot{BOT_TOKEN}/getUpdates",
                params={
                    "offset": _last_update_id + 1,
                    "timeout": 30,  # long polling
                    "allowed_updates": ["message"],
                },
                timeout=35,
            )
            if not resp.ok:
                time.sleep(5)
                continue

            data = resp.json()
            for update in data.get("result", []):
                _last_update_id = update.get("update_id", _last_update_id)
                msg = update.get("message", {})
                chat_id = str(msg.get("chat", {}).get("id", ""))
                text = msg.get("text", "").strip()

                # Chỉ xử lý từ chat được phép
                if chat_id != CHAT_ID:
                    logger.debug(f"Ignored message from {chat_id}")
                    continue

                reply = _handle_command(text)
                if reply:
                    send_tg(reply)
                    logger.info(f"TG command '{text}' → replied")

        except requests.Timeout:
            continue  # long polling timeout is normal
        except Exception as e:
            logger.warning(f"TG polling error: {e}")
            time.sleep(5)

    _polling_active = False
    logger.info("Telegram command listener stopped")


def start_polling() -> threading.Event:
    """Khởi động polling thread. Trả về stop_event."""
    stop_event = threading.Event()
    t = threading.Thread(target=polling_loop, args=(stop_event,), daemon=True)
    t.start()
    return stop_event
