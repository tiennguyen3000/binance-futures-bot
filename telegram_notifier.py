"""
Telegram notifier — gửi thông báo giao dịch qua Bot API.
Dùng HTTP request đơn giản, không cần python-telegram-bot.
"""
import logging
import os
from typing import Optional

import requests

logger = logging.getLogger(__name__)

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")
API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"


def _is_configured() -> bool:
    return bool(BOT_TOKEN and CHAT_ID)


def send_message(text: str, parse_mode: str = "HTML") -> bool:
    """Gửi tin nhắn text tới Telegram. Trả về True nếu thành công."""
    if not _is_configured():
        logger.debug("Telegram chưa được cấu hình (TELEGRAM_BOT_TOKEN / CHAT_ID)")
        return False

    try:
        resp = requests.post(
            API_URL,
            json={
                "chat_id": CHAT_ID,
                "text": text,
                "parse_mode": parse_mode,
                "disable_web_page_preview": True,
            },
            timeout=8,
        )
        resp.raise_for_status()
        logger.debug(f"Telegram sent OK: {text[:60]}...")
        return True
    except Exception as e:
        logger.warning(f"Telegram send thất bại: {e}")
        return False


# ─── Helper formatters ──────────────────────────────────────────

def signal_msg(symbol: str, side: str, price: float, rsi: float, confidence: float = None) -> str:
    emoji = "🟢" if side == "LONG" else "🔴"
    conf_str = f"\n• 🎯 Độ tin tưởng: {confidence:.0f}%" if confidence else ""
    return (
        f"{emoji} <b>TÍN HIỆU {side}</b>{conf_str}\n"
        f"• Cặp: {symbol}\n"
        f"• Giá: {price:.2f} USDT\n"
        f"• RSI(14): {rsi:.1f}"
    )


def entry_msg(symbol: str, side: str, entry: float, qty: float,
              sl: float, tp1: float, balance: float, tp2: float = None) -> str:
    emoji = "🟢" if side == "LONG" else "🔴"
    lines = [
        f"{emoji} <b>ĐÃ VÀO LỆNH {side}</b>",
        f"• Cặp: {symbol}",
        f"• Vào: {entry:.2f}",
        f"• Khối lượng: {qty:.4f}",
        f"• 🛑 SL: {sl:.2f}",
        f"• ✅ TP1: {tp1:.2f}",
    ]
    if tp2:
        lines.append(f"• 🎯 TP2: {tp2:.2f}")
    lines.append(f"• 💰 Ví: {balance:.2f} USDT")
    return "\n".join(lines)


def exit_msg(symbol: str, side: str, entry: float, exit_p: float,
             pnl: float, roi: float, reason: str = "manual") -> str:
    emoji = "✅" if pnl >= 0 else "❌"
    pnl_emoji = "📈" if pnl >= 0 else "📉"
    reason_txt = {"manual": "đóng lệnh", "sl": "SL", "tp": "TP"}.get(reason, reason)
    return (
        f"{emoji} <b>ĐÃ ĐÓNG LỆNH</b> ({reason_txt})\n"
        f"• Cặp: {symbol}\n"
        f"• Hướng: {side}\n"
        f"• Vào: {entry:.2f} → Ra: {exit_p:.2f}\n"
        f"{pnl_emoji} <b>PnL: {pnl:+.2f} USDT</b> ({roi:+.2f}%)"
    )


def status_msg(positions: list, balance: float, max_pos: int) -> str:
    if not positions:
        return (
            f"📊 <b>TRẠNG THÁI</b>\n"
            f"• Ví: {balance:.2f} USDT\n"
            f"• Vị thế: 0/{max_pos} — Đang quét..."
        )
    lines = [
        f"📊 <b>TRẠNG THÁI</b>",
        f"• Ví: {balance:.2f} USDT",
        f"• Vị thế: {len(positions)}/{max_pos}",
    ]
    for p in positions:
        emoji = "🟢" if p["side"] == "LONG" else "🔴"
        lines.append(
            f"{emoji} {p['symbol']} {p['side']} | "
            f"{p['unrealized_pnl']:+.2f} USDT"
        )
    return "\n".join(lines)


def error_msg(context: str, detail: str) -> str:
    return (
        f"⚠️ <b>LỖI</b>\n"
        f"• {context}\n"
        f"• {detail[:200]}"
    )


def bot_start_msg(mode: str, capital: float, leverage: int) -> str:
    return (
        f"🤖 <b>TRADING BOT KHỞI ĐỘNG</b>\n"
        f"• Chế độ: {mode}\n"
        f"• Vốn: {capital} USDT\n"
        f"• Đòn bẩy: {leverage}x\n"
        f"• SL: ATR×1.5 | TP1: ATR×2 | TP2: ATR×3\n"
        f"• Tối đa: 1 vị thế\n"
        f"📱 /help — điều khiển bot"
    )


def bot_stop_msg(positions: list) -> str:
    if not positions:
        return "🛑 Bot dừng. Không có vị thế nào đang mở."
    lines = ["🛑 <b>BOT DỪNG</b>"]
    for p in positions:
        emoji = "🟢" if p["side"] == "LONG" else "🔴"
        lines.append(f"{emoji} {p['symbol']} {p['side']} @ {p['entry_price']:.2f}")
    lines.append("⚠️ Còn vị thế mở — hãy kiểm tra sàn!")
    return "\n".join(lines)
