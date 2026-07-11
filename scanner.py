"""
Professional signal scanner — multi-timeframe trend + pullback entry.
Chiến lược thực chiến: xác định trend khung cao (1h), vào lệnh tại pullback
về EMA khung thấp (15m), SL dựa trên ATR + cấu trúc, TP đa mục tiêu.
"""
import logging
import pandas as pd
import numpy as np

from api_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


# ─── Indicators (pandas-native, không cần pandas-ta/numba) ─────

def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(span=period, adjust=False).mean()
    avg_loss = loss.ewm(span=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    return 100 - (100 / (1 + rs))


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    """Average True Range — đo lường biến động giá."""
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat([
        (high - low).abs(),
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=period, adjust=False).mean()


def find_swing_low(df: pd.DataFrame, lookback: int = 20) -> float:
    """Tìm đáy gần nhất trong lookback nến (cấu trúc thị trường)."""
    lows = df["low"].astype(float).tail(lookback)
    return float(lows.min())


def find_swing_high(df: pd.DataFrame, lookback: int = 20) -> float:
    """Tìm đỉnh gần nhất trong lookback nến."""
    highs = df["high"].astype(float).tail(lookback)
    return float(highs.max())


def klines_to_df(klines: list) -> pd.DataFrame:
    """Chuyển Binance kline list sang DataFrame."""
    df = pd.DataFrame(klines, columns=[
        "timestamp", "open", "high", "low", "close", "volume",
        "close_time", "quote_volume", "count", "taker_buy_vol",
        "taker_buy_quote", "ignore",
    ])
    for col in ["open", "high", "low", "close", "volume"]:
        df[col] = df[col].astype(float)
    return df


# ─── Stablecoin filter ──────────────────────────────────────────

STABLECOIN_SUFFIXES = [
    "USDC", "USDT", "BUSD", "TUSD", "DAI", "FDUSD", "USDP", "GUSD",
    "EUR", "GBP", "CHF", "AUD", "BRL", "SGD", "TRY", "ZAR",
    "UST", "USTC", "EURS", "CEUR",
]
STABLECOIN_PAIRS: set[str] = set()
for base in ["", "USD"]:
    for quote in STABLECOIN_SUFFIXES:
        STABLECOIN_PAIRS.add(f"{base}{quote}")
for s in STABLECOIN_SUFFIXES:
    for q in STABLECOIN_SUFFIXES:
        if s != q:
            STABLECOIN_PAIRS.add(f"{s}{q}")


def is_stablecoin(symbol: str) -> bool:
    """Loại bỏ stablecoin và các cặp fiat.
    Ví dụ: USDCUSDT → base=USDC → stablecoin ✓
    BTCUSDT → base=BTC → not stablecoin ✓
    """
    # Binance Futures USDT pairs always end with USDT
    if symbol.endswith("USDT"):
        base = symbol[:-4]  # Remove "USDT" suffix
    elif symbol.endswith("BUSD"):
        base = symbol[:-4]
    elif symbol.endswith("USDC"):
        base = symbol[:-4]
    else:
        base = symbol

    if not base or len(base) > 7:
        return False

    stable_bases = {"USDC", "USDT", "BUSD", "DAI", "FDUSD", "TUSD",
                    "USDP", "GUSD", "UST", "USTC", "EURS", "EUR",
                    "GBP", "CHF", "BRL", "TRY", "ZAR", "AUD", "SGD",
                    "CEUR", "SUSD", "HUSD", "LUSD", "ALUSD"}
    return base in stable_bases


class SmartScanner:
    """
    Chiến lược giao dịch chuyên nghiệp:

    Bước 1 — Xu hướng khung 1h
      EMA(50) và EMA(200) trên 1h xác định trend:
      - Price > EMA50 > EMA200 → uptrend (chỉ LONG)
      - Price < EMA50 < EMA200 → downtrend (chỉ SHORT)
      - Xen kẽ → sideway (hạn chế giao dịch)

    Bước 2 — Chờ pullback khung 15m
      Vào lệnh KHI giá pullback về gần EMA(9) hoặc EMA(21) khung 15m
      và có nến xác nhận (bật lại từ EMA).

    Bước 3 — Stop Loss động theo ATR + cấu trúc
      SL = max(entry - ATR_15m * 1.5, swing_low - epsilon)  (LONG)
      Đảm bảo SL nằm dưới vùng quét thanh lý (swing low gần nhất).

    Bước 4 — Take Profit đa mục tiêu
      TP1 = entry + 2 * ATR (chốt 50%)
      TP2 = entry + 3 * ATR (chốt 50%)

    Bước 5 — Lọc RSI
      RSI(14) khung 15m trong vùng 40-60 khi pullback.
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        top_n: int = 10,
        interval_entry: str = "15m",      # Khung vào lệnh
        interval_trend: str = "1h",       # Khung xác định trend
        ema_fast: int = 9,
        ema_slow: int = 21,
        ema_trend_fast: int = 50,
        ema_trend_slow: int = 200,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_sl_mult: float = 1.5,         # SL = ATR * 1.5
        atr_tp1_mult: float = 2.0,        # TP1 = ATR * 2
        atr_tp2_mult: float = 3.0,        # TP2 = ATR * 3
        rsi_entry_min: float = 40.0,
        rsi_entry_max: float = 60.0,
        kline_limit: int = 100,
    ):
        self.client = client
        self.top_n = top_n
        self.interval_entry = interval_entry
        self.interval_trend = interval_trend
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend_fast = ema_trend_fast
        self.ema_trend_slow = ema_trend_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp1_mult = atr_tp1_mult
        self.atr_tp2_mult = atr_tp2_mult
        self.rsi_entry_min = rsi_entry_min
        self.rsi_entry_max = rsi_entry_max
        self.kline_limit = kline_limit

        # Cache EMA 15m để phát hiện crossover
        self._prev_ema_fast: dict[str, float] = {}

        logger.info(
            f"SmartScanner initialized: "
            f"trend={interval_trend} EMA({ema_trend_fast},{ema_trend_slow}), "
            f"entry={interval_entry} EMA({ema_fast},{ema_slow}) RSI({rsi_period}), "
            f"ATR({atr_period}) SL={atr_sl_mult}x TP1={atr_tp1_mult}x TP2={atr_tp2_mult}x"
        )

    def scan(self) -> tuple[str | None, dict | None]:
        """
        Quét top volume coins, thu thập tất cả tín hiệu, chọn tín hiệu 
        có độ tin tưởng CAO NHẤT để vào lệnh (chỉ 1 lệnh).
        
        Returns:
            (symbol, signal_dict) — tín hiệu tốt nhất, hoặc (None, None)
            signal_dict = {
                "side", "entry_price", "rsi", "atr",
                "sl_price", "tp1_price", "tp2_price",
                "trend", "reason", "entry_type",
                "confidence": float,  # 0-100: độ tin tưởng
            }
        """
        logger.info("Smart scanning for trading signals (top 30, best pick)...")

        symbols = self._get_top_symbols()
        if not symbols:
            logger.warning("No symbols returned from exchange")
            return None, None

        all_signals: list[tuple[str, dict]] = []

        for symbol in symbols:
            signal = self._evaluate(symbol)
            if signal:
                # Thêm confidence score
                signal["confidence"] = self._calc_confidence(signal)
                all_signals.append((symbol, signal))

        if not all_signals:
            logger.info("No high-quality signal found among all scanned coins")
            return None, None

        # Sắp xếp theo confidence giảm dần, chọn tín hiệu tốt nhất
        all_signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
        best_symbol, best_signal = all_signals[0]

        logger.info(
            f">>> BEST SIGNAL: {best_signal['side']} {best_symbol} "
            f"(confidence={best_signal['confidence']:.1f}/100)"
        )
        logger.info(
            f"  Entry={best_signal['entry_price']:.2f} | "
            f"RSI={best_signal['rsi']:.1f} | "
            f"ATR={best_signal['atr']:.4f} | "
            f"SL={best_signal['sl_price']:.2f} "
            f"TP1={best_signal['tp1_price']:.2f} TP2={best_signal['tp2_price']:.2f}"
        )
        logger.info(f"  Reason: {best_signal['reason']}")

        # Log top 3 signals
        for i, (sym, sig) in enumerate(all_signals[:3]):
            logger.info(f"  #{i+1}: {sig['side']} {sym} (conf={sig['confidence']:.0f})")

        return best_symbol, best_signal

    def _calc_confidence(self, signal: dict) -> float:
        """
        Tính điểm tin tưởng 0-100 dựa trên nhiều yếu tố:
        - entry_type: cross > near_ema (weight 30)
        - RSI gần 50 càng tốt (weight 25)
        - Trend strength càng cao càng tốt (weight 20)
        - Risk:Reward ratio (weight 25)
        """
        score = 0.0

        # 1. Entry type (0-30 điểm)
        if signal.get("entry_type", "").startswith("cross"):
            score += 30  # EMA crossover đáng tin hơn
        else:
            score += 15  # Chỉ ở gần EMA, ít tin hơn

        # 2. RSI positioning (0-25 điểm)
        rsi_val = signal.get("rsi", 50)
        # Lý tưởng: RSI 45-55 được 25 điểm, xa dần thì giảm
        rsi_distance = abs(rsi_val - 50)
        if rsi_distance <= 5:
            score += 25
        elif rsi_distance <= 10:
            score += 20
        elif rsi_distance <= 15:
            score += 12
        else:
            score += 5

        # 3. Trend strength (0-20 điểm)
        trend_strength = signal.get("trend_strength", 0)
        score += min(trend_strength * 0.2, 20)

        # 4. Risk:Reward (0-25 điểm)
        entry = signal.get("entry_price", 1)
        sl = signal.get("sl_price", entry)
        tp1 = signal.get("tp1_price", entry)
        if signal.get("side") == "LONG":
            risk = entry - sl
            reward = tp1 - entry
        else:
            risk = sl - entry
            reward = entry - tp1

        rr = (reward / risk) if risk > 0 else 0
        if rr >= 3.0:
            score += 25
        elif rr >= 2.5:
            score += 22
        elif rr >= 2.0:
            score += 18
        elif rr >= 1.5:
            score += 12
        else:
            score += 5

        return round(min(score, 100), 1)

    def _get_top_symbols(self) -> list[str]:
        """Lấy top N volume, loại bỏ stablecoin."""
        tickers = self.client.get_top_volume_symbols(limit=self.top_n)
        symbols = [t["symbol"] for t in tickers if "symbol" in t]
        # Lọc stablecoin
        filtered = [s for s in symbols if not is_stablecoin(s)]
        filtered_out = len(symbols) - len(filtered)
        if filtered_out:
            logger.debug(f"Filtered {filtered_out} stablecoins/fiat pairs")
        logger.debug(f"Top {len(filtered)} trading symbols: {', '.join(filtered[:5])}...")
        return filtered

    def _evaluate(self, symbol: str) -> dict | None:
        """
        Đánh giá một symbol qua 5 bước.
        Trả về signal dict nếu tất cả điều kiện đều đạt.
        """
        try:
            # ── Bước 0: Lấy dữ liệu ──
            df_entry = self._get_df(symbol, self.interval_entry)
            df_trend = self._get_df(symbol, self.interval_trend)

            if df_entry is None or df_trend is None:
                return None

            # ── Bước 1: Xác định xu hướng khung 1h ──
            trend, trend_strength = self._detect_trend(df_trend)
            logger.debug(f"{symbol}: trend={trend}, strength={trend_strength}")

            if trend == "sideway":
                logger.debug(f"{symbol}: sideway, skip")
                return None

            # ── Bước 2: Tính toán các chỉ báo khung 15m ──
            close = df_entry["close"]
            ema_f = ema(close, self.ema_fast)
            ema_s = ema(close, self.ema_slow)
            rsi_val = rsi(close, self.rsi_period)
            atr_val = atr(df_entry, self.atr_period)

            if ema_f is None or ema_s is None:
                return None

            current_ema_f = float(ema_f.iloc[-1])
            current_ema_s = float(ema_s.iloc[-1])
            current_rsi = float(rsi_val.iloc[-1])
            current_atr = float(atr_val.iloc[-1])
            current_price = float(close.iloc[-1])

            # ── Bước 3: Xác định tín hiệu ──
            prev_ema_f = self._prev_ema_fast.get(symbol)
            signal = None

            # LONG: xu hướng tăng + pullback về EMA + RSI không quá cao
            if trend == "uptrend":
                # Giá đang ở gần EMA(9) hoặc EMA(21) — pullback
                near_ema = (current_price <= current_ema_s * 1.005 and 
                           current_price >= current_ema_f * 0.995)
                
                # RSI trong vùng cho phép (không quá mua)
                rsi_ok = self.rsi_entry_min <= current_rsi <= self.rsi_entry_max

                # Crossover tăng (EMA9 vừa cắt lên EMA21)
                cross_up = (prev_ema_f is not None and 
                           prev_ema_f <= current_ema_s and 
                           current_ema_f > current_ema_s)

                if (cross_up or near_ema) and rsi_ok:
                    # Tính SL/TP chuyên nghiệp
                    swing_low = find_swing_low(df_entry, 20)
                    sl_calc = current_price - current_atr * self.atr_sl_mult
                    # SL phải nằm dưới swing low để tránh bị quét
                    sl_price = min(sl_calc, swing_low * 0.998)
                    # Nhưng không quá xa (max 3x ATR)
                    sl_price = max(sl_price, current_price - current_atr * 3.0)

                    tp1 = current_price + current_atr * self.atr_tp1_mult
                    tp2 = current_price + current_atr * self.atr_tp2_mult

                    rr1 = (tp1 - current_price) / (current_price - sl_price) if sl_price < current_price else 0
                    rr2 = (tp2 - current_price) / (current_price - sl_price) if sl_price < current_price else 0

                    # Chỉ vào nếu R:R >= 1:1.5
                    if rr1 >= 1.5 or rr2 >= 2.0:
                        signal = {
                            "side": "LONG",
                            "entry_price": current_price,
                            "rsi": current_rsi,
                            "atr": current_atr,
                            "sl_price": round(sl_price, 2),
                            "tp1_price": round(tp1, 2),
                            "tp2_price": round(tp2, 2),
                            "trend": trend,
                            "trend_strength": trend_strength,
                            "reason": f"Pullback EMA + RSI={current_rsi:.0f}",
                            "entry_type": "cross_up" if cross_up else "near_ema",
                        }
                        logger.debug(
                            f"{symbol} LONG: price={current_price:.2f}, "
                            f"EMA9={current_ema_f:.2f}, EMA21={current_ema_s:.2f}, "
                            f"RSI={current_rsi:.1f}, ATR={current_atr:.4f}, "
                            f"SL={sl_price:.2f}, TP1={tp1:.2f}, TP2={tp2:.2f}, "
                            f"R:R1={rr1:.1f}, R:R2={rr2:.1f}"
                        )

            # SHORT: xu hướng giảm + pullback lên EMA
            elif trend == "downtrend":
                near_ema = (current_price >= current_ema_s * 0.995 and 
                           current_price <= current_ema_f * 1.005)

                rsi_ok = self.rsi_entry_min <= current_rsi <= self.rsi_entry_max

                cross_down = (prev_ema_f is not None and 
                             prev_ema_f >= current_ema_s and 
                             current_ema_f < current_ema_s)

                if (cross_down or near_ema) and rsi_ok:
                    swing_high = find_swing_high(df_entry, 20)
                    sl_calc = current_price + current_atr * self.atr_sl_mult
                    sl_price = max(sl_calc, swing_high * 1.002)
                    sl_price = min(sl_price, current_price + current_atr * 3.0)

                    tp1 = current_price - current_atr * self.atr_tp1_mult
                    tp2 = current_price - current_atr * self.atr_tp2_mult

                    rr1 = (current_price - tp1) / (sl_price - current_price) if sl_price > current_price else 0
                    rr2 = (current_price - tp2) / (sl_price - current_price) if sl_price > current_price else 0

                    if rr1 >= 1.5 or rr2 >= 2.0:
                        signal = {
                            "side": "SHORT",
                            "entry_price": current_price,
                            "rsi": current_rsi,
                            "atr": current_atr,
                            "sl_price": round(sl_price, 2),
                            "tp1_price": round(tp1, 2),
                            "tp2_price": round(tp2, 2),
                            "trend": trend,
                            "trend_strength": trend_strength,
                            "reason": f"Pullback EMA + RSI={current_rsi:.0f}",
                            "entry_type": "cross_down" if cross_down else "near_ema",
                        }

            # Cập nhật EMA cache
            self._prev_ema_fast[symbol] = current_ema_f

            return signal

        except Exception as e:
            logger.warning(f"Failed to evaluate {symbol}: {e}")
            return None

    def _detect_trend(self, df: pd.DataFrame) -> tuple[str, float]:
        """
        Xác định xu hướng dựa trên EMA(50, 200) khung 1h.
        Returns (trend, strength).
        strength: 0-100, khoảng cách giữa EMAs cho biết độ mạnh trend.
        """
        close = df["close"]
        ema50 = ema(close, self.ema_trend_fast)
        ema200 = ema(close, self.ema_trend_slow)
        price = float(close.iloc[-1])
        e50 = float(ema50.iloc[-1])
        e200 = float(ema200.iloc[-1])

        if pd.isna(e50) or pd.isna(e200):
            return "sideway", 0

        # Khoảng cách % giữa các EMA
        if e200 != 0:
            strength = min(abs(e50 - e200) / e200 * 1000, 100)
        else:
            strength = 0

        # Uptrend: price > EMA50 > EMA200
        if price > e50 > e200:
            return "uptrend", strength
        # Downtrend: price < EMA50 < EMA200
        elif price < e50 < e200:
            return "downtrend", strength
        else:
            return "sideway", strength

    def _get_df(self, symbol: str, interval: str) -> pd.DataFrame | None:
        """Lấy klines và trả về DataFrame."""
        try:
            klines = self.client.get_klines(symbol, interval, self.kline_limit)
            if not klines or len(klines) < self.kline_limit:
                return None
            return klines_to_df(klines)
        except Exception as e:
            logger.warning(f"Failed to get {interval} data for {symbol}: {e}")
            return None

    def reset_cache(self):
        """Reset EMA cache khi cần."""
        self._prev_ema_fast.clear()
        logger.info("SmartScanner cache cleared")
