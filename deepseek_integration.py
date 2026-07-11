"""
DeepSeek AI integration for signal filtering.

Uses OpenAI-compatible client to call DeepSeek API for
second-opinion validation of trading signals before entry.
"""
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

# Try to import openai (optional dependency)
try:
    from openai import OpenAI, APIError, Timeout
    HAS_OPENAI = True
except ImportError:
    HAS_OPENAI = False
    logger.warning("openai package not installed. DeepSeek filter unavailable.")


def get_deepseek_client() -> Optional['OpenAI']:
    """
    Initialize and return a DeepSeek OpenAI-compatible client.
    Returns None if API key is not configured or openai not installed.
    """
    if not HAS_OPENAI:
        return None

    api_key = os.getenv("DEEPSEEK_API_KEY", "")
    base_url = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com/v1")

    if not api_key:
        logger.warning("DEEPSEEK_API_KEY not set in .env")
        return None

    client = OpenAI(
        api_key=api_key,
        base_url=base_url,
        timeout=10.0,  # 10 second timeout to avoid blocking scan cycles
        max_retries=1,
    )
    return client


def deepseek_signal_filter(symbol: str, signal: dict, price: float, rsi: float) -> str:
    """
    Use DeepSeek to evaluate a trading signal's reliability.
    
    Args:
        symbol: Trading pair (e.g. 'BTCUSDT')
        signal: Signal dict with 'side', 'price', 'rsi', etc.
        price: Current market price
        rsi: Current RSI value
    
    Returns:
        "yes" if signal is deemed reliable, "no" if rejected,
        or "yes (fallback)" if DeepSeek is unavailable/errors.
    """
    client = get_deepseek_client()
    if not client:
        logger.info("DeepSeek not configured — passing signal by default")
        return "yes (fallback)"

    side = signal.get("side", "UNKNOWN")
    ema_fast = signal.get("ema_fast", "?")
    ema_slow = signal.get("ema_slow", "?")
    
    prompt = (
        f"Bạn là một nhà giao dịch crypto chuyên nghiệp với 10 năm kinh nghiệm.\n\n"
        f"Tín hiệu {side} vừa xuất hiện trên {symbol}:\n"
        f"- Giá hiện tại: {price:.2f} USDT\n"
        f"- RSI(14): {rsi:.1f}\n"
        f"- EMA(9): {ema_fast:.2f}\n"
        f"- EMA(21): {ema_slow:.2f}\n\n"
        f"Hãy đánh giá tín hiệu này có đáng tin cậy không dựa trên:\n"
        f"1. RSI có ở vùng quá mua (>70) hoặc quá bán (<30) không?\n"
        f"2. Khoảng cách giữa EMA9 và EMA21 có đủ lớn để xác nhận xu hướng?\n"
        f"3. Có dấu hiệu nhiễu (chop) nào không?\n\n"
        f"Trả lời CHỈ MỘT TỪ: 'yes' nếu tín hiệu đáng tin, 'no' nếu không.\n"
        f"Sau đó xuống dòng và giải thích ngắn gọn bằng tiếng Việt."
    )

    try:
        response = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=100,
        )

        content = response.choices[0].message.content.strip()
        logger.info(f"DeepSeek response for {symbol} {side}: {content}")

        # Parse first word as yes/no
        first_word = content.split()[0].lower().strip(".,!?")
        
        if first_word in ("yes", "có", "đồng ý", "đáng tin"):
            return "yes"
        elif first_word in ("no", "không", "từ chối"):
            return "no"
        else:
            # Unclear response — be conservative
            logger.warning(f"Unclear DeepSeek response: {content}")
            return "yes"

    except Timeout:
        logger.warning("DeepSeek API timeout — passing signal by default")
        return "yes (timeout)"
    except APIError as e:
        logger.warning(f"DeepSeek API error: {e} — passing signal by default")
        return "yes (api_error)"
    except Exception as e:
        logger.warning(f"DeepSeek unexpected error: {e} — passing signal by default")
        return "yes (error)"
