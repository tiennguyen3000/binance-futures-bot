# Two-Setup Signal Design

**Status:** Approved by user on 2026-07-12

## Goal
Replace the scanner's additive score model with two explicit, deterministic entry setups: trend-breakout and trend-pullback.

## Shared controls
- Use completed candles only.
- A direction exists only when 1h close, EMA50, and EMA200 align: `close > EMA50 > EMA200` for LONG or `close < EMA50 < EMA200` for SHORT.
- Funding must be readable and within the configured threshold.
- All executable signals use `TP1 = 2.0R`, where `R = abs(entry - stop)`.
- Prior 52 closed 1h candles define structural resistance/support. LONG is rejected if the prior-52 high is above entry but below TP1. SHORT is rejected if the prior-52 low is below entry but above TP1. A price already beyond that range has no internal barrier.
- MACD is recorded as optional context only. It never gates entry.

## Setup A: Trend-breakout
LONG:
1. 1h uptrend.
2. Last closed 15m close is above the high of the preceding 20 closed 15m candles.
3. Last closed 15m volume is at least 1.5 times the mean of the preceding 20 closed 15m candles.
4. Stop is below the breakout structure: `min(prior_20_low - 0.5 ATR, entry - 1.5 ATR)`. This guarantees a stop distance of at least 1 ATR and no more than 1.5 ATR.
5. TP1 is 2R and must clear the 1h structural resistance filter.

SHORT is the exact inverse using a close below the prior-20 low, stop above `max(prior_20_high + 0.5 ATR, entry + 1.5 ATR)`, and support validation.

## Setup B: Trend-pullback
LONG:
1. 1h uptrend.
2. Over the last two completed 15m candles, price touched or crossed EMA21 (`prior low <= prior EMA21`).
3. The latest completed candle closes above EMA21 and is a bullish rejection or bullish engulfing. Bullish rejection has a lower wick at least the body size. Bullish engulfing closes above the preceding open and opens at/below the preceding close.
4. Stop is below the 20-candle swing low minus 0.5 ATR.
5. TP1 is 2R and must clear the 1h structural resistance filter.

SHORT is the exact inverse.

## Signal schema
A valid signal has `setup` (`trend_breakout` or `trend_pullback`), `side`, entry/SL/TP1/TP2, fixed `rr1=2.0`, reasons, optional MACD context, volume ratio, and `confidence=100.0` for backward-compatible consumers. Legacy score fields are removed.

## Testing
Use deterministic synthetic closed-candle frames to cover LONG/SHORT valid signals, each rejection condition, SL bounds, 2R arithmetic, S/R blocking, MACD non-gating, and no fallback to score behavior.
