"""Closed-candle, scoring-based market scanner."""
from __future__ import annotations

import logging

import numpy as np
import pandas as pd

from api_client import BinanceFuturesClient

logger = logging.getLogger(__name__)


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain, loss = delta.clip(lower=0), -delta.clip(upper=0)
    rs = gain.ewm(span=period, adjust=False).mean() / loss.ewm(span=period, adjust=False).mean().replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def atr(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high, low, close = df["high"].astype(float), df["low"].astype(float), df["close"].astype(float)
    previous = close.shift(1)
    true_range = pd.concat([(high-low).abs(), (high-previous).abs(), (low-previous).abs()], axis=1).max(axis=1)
    return true_range.ewm(span=period, adjust=False).mean()


def macd(close: pd.Series):
    line = ema(close, 12) - ema(close, 26)
    signal = ema(line, 9)
    return line, signal, line - signal


def klines_to_df(klines: list) -> pd.DataFrame:
    columns = ["timestamp", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "count", "taker_buy_vol", "taker_buy_quote", "ignore"]
    frame = pd.DataFrame(klines, columns=columns)
    for name in ("open", "high", "low", "close", "volume"):
        frame[name] = frame[name].astype(float)
    return frame


def is_stablecoin(symbol: str) -> bool:
    base = symbol[:-4] if symbol.endswith("USDT") else symbol
    return base in {"USDC", "BUSD", "TUSD", "FDUSD", "DAI", "USDP", "GUSD", "UST", "USTC", "EUR", "GBP", "CHF", "BRL", "TRY", "ZAR", "AUD", "SGD"}


class SmartScanner:
    """Generates signals from completed candles only; no intrabar/repainting inputs."""
    WEIGHTS = {"trend": 2, "macd": 1, "structure": 2, "rsi": 1, "volume": 1, "range_breakout": 1, "atr_trend": 1}

    def __init__(self, client: BinanceFuturesClient, top_n: int = 30, interval_entry: str = "15m", interval_trend: str = "1h", interval_macro: str = "4h", max_funding_rate_pct: float = 0.1, ema_fast: int = 9, ema_slow: int = 21, ema_trend_fast: int = 50, ema_trend_slow: int = 200, rsi_period: int = 14, atr_period: int = 14, atr_sl_mult: float = 2.0, atr_tp1_mult: float = 2.5, atr_tp2_mult: float = 4.0, rsi_range_min: float = 25, rsi_range_max: float = 75, min_score: int = 3, kline_limit: int = 600):
        self.client, self.top_n = client, top_n
        self.interval_entry, self.interval_trend, self.interval_macro = interval_entry, interval_trend, interval_macro
        self.max_funding_rate_pct, self.ema_fast, self.ema_slow = max_funding_rate_pct, ema_fast, ema_slow
        self.ema_trend_fast, self.ema_trend_slow = ema_trend_fast, ema_trend_slow
        self.rsi_period, self.atr_period = rsi_period, atr_period
        self.atr_sl_mult, self.atr_tp1_mult, self.atr_tp2_mult = atr_sl_mult, atr_tp1_mult, atr_tp2_mult
        self.rsi_range_min, self.rsi_range_max, self.min_score = rsi_range_min, rsi_range_max, min_score
        self.kline_limit = max(kline_limit, ema_trend_slow + 100)
        self.min_rr = 1.5  # R:R threshold, adjustable via Telegram /rr

    @staticmethod
    def _closed(frame: pd.DataFrame) -> pd.DataFrame:
        """Exclude Binance's in-progress final kline before calculating any signal."""
        return frame.iloc[:-1].copy()

    @staticmethod
    def _volume_ratio(volumes: pd.Series, baseline: int = 20) -> float:
        if len(volumes) < baseline + 1:
            return 0.0
        average = float(volumes.iloc[-(baseline + 1):-1].mean())
        return float(volumes.iloc[-1]) / average if average else 0.0

    @staticmethod
    def _breakout(frame: pd.DataFrame, side: str, lookback: int = 20) -> bool:
        if len(frame) < lookback + 1:
            return False
        signal_close = float(frame["close"].iloc[-1])
        preceding = frame.iloc[-(lookback + 1):-1]
        reference = float(preceding["high"].max()) if side == "LONG" else float(preceding["low"].min())
        return signal_close > reference if side == "LONG" else signal_close < reference

    def _get_top_symbols(self) -> list[str]:
        # Ask more than N before filtering stablecoins, then cap the valid universe.
        return [item["symbol"] for item in self.client.get_top_volume_symbols(limit=self.top_n * 2) if not is_stablecoin(item.get("symbol", ""))][:self.top_n]

    def scan(self, eligible=None) -> tuple[str | None, dict | None]:
        signals = []
        for symbol in self._get_top_symbols():
            signal = self._evaluate(symbol)
            if signal and (eligible is None or eligible(symbol, signal)):
                signals.append((symbol, signal))
        if not signals:
            return None, None
        return max(signals, key=lambda item: item[1]["confidence"])

    def scan_all(self) -> list[dict]:
        """Return full scan results for all symbols (incl. R:R failures) sorted by score."""
        results = []
        for symbol in self._get_top_symbols():
            try:
                entry, trend, macro = (self._get_df(symbol, self.interval_entry),
                                       self._get_df(symbol, self.interval_trend),
                                       self._get_df(symbol, self.interval_macro))
                if entry is None or trend is None:
                    continue
                macro = macro if macro is not None else trend
                funding = abs(self.client.get_funding_rate(symbol)) * 100
                close, trend_close = entry["close"], trend["close"]
                price, trend_price = float(close.iloc[-1]), float(trend_close.iloc[-1])
                e9, e21 = float(ema(close, self.ema_fast).iloc[-1]), float(ema(close, self.ema_slow).iloc[-1])
                e50, e200 = float(ema(trend_close, self.ema_trend_fast).iloc[-1]), float(ema(trend_close, self.ema_trend_slow).iloc[-1])
                rsi_v, atr_v = float(rsi(close, self.rsi_period).iloc[-1]), float(atr(entry, self.atr_period).iloc[-1])
                line, sig, _ = macd(close)
                ratio = self._volume_ratio(entry["volume"])
                lo, sh = 0, 0
                if trend_price > e50 > e200: lo += 2
                elif trend_price < e50 < e200: sh += 2
                if line.iloc[-2] <= sig.iloc[-2] and line.iloc[-1] > sig.iloc[-1]: lo += 1
                elif line.iloc[-2] >= sig.iloc[-2] and line.iloc[-1] < sig.iloc[-1]: sh += 1
                if self._breakout(entry, "LONG"): lo += 2
                elif self._breakout(entry, "SHORT"): sh += 2
                if self.rsi_range_min <= rsi_v <= self.rsi_range_max:
                    if price < e21: lo += 1
                    elif price > e21: sh += 1
                if ratio >= 1.5:
                    if price >= e9: lo += 1
                    else: sh += 1
                h52 = float(macro["high"].tail(52).max())
                l52 = float(macro["low"].tail(52).min())
                if price > h52: lo += 1
                elif price < l52: sh += 1
                if price > e21 + atr_v * .5: lo += 1
                elif price < e21 - atr_v * .5: sh += 1
                side = "LONG" if lo >= self.min_score and lo > sh else "SHORT" if sh >= self.min_score and sh > lo else None
                rr = sl = tp1 = 0.0
                passed = False
                fail = ""
                if side and atr_v > 0:
                    swing = float(entry["low"].iloc[-21:-1].min()) if side == "LONG" else float(entry["high"].iloc[-21:-1].max())
                    sl = min(price - atr_v * self.atr_sl_mult, swing - atr_v * .5) if side == "LONG" else max(price + atr_v * self.atr_sl_mult, swing + atr_v * .5)
                    tp1 = price + atr_v * self.atr_tp1_mult if side == "LONG" else price - atr_v * self.atr_tp1_mult
                    rr = abs(tp1 - price) / abs(price - sl) if abs(price - sl) > 1e-8 else 0
                    passed = rr >= self.min_rr and funding <= self.max_funding_rate_pct
                    if not passed:
                        if rr < self.min_rr:
                            fail = f"R:R {rr:.2f}<{self.min_rr}"
                        if funding > self.max_funding_rate_pct:
                            fail += f" fund {funding:.3f}%>{self.max_funding_rate_pct}%" if fail else f"fund {funding:.3f}%>{self.max_funding_rate_pct}%"
                elif side is None:
                    if max(lo, sh) < self.min_score:
                        fail = f"score {max(lo,sh)}<{self.min_score}"
                    else:
                        fail = f"L{lo}=S{sh}"
                else:
                    fail = "ATR=0"
                results.append(dict(symbol=symbol, price=round(price, 4), rsi=round(rsi_v, 1),
                    atr=round(atr_v, 4), funding=round(funding, 3), lo=lo, sh=sh,
                    side=side or "-", rr=round(rr, 2), sl=round(sl, 2), tp1=round(tp1, 2),
                    vol=round(ratio, 2), ok=passed, fail=fail))
            except Exception:
                continue
        results.sort(key=lambda r: (r["ok"], r["lo"] + r["sh"]), reverse=True)
        return results

    def _get_df(self, symbol: str, interval: str) -> pd.DataFrame | None:
        raw = self.client.get_klines(symbol, interval, self.kline_limit)
        if not raw:
            return None
        closed = self._closed(klines_to_df(raw))
        return closed if len(closed) >= self.ema_trend_slow + 1 else None

    def _funding_is_acceptable(self, symbol: str) -> bool:
        funding_rate = self.client.get_funding_rate(symbol)
        if funding_rate is None:
            logger.warning("Skipping %s because funding cannot be verified", symbol)
            return False
        return abs(funding_rate) * 100 <= self.max_funding_rate_pct

    @staticmethod
    def _prior_range(frame: pd.DataFrame, periods: int) -> tuple[float, float]:
        if len(frame) < periods + 1:
            raise ValueError("Insufficient macro candles for prior range")
        preceding = frame.iloc[-(periods + 1):-1]
        return float(preceding["high"].max()), float(preceding["low"].min())

    def _evaluate(self, symbol: str) -> dict | None:
        try:
            entry, trend, macro = self._get_df(symbol, self.interval_entry), self._get_df(symbol, self.interval_trend), self._get_df(symbol, self.interval_macro)
            if entry is None or trend is None or macro is None:
                return None
            if not self._funding_is_acceptable(symbol):
                return None
            close, trend_close = entry["close"], trend["close"]
            price, trend_price = float(close.iloc[-1]), float(trend_close.iloc[-1])
            e9, e21, e50, e200 = float(ema(close, self.ema_fast).iloc[-1]), float(ema(close, self.ema_slow).iloc[-1]), float(ema(trend_close, self.ema_trend_fast).iloc[-1]), float(ema(trend_close, self.ema_trend_slow).iloc[-1])
            rsi_value, atr_value = float(rsi(close, self.rsi_period).iloc[-1]), float(atr(entry, self.atr_period).iloc[-1])
            line, signal, _ = macd(close)
            ratio = self._volume_ratio(entry["volume"])
            long, short, reasons_long, reasons_short = 0, 0, [], []
            if trend_price > e50 > e200: long += 2; reasons_long.append("1h closed-candle uptrend")
            elif trend_price < e50 < e200: short += 2; reasons_short.append("1h closed-candle downtrend")
            if line.iloc[-2] <= signal.iloc[-2] and line.iloc[-1] > signal.iloc[-1]: long += 1; reasons_long.append("15m MACD cross up")
            elif line.iloc[-2] >= signal.iloc[-2] and line.iloc[-1] < signal.iloc[-1]: short += 1; reasons_short.append("15m MACD cross down")
            if self._breakout(entry, "LONG"): long += 2; reasons_long.append("close broke prior swing high")
            elif self._breakout(entry, "SHORT"): short += 2; reasons_short.append("close broke prior swing low")
            if self.rsi_range_min <= rsi_value <= self.rsi_range_max:
                if price < e21: long += 1; reasons_long.append("RSI pullback zone")
                elif price > e21: short += 1; reasons_short.append("RSI pullback zone")
            if ratio >= 1.5:
                if price >= e9: long += 1; reasons_long.append(f"closed volume spike x{ratio:.1f}")
                else: short += 1; reasons_short.append(f"closed volume spike x{ratio:.1f}")
            high52, low52 = self._prior_range(macro, 52)
            if price > high52: long += 1; reasons_long.append("52-period range breakout")
            elif price < low52: short += 1; reasons_short.append("52-period range breakdown")
            if price > e21 + atr_value * .5: long += 1; reasons_long.append("ATR trend up")
            elif price < e21 - atr_value * .5: short += 1; reasons_short.append("ATR trend down")
            side = "LONG" if long >= self.min_score and long > short else "SHORT" if short >= self.min_score and short > long else None
            if side is None or atr_value <= 0:
                return None
            swing = float(entry["low"].iloc[-21:-1].min()) if side == "LONG" else float(entry["high"].iloc[-21:-1].max())
            sl = min(price - atr_value * self.atr_sl_mult, swing - atr_value * .5) if side == "LONG" else max(price + atr_value * self.atr_sl_mult, swing + atr_value * .5)
            tp1 = price + atr_value * self.atr_tp1_mult if side == "LONG" else price - atr_value * self.atr_tp1_mult
            tp2 = price + atr_value * self.atr_tp2_mult if side == "LONG" else price - atr_value * self.atr_tp2_mult
            score, reasons = (long, reasons_long) if side == "LONG" else (short, reasons_short)
            rr1 = abs(tp1-price) / abs(price-sl)
            if rr1 < self.min_rr:
                return None
            return {"side": side, "entry_price": price, "rsi": round(rsi_value, 1), "atr": atr_value, "sl_price": sl, "tp1_price": tp1, "tp2_price": tp2, "reasons": reasons, "reason": "; ".join(reasons), "confidence": round(score / sum(self.WEIGHTS.values()) * 100, 1), "long_score": long, "short_score": short, "total_score": score, "rr1": round(rr1, 2), "vol_ratio": round(ratio, 2), "volume_spike": ratio >= 1.5, "data_policy": "closed_candles_only", "tp2_note": "informational target; partial exit is not implemented"}
        except Exception as exc:
            logger.warning("Signal evaluation failed for %s: %s", symbol, exc)
            return None

    def reset_cache(self) -> None:
        logger.info("Scanner has no mutable market-data cache")
