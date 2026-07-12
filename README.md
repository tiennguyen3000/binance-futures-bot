# Binance Futures Trading Bot

A Binance USD-M Futures bot designed for testnet-first operation. This project can submit leveraged orders; use it only after independent strategy validation and with capital you can lose.

## Safety model

- Default: `TRADING_ENABLED=false`; signals may be scanned but no entries are submitted.
- Default REST binding is `127.0.0.1`; Docker Compose intentionally publishes no host port.
- Mutating REST calls require both `ENABLE_API_MUTATIONS=true` and a matching `Authorization: Bearer <API_CONTROL_TOKEN>` header.
- An entry is considered successful only after exchange-side stop-loss acceptance. If SL placement fails, the executor attempts an emergency reduce-only close.
- On startup, every exchange position must have an exchange-native stop. An unknown/unprotected position puts the bot into `SAFE_HALT`, disabling new entries.
- Position size is risk-based from available USDT and entry-to-stop distance, then capped by 10% equity margin allocation at configured leverage.
- The scanner calculates signals using completed candles only. It excludes the in-progress Binance kline.

These controls reduce risk; they do not guarantee profitability or protect against exchange/API/network failures.

## Strategy implementation

The scanner evaluates tradable USDT perpetuals by 24h quote volume, excludes stablecoin bases, and evaluates only closed 15m/1h/4h candles:

- 1h EMA(50)/EMA(200) regime, using 1h close rather than 15m price.
- 15m MACD cross.
- Close beyond a swing level calculated from prior candles.
- RSI pullback, closed-candle volume spike against prior 20 closed candles, 52-period range break, and ATR trend confirmation.
- ATR stop and target calculation; TP1 must satisfy R:R >= 1.5.

`TP2` is informational only. Partial take-profit/trailing exit is not implemented and must not be assumed.

## Setup

```bash
uv venv .venv
uv pip install --python .venv/Scripts/python.exe -r requirements.txt pytest
copy env_template .env
# add TESTNET keys only
.venv/Scripts/python.exe main.py --test --verbose
```

Start with Binance testnet. Do not set `TRADING_ENABLED=true` until the following all hold:

1. You validated order endpoint behaviour on your Binance testnet account.
2. Startup reconciliation reports no unprotected positions.
3. You have reviewed exchange precision, margin-mode and position-mode compatibility.
4. You have completed a fee/slippage/funding-aware out-of-sample backtest plus paper/testnet observation.

## Testing

```bash
.venv/Scripts/python.exe -m unittest discover -v
.venv/Scripts/python.exe -m py_compile *.py
```

Tests cover fail-safe defaults, control-plane authorization, stopped-order emergency handling, reconciliation, and closed-candle scanner invariants. They do not substitute for testnet integration testing.

## REST control plane

Read-only endpoints: `GET /status`, `/positions`, `/balance`, `/config`.

Mutation endpoints (`POST /start`, `/stop`, `/scan`, `/switch`, `/close`) are disabled by default. When explicitly enabled, supply:

```text
Authorization: Bearer <API_CONTROL_TOKEN>
```

Do not expose this Python server directly to the Internet. Use localhost, a private VPN, or an authenticated reverse proxy.

## Operational notes

- Set API keys with Futures trading permission only; disable withdrawals and use IP allowlisting where possible.
- Inspect `.bot_state/trade_journal.sqlite3` only locally; it is ignored by Git.
- A transport timeout after order submission is an unknown exchange outcome; inspect Binance orders/positions before retrying manually.
- Realized PnL and exit reason should be reconciled with exchange fills/income history before using results for strategy evaluation.
