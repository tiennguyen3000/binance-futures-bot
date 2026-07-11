"""
Professional signal scanner — scoring-based multi-indicator strategy.
Áp dụng cơ chế scoring từ crypto-trading-bot: mỗi chỉ báo đúng cộng điểm,
cần ≥3 điểm để có tín hiệu (thay vì AND logic nghiêm ngặt).

Chiến lược:
  - Trend 1h (EMA50/200) + MACD
  - Pullback/Re-test các mức kỹ thuật
  - Structure break / Swing point
  - Volume confirmation
  - RSI zone (mở rộng)
  - Ichimoku-like cloud filter
  - Supertrend-like filter
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


def macd(close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9):
    """Return (macd_line, signal_line, histogram)."""
    ema_fast = ema(close, fast)
    ema_slow = ema(close, slow)
    macd_line = ema_fast - ema_slow
    sig_line = ema(macd_line, signal)
    hist = macd_line - sig_line
    return macd_line, sig_line, hist


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
    """Loại bỏ stablecoin và các cặp fiat."""
    if symbol.endswith("USDT"):
        base = symbol[:-4]
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
    Chiến lược giao dịch SCORING-BASED (kế thừa từ crypto-trading-bot):

    Mỗi coin được chấm điểm dựa trên 8 chỉ báo độc lập:
      1. Trend 1h (EMA50/200)       — 2 điểm
      2. MACD 15m crossover         — 1 điểm
      3. Structure break            — 2 điểm
      4. RSI trong vùng cho phép    — 1 điểm
      5. Volume spike               — 1 điểm
      6. Ichimoku-like cloud        — 1 điểm
      7. Supertrend-like (ATR trend) — 1 điểm
      8. Divergence                 — 2 điểm (bonus)

    Yêu cầu: ≥3 điểm cho 1 hướng (LONG/SHORT) và hướng đó > hướng ngược lại.
    KHÔNG yêu cầu EMA crossover, KHÔNG yêu cầu RSI 40-60 cứng nhắc.
    SL rộng hơn = khó bị quét hơn.
    """

    def __init__(
        self,
        client: BinanceFuturesClient,
        top_n: int = 30,
        interval_entry: str = "15m",      # Khung vào lệnh
        interval_trend: str = "1h",       # Khung xác định trend
        interval_macro: str = "4h",       # Khung macro (ichimoku-like)
        ema_fast: int = 9,
        ema_slow: int = 21,
        ema_trend_fast: int = 50,
        ema_trend_slow: int = 200,
        rsi_period: int = 14,
        atr_period: int = 14,
        atr_sl_mult: float = 2.0,         # SL = ATR × 2.0 (rộng hơn)
        atr_tp1_mult: float = 2.5,        # TP1 = ATR × 2.5
        atr_tp2_mult: float = 4.0,        # TP2 = ATR × 4.0
        rsi_range_min: float = 25.0,      # RSI mở rộng
        rsi_range_max: float = 75.0,
        min_score: int = 3,               # Điểm tối thiểu để có tín hiệu
        kline_limit: int = 200,           # Cần 200 nến cho Ichimoku/MA200
    ):
        self.client = client
        self.top_n = top_n
        self.interval_entry = interval_entry
        self.interval_trend = interval_trend
        self.interval_macro = interval_macro
        self.ema_fast = ema_fast
        self.ema_slow = ema_slow
        self.ema_trend_fast = ema_trend_fast
        self.ema_trend_slow = ema_trend_slow
        self.rsi_period = rsi_period
        self.atr_period = atr_period
        self.atr_sl_mult = atr_sl_mult
        self.atr_tp1_mult = atr_tp1_mult
        self.atr_tp2_mult = atr_tp2_mult
        self.rsi_range_min = rsi_range_min
        self.rsi_range_max = rsi_range_max
        self.min_score = min_score
        self.kline_limit = kline_limit

        logger.info(
            f"SmartScanner initialized (SCORING MODE): "
            f"trend={interval_trend} EMA({ema_trend_fast},{ema_trend_slow}), "
            f"entry={interval_entry} RSI({rsi_range_min}-{rsi_range_max}), "
            f"ATR({atr_period}) SL={atr_sl_mult}x TP1={atr_tp1_mult}x TP2={atr_tp2_mult}x "
            f"min_score={min_score}"
        )

    def scan(self) -> tuple[str | None, dict | None]:
        """
        Quét top volume coins, thu thập tất cả tín hiệu, chọn tín hiệu 
        có độ tin tưởng CAO NHẤT để vào lệnh (chỉ 1 lệnh).
        
        Returns:
            (symbol, signal_dict) — tín hiệu tốt nhất, hoặc (None, None)
        """
        logger.info(f"Scoring scan: quét top {self.top_n} coins, cần ≥{self.min_score} điểm...")

        symbols = self._get_top_symbols()
        if not symbols:
            logger.warning("No symbols returned from exchange")
            return None, None

        all_signals: list[tuple[str, dict]] = []

        for symbol in symbols:
            signal = self._evaluate(symbol)
            if signal:
                all_signals.append((symbol, signal))

        if not all_signals:
            logger.info("No scoring signal found among all scanned coins")
            return None, None

        # Sắp xếp theo confidence giảm dần
        all_signals.sort(key=lambda x: x[1]["confidence"], reverse=True)
        best_symbol, best_signal = all_signals[0]

        logger.info(
            f">>> BEST SIGNAL: {best_signal['side']} {best_symbol} "
            f"(score={best_signal['long_score']}L/{best_signal['short_score']}S, "
            f"confidence={best_signal['confidence']:.0f}/100)"
        )
        logger.info(
            f"  Entry={best_signal['entry_price']:.2f} | "
            f"RSI={best_signal['rsi']:.1f} | "
            f"ATR={best_signal['atr']:.4f} | "
            f"SL={best_signal['sl_price']:.2f} "
            f"TP1={best_signal['tp1_price']:.2f} TP2={best_signal['tp2_price']:.2f}"
        )
        logger.info(f"  Reasons: {best_signal['reasons']}")

        # Log top 3 signals
        for i, (sym, sig) in enumerate(all_signals[:3]):
            logger.info(f"  #{i+1}: {sig['side']} {sym} "
                        f"(L{sig['long_score']}/S{sig['short_score']}, "
                        f"conf={sig['confidence']:.0f})")

        return best_symbol, best_signal

    def _get_top_symbols(self) -> list[str]:
        """Lấy top N volume, loại bỏ stablecoin."""
        tickers = self.client.get_top_volume_symbols(limit=self.top_n)
        symbols = [t["symbol"] for t in tickers if "symbol" in t]
        filtered = [s for s in symbols if not is_stablecoin(s)]
        return filtered

    def _evaluate(self, symbol: str) -> dict | None:
        """
        Đánh giá một symbol qua cơ chế SCORING (8 chỉ báo).
        Trả về signal dict nếu điểm đạt ngưỡng.
        """
        try:
            # Lấy dữ liệu 3 khung thời gian
            df_entry = self._get_df(symbol, self.interval_entry)
            df_trend = self._get_df(symbol, self.interval_trend)
            df_macro = self._get_df(symbol, self.interval_macro)

            if df_entry is None or df_trend is None:
                return None

            # Nếu không có macro, dùng trend làm macro
            if df_macro is None:
                df_macro = df_trend

            # ── Tính toán chỉ báo ──
            close_entry = df_entry["close"]
            high_entry = df_entry["high"]
            low_entry = df_entry["low"]
            vol_entry = df_entry["volume"]

            close_trend = df_trend["close"]
            close_macro = df_macro["close"]

            current_price = float(close_entry.iloc[-1])

            # EMA entry
            ema9_entry = ema(close_entry, self.ema_fast)
            ema21_entry = ema(close_entry, self.ema_slow)
            c_ema9 = float(ema9_entry.iloc[-1])
            c_ema21 = float(ema21_entry.iloc[-1])

            # EMA trend
            ema50_trend = ema(close_trend, self.ema_trend_fast)
            ema200_trend = ema(close_trend, self.ema_trend_slow)
            c_ema50 = float(ema50_trend.iloc[-1])
            c_ema200 = float(ema200_trend.iloc[-1])

            # RSI
            rsi_val = rsi(close_entry, self.rsi_period)
            c_rsi = float(rsi_val.iloc[-1])

            # ATR 15m
            atr_val = atr(df_entry, self.atr_period)
            c_atr = float(atr_val.iloc[-1])

            # MACD 15m
            macd_line, macd_signal, macd_hist = macd(close_entry)
            c_macd = float(macd_line.iloc[-1])
            c_macd_sig = float(macd_signal.iloc[-1])
            prev_macd = float(macd_line.iloc[-2]) if len(macd_line) > 1 else c_macd
            prev_macd_sig = float(macd_signal.iloc[-2]) if len(macd_signal) > 1 else c_macd_sig
            macd_bullish = c_macd > c_macd_sig
            macd_cross_up = prev_macd <= prev_macd_sig and c_macd > c_macd_sig
            macd_cross_down = prev_macd >= prev_macd_sig and c_macd < c_macd_sig

            # Volume
            avg_vol = float(vol_entry.tail(20).mean())
            current_vol = float(vol_entry.iloc[-1])
            vol_ratio = current_vol / avg_vol if avg_vol > 0 else 1.0
            volume_spike = current_vol > avg_vol * 1.5

            # Swing points (entry timeframe)
            swing_low = find_swing_low(df_entry, 20)
            swing_high = find_swing_high(df_entry, 20)

            # ── Tính điểm LONG và SHORT ──
            long_score = 0
            short_score = 0
            reasons_long: list[str] = []
            reasons_short: list[str] = []

            # --- Chỉ báo 1: Trend 1h (EMA50/200) — 2 điểm ---
            if current_price > c_ema50 > c_ema200:
                long_score += 2
                reasons_long.append(f"Trend 1h UPTREND (EMA50>{c_ema50:.0f})")
            elif current_price < c_ema50 < c_ema200:
                short_score += 2
                reasons_short.append(f"Trend 1h DOWNTREND (EMA50<{c_ema50:.0f})")

            # --- Chỉ báo 2: MACD 15m — 1 điểm ---
            if macd_cross_up:
                long_score += 1
                reasons_long.append("MACD 15m CROSS UP")
            elif macd_cross_down:
                short_score += 1
                reasons_short.append("MACD 15m CROSS DOWN")
            elif macd_bullish:
                long_score += 1
                reasons_long.append("MACD 15m Bullish")
            else:
                short_score += 1
                reasons_short.append("MACD 15m Bearish")

            # --- Chỉ báo 3: Structure break — 2 điểm ---
            # Giá phá vỡ swing high gần nhất (cho LONG) hoặc swing low (cho SHORT)
            if current_price > swing_high * 1.002:
                long_score += 2
                reasons_long.append(f"Structure BREAK UP (>swingH {swing_high:.2f})")
            elif current_price < swing_low * 0.998:
                short_score += 2
                reasons_short.append(f"Structure BREAK DOWN (<swingL {swing_low:.2f})")

            # --- Chỉ báo 4: RSI trong vùng — 1 điểm ---
            if self.rsi_range_min <= c_rsi <= self.rsi_range_max:
                # Trong vùng cho phép — cả LONG và SHORT đều có thể
                if c_rsi >= 40 and c_rsi <= 60:
                    # Vùng lý tưởng cho pullback
                    if current_price < c_ema21:
                        long_score += 1
                        reasons_long.append(f"RSI {c_rsi:.0f} (pullback zone)")
                    elif current_price > c_ema21:
                        short_score += 1
                        reasons_short.append(f"RSI {c_rsi:.0f} (pullback zone)")
                    else:
                        long_score += 1
                        reasons_long.append(f"RSI {c_rsi:.0f} neutral")
                elif c_rsi > 60:
                    # RSI cao — thiên về SHORT nếu có trend giảm
                    if current_price > c_ema21:
                        short_score += 1
                        reasons_short.append(f"RSI {c_rsi:.0f} overbought zone")
                else:
                    # RSI thấp — thiên về LONG nếu có trend tăng
                    if current_price < c_ema21:
                        long_score += 1
                        reasons_long.append(f"RSI {c_rsi:.0f} oversold zone")

            # --- Chỉ báo 5: Volume spike — 1 điểm ---
            if volume_spike:
                if current_price > c_ema9:
                    long_score += 1
                    reasons_long.append(f"Volume x{vol_ratio:.1f} spike + giá > EMA9")
                elif current_price < c_ema9:
                    short_score += 1
                    reasons_short.append(f"Volume x{vol_ratio:.1f} spike + giá < EMA9")
                else:
                    long_score += 1
                    reasons_long.append(f"Volume x{vol_ratio:.1f} spike")
            elif vol_ratio > 1.2:
                # Volume cao hơn TB nhưng không spike
                pass  # Tín hiệu yếu, không tính điểm

            # --- Chỉ báo 6: Ichimoku-like cloud (macro TF) — 1 điểm ---
            # Tính cloud đơn giản: 52-period high/low
            macro_high_52 = float(df_macro["high"].tail(52).max())
            macro_low_52 = float(df_macro["low"].tail(52).min())
            cloud_top = max(macro_high_52, (macro_high_52 + macro_low_52) / 2)
            cloud_bottom = min(macro_low_52, (macro_high_52 + macro_low_52) / 2)

            if current_price > cloud_top:
                long_score += 1
                reasons_long.append(f"Above Ichimoku Cloud ({cloud_top:.0f})")
            elif current_price < cloud_bottom:
                short_score += 1
                reasons_short.append(f"Below Ichimoku Cloud ({cloud_bottom:.0f})")

            # --- Chỉ báo 7: Supertrend-like (ATR trend) — 1 điểm ---
            # Dùng ATR để xác định xu hướng: giá > EMA21 + ATR*0.5 = uptrend
            if current_price > c_ema21 + c_atr * 0.5:
                long_score += 1
                reasons_long.append(f"ATR trend UP (>{c_ema21:.0f}+ATR*0.5)")
            elif current_price < c_ema21 - c_atr * 0.5:
                short_score += 1
                reasons_short.append(f"ATR trend DOWN (<{c_ema21:.0f}-ATR*0.5)")

            # --- Chỉ báo 8: Divergence check — 2 điểm (bonus) ---
            # RSI divergence đơn giản
            rsi_series_val = rsi_val.tail(20)
            if len(rsi_series_val) >= 20:
                price_20 = close_entry.tail(20)
                # Bullish divergence: giá đáy thấp hơn, RSI đáy cao hơn
                if len(price_20) >= 20:
                    price_low_idx = price_20.idxmin()
                    price_low_pos = price_20.index.get_loc(price_low_idx)
                    if price_low_pos < 18:  # Không phải nến cuối
                        # So sánh với swing low trước đó
                        before_slice = price_20.iloc[:price_low_pos]
                        if len(before_slice) > 3:
                            prev_low = before_slice.min()
                            prev_low_idx = before_slice.idxmin()
                            prev_rsi = rsi_series_val.loc[prev_low_idx]
                            curr_rsi = rsi_series_val.loc[price_low_idx]
                            # Giá thấp hơn → RSI cao hơn = Bullish divergence
                            if price_20.iloc[-1] <= price_20.iloc[price_low_pos] * 1.01 and \
                               curr_rsi > prev_rsi and \
                               curr_rsi - prev_rsi > 5:
                                long_score += 2
                                reasons_long.append("Bullish RSI Divergence")

                # Bearish divergence: giá đỉnh cao hơn, RSI đỉnh thấp hơn
                if len(price_20) >= 20:
                    price_high_idx = price_20.idxmax()
                    price_high_pos = price_20.index.get_loc(price_high_idx)
                    if price_high_pos < 18:
                        before_slice = price_20.iloc[:price_high_pos]
                        if len(before_slice) > 3:
                            prev_high = before_slice.max()
                            prev_high_idx = before_slice.idxmax()
                            prev_rsi = rsi_series_val.loc[prev_high_idx]
                            curr_rsi = rsi_series_val.loc[price_high_idx]
                            if price_20.iloc[-1] >= price_20.iloc[price_high_pos] * 0.99 and \
                               curr_rsi < prev_rsi and \
                               prev_rsi - curr_rsi > 5:
                                short_score += 2
                                reasons_short.append("Bearish RSI Divergence")

            # ── Xác định tín hiệu ──
            signal_side = None
            reasons = []
            final_score = 0

            if long_score >= self.min_score and long_score > short_score:
                signal_side = "LONG"
                reasons = reasons_long[:5]
                final_score = long_score
            elif short_score >= self.min_score and short_score > long_score:
                signal_side = "SHORT"
                reasons = reasons_short[:5]
                final_score = short_score
            else:
                logger.debug(f"{symbol}: score L{long_score}/S{short_score} — below threshold")
                return None

            # ── Tính Entry, SL, TP ──
            # Entry: giá hiện tại (market order)
            entry_price = current_price

            # SL: WIDER = max(ATR-based, structure-based) để khó bị quét hơn
            # (ngược lại với bot cũ dùng min)
            if signal_side == "LONG":
                sl_atr = entry_price - c_atr * self.atr_sl_mult
                sl_struct = swing_low - c_atr * 0.5  # Dưới swing low 1 nửa ATR
                sl_price = min(sl_atr, sl_struct)  # Lấy cái XA hơn = an toàn hơn
                # Giới hạn SL không quá xa (tối đa 3x ATR)
                sl_price = max(sl_price, entry_price - c_atr * 3.0)
                # Đảm bảo khoảng cách tối thiểu 0.3%
                min_dist = entry_price * 0.003
                if entry_price - sl_price < min_dist:
                    sl_price = entry_price - min_dist

                tp1 = entry_price + c_atr * self.atr_tp1_mult
                tp2 = entry_price + c_atr * self.atr_tp2_mult

                rr1 = (tp1 - entry_price) / (entry_price - sl_price) if entry_price > sl_price else 0
            else:
                sl_atr = entry_price + c_atr * self.atr_sl_mult
                sl_struct = swing_high + c_atr * 0.5  # Trên swing high 1 nửa ATR
                sl_price = max(sl_atr, sl_struct)
                sl_price = min(sl_price, entry_price + c_atr * 3.0)
                min_dist = entry_price * 0.003
                if sl_price - entry_price < min_dist:
                    sl_price = entry_price + min_dist

                tp1 = entry_price - c_atr * self.atr_tp1_mult
                tp2 = entry_price - c_atr * self.atr_tp2_mult

                rr1 = (entry_price - tp1) / (sl_price - entry_price) if sl_price > entry_price else 0

            # Chỉ vào nếu R:R ≥ 1.0 (dễ thở hơn so với 1.5 trước đây)
            if rr1 < 1.0:
                logger.debug(f"{symbol}: R:R {rr1:.1f} < 1.0, skip")
                return None

            # ── Confidence score ──
            # Normalize: score / max_possible * 100
            max_possible_score = 12  # 2+1+2+1+1+1+1+2+1(bonus)
            confidence = min(final_score / max_possible_score * 100, 100)

            signal = {
                "side": signal_side,
                "entry_price": round(entry_price, 2),
                "rsi": round(c_rsi, 1),
                "atr": round(c_atr, 4),
                "sl_price": round(sl_price, 2),
                "tp1_price": round(tp1, 2),
                "tp2_price": round(tp2, 2),
                "trend": "uptrend" if signal_side == "LONG" else "downtrend",
                "trend_strength": 50,
                "reason": "; ".join(reasons),
                "reasons": reasons,
                "entry_type": "scoring",
                "confidence": round(confidence, 1),
                "long_score": long_score,
                "short_score": short_score,
                "total_score": final_score,
                "rr1": round(rr1, 2),
                "vol_ratio": round(vol_ratio, 2),
                "volume_spike": volume_spike,
            }

            logger.debug(
                f"{symbol} {signal_side}: score=L{long_score}/S{short_score}, "
                f"price={entry_price:.2f}, RSI={c_rsi:.1f}, ATR={c_atr:.4f}, "
                f"SL={sl_price:.2f}, TP1={tp1:.2f}, TP2={tp2:.2f}, R:R={rr1:.1f}, "
                f"reasons={reasons[:3]}"
            )

            return signal

        except Exception as e:
            logger.warning(f"Failed to evaluate {symbol}: {e}")
            return None

    def _get_df(self, symbol: str, interval: str) -> pd.DataFrame | None:
        """Lấy klines và trả về DataFrame."""
        try:
            klines = self.client.get_klines(symbol, interval, self.kline_limit)
            if not klines or len(klines) < 50:
                return None
            return klines_to_df(klines)
        except Exception as e:
            logger.warning(f"Failed to get {interval} data for {symbol}: {e}")
            return None

    def reset_cache(self):
        """Reset cache khi cần."""
        logger.info("SmartScanner cache cleared")
