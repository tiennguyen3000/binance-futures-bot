# Binance Futures Trading Bot

A Binance USD-M Futures bot designed for testnet-first operation. Supports multi-position entry, in-process SL/TP price monitoring, scoring-based multi-indicator scanning, and Telegram command control. Use it only after independent strategy validation and with capital you can lose.

## Branch: `feat/trading-bot-hardening`

## Safety model

- Default: `TRADING_ENABLED=false`; signals may be scanned but no entries are submitted.
- Default: `BINANCE_TESTNET=true`; testnet keys used unless `.env.live` is configured and `/live` is set.
- Mutating REST calls require both `ENABLE_API_MUTATIONS=true` and a matching `Authorization: Bearer <API_C...EN>` header.
- **SAFE_HALT**: On startup, every exchange position must have an exchange-native stop. An unknown/unprotected position puts the bot into SAFE_HALT, disabling new entries. Use `/resume` or `/start` to force-resume (with warning).
- **In-process SL/TP monitor**: A dedicated thread checks price every 5 seconds and closes positions when SL or TP is triggered. This acts as a safety net when exchange STOP_MARKET/TAKE_PROFIT_MARKET orders are unavailable (e.g. testnet).
- **No emergency close on SL/TP failure**: If exchange stop-loss or take-profit orders fail, the bot logs a warning and keeps the position open with local SL/TP tracking — position is still monitored and will be closed when price hits the threshold.
- Position size is risk-based from available USDT and entry-to-stop distance, then capped by 10% equity margin allocation at configured leverage.
- The scanner calculates signals using completed candles only. It excludes the in-progress Binance kline.

These controls reduce risk; they do not guarantee profitability or protect against exchange/API/network failures.

## Features

| Feature | Description |
|---------|-------------|
| **Multi-position entry** | `while` loop scans and opens up to `MAX_POSITIONS` per cycle (set via `/maxpos`, default 1, max 5) |
| **Scoring-based scanner** | 7 indicators, 9 max points, ≥3 points + R:R ≥ threshold to enter |
| **Adjustable R:R** | Telegram `/rr <value>` (0.5–5.0), dynamically applied |
| **In-process SL/TP** | Monitor thread checks price every 5s, closes at SL/TP |
| **Telegram control** | `/start`, `/stop`, `/rr`, `/maxpos`, `/funding`, `/scanlist`, `/position`, `/pnl`, `/status`, `/resume`, `/help` |
| **REST API** | `GET /status`, `/positions`, `/balance`, `/config` + mutation endpoints |
| **Scan results table** | Telegram `/scanlist` shows full table with score, R:R, funding, and failure reason (bold) |
| **Reconciliation** | Tracks unprotected exchange positions as UNKNOWN + SAFE_HALT, visible via `/position` |
| **Exchange sync** | `check_and_sync_positions()` removes closed positions from local tracker |

## Strategy implementation

The scanner evaluates tradable USDT perpetuals by 24h quote volume, excludes stablecoin bases, and evaluates only closed 15m/1h/4h candles:

| # | Indicator | Max pts | LONG trigger | SHORT trigger |
|---|-----------|:-------:|--------------|---------------|
| 1 | **1h Trend** (EMA50/200) | 2 | price > EMA50 > EMA200 | price < EMA50 < EMA200 |
| 2 | **MACD cross** 15m | 1 | line crosses up | line crosses down |
| 3 | **Structure break** | 2 | breaks prior swing high | breaks prior swing low |
| 4 | **RSI pullback** (25–75) | 1 | RSI in zone + price < EMA21 | RSI in zone + price > EMA21 |
| 5 | **Volume spike** (≥1.5x avg20) | 1 | spike + price ≥ EMA9 | spike + price < EMA9 |
| 6 | **Range breakout** (52 candles) | 1 | price > 52-high | price < 52-low |
| 7 | **ATR trend** | 1 | price > EMA21 + ATR×0.5 | price < EMA21 − ATR×0.5 |

**Entry requires:** ≥3 points for one direction AND that direction > opposite. R:R ≥ threshold (adjustable via `/rr`).

## Setup

### Local (macOS/Linux)

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp env_template .env
# add TESTNET keys only
python main.py --test --verbose
```

### Docker

```bash
docker compose build
docker compose up -d
```

The container mounts `.env` (testnet) and `.env.live` (live) as read-only secrets, persists logs and `.bot_state/`.

## Telegram Commands

| Command | Function |
|---------|----------|
| `/start` | Enable trading + clear SAFE_HALT |
| `/stop` | Disable trading |
| `/scan N` | Set number of coins to scan (5–100) |
| `/rr N` | Set R:R threshold (0.5–5.0) |
| `/maxpos N` | Set max positions (1–5) |
| `/funding P` | Set max funding rate % (0.001–0.5) |
| `/scanlist` | Show detailed scan results table |
| `/position` | Show open positions with PnL |
| `/pnl` | P&L summary |
| `/status` | Dashboard overview |
| `/resume` | Force-resume from SAFE_HALT (warning: risk) |
| `/testnet` | Switch to testnet (requires restart) |
| `/live` | Switch to live (requires restart) |
| `/help` | Command list |

## REST control plane

Read-only endpoints: `GET /status`, `/positions`, `/balance`, `/config`.

Mutation endpoints (`POST /start`, `/stop`, `/scan`, `/switch`, `/close`) are disabled by default. When explicitly enabled, supply:

```text
Authorization: Bearer <API_C...EN>
```

Do not expose this Python server directly to the Internet. Use localhost, a private VPN, or an authenticated reverse proxy.

## Testing

```bash
python -m unittest discover -v
python -m py_compile *.py
```

Tests cover fail-safe defaults, control-plane authorization, stopped-order handling, reconciliation, and closed-candle scanner invariants. They do not substitute for testnet integration testing.

## Operational notes

- Set API keys with Futures trading permission only; disable withdrawals and use IP allowlisting where possible.
- Inspect `.bot_state/trade_journal.sqlite3` only locally; it is ignored by Git.
- A transport timeout after order submission is an unknown exchange outcome; inspect Binance orders/positions before retrying manually.
- On testnet, STOP_MARKET/TAKE_PROFIT_MARKET orders may fail (−4120). The bot keeps the position and relies on the in-process SL/TP monitor (5s interval) instead.
- Realized PnL and exit reason should be reconciled with exchange fills/income history before using results for strategy evaluation.
