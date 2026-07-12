# Trading Bot Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Binance Futures bot fail-safe by default, protect its control plane and order lifecycle, correct deterministic strategy defects, and establish automated regression coverage.

**Architecture:** Introduce a small shared runtime configuration layer, a serialised executor with durable intent/state, and explicit exchange-order verification. Scanner decisions use only closed candles and pure indicator inputs. HTTP control defaults to localhost, requires a bearer token for mutating actions, and exposes no permissive CORS.

**Tech Stack:** Python 3.11+, requests, pandas, stdlib `unittest`, SQLite, Docker Compose.

---

## File structure

- Create: `settings.py` — typed environment configuration and shared risk/control settings.
- Create: `trade_journal.py` — SQLite journal for submitted order intents and reconciled outcomes.
- Create: `tests/` — unittest regression coverage with fake exchange clients.
- Modify: `api_client.py` — structured request failures, Binance clock offset, exchange metadata cache, normalized order results, client order IDs.
- Modify: `executor.py` — lock-protected lifecycle, real-balance risk sizing, verified exchange-side SL, emergency close, managed-order cancellation.
- Modify: `state_manager.py` — thread-safe position lifecycle records.
- Modify: `api_server.py` and `main.py` — localhost/token-protected control plane and explicit safe defaults.
- Modify: `scanner.py` — only closed-candle strategy evaluation, prior-swing breakout, volume baseline, warm-up validation, accurate score metadata.
- Modify: `bot_controller.py`, `telegram_notifier.py`, `README.md`, Docker files — consistent runtime configuration and operational documentation.
- Modify: `hermes_skill.py` — either make contract-compatible and fail-safe or disable direct execution.

### Task 1: Establish test harness and shared settings

**Files:**
- Create: `tests/__init__.py`
- Create: `tests/test_settings.py`
- Create: `settings.py`
- Modify: `requirements.txt`

- [ ] Add failing tests proving production defaults are `TRADING_ENABLED=false`, REST host is loopback, and missing API-control token disables mutating HTTP operations.
- [ ] Run `python -m unittest tests.test_settings -v` and confirm RED.
- [ ] Implement immutable `BotSettings.from_env()` with validated numeric/risk parameters and safe defaults.
- [ ] Run the focused test, then `python -m unittest discover -v`.

### Task 2: Harden Binance client contracts

**Files:**
- Create: `tests/test_api_client.py`
- Modify: `api_client.py`

- [ ] Add failing tests for request transport error without an assigned response; algo-order identifiers; client-order-ID inclusion; and cached trading-symbol validation.
- [ ] Implement `ApiResult`/order result handling while retaining a backward-compatible public result mapping where possible.
- [ ] Add server-time offset, `recvWindow`, error classification, exchange-info caching, market filters, and Decimal-based step/tick normalisation.
- [ ] Run focused and full tests.

### Task 3: Fail-safe serialized order lifecycle

**Files:**
- Create: `tests/test_executor.py`
- Create: `trade_journal.py`
- Modify: `state_manager.py`
- Modify: `executor.py`

- [ ] Add failing tests: SL failure causes emergency close; algo-ID protective orders are accepted; protection is repriced from fill; close failure does not cancel protection first; concurrent closes submit only once; sizing caps loss at configured risk.
- [ ] Implement lock-protected lifecycle states, order intent journal, real balance sizing, exchange protection verification, emergency close, targeted cancellation, and post-close position verification.
- [ ] Run focused/full tests.

### Task 4: Reconcile positions and secure runtime control plane

**Files:**
- Create: `tests/test_api_server.py`
- Create: `tests/test_reconciliation.py`
- Modify: `api_server.py`
- Modify: `main.py`
- Modify: `docker-compose.yml`

- [ ] Add failing tests that mutation endpoints reject absent/incorrect bearer token; CORS is not wildcard; exchange positions without verified SL yield safe halt; unknown excess positions cannot be silently ignored.
- [ ] Implement authenticated mutating endpoints and localhost default; start only when reconciliation classifies all exchange positions; use exchange conditional orders as source of truth and polling solely as watchdog.
- [ ] Do not publish port 8765 by default in Compose.
- [ ] Run tests.

### Task 5: Correct scanner data integrity and score semantics

**Files:**
- Create: `tests/test_scanner.py`
- Modify: `scanner.py`

- [ ] Add failing deterministic-data tests for prior-candle breakout, exclusion of in-progress candle, correct volume baseline, EMA warm-up, and confidence denominator.
- [ ] Implement closed-candle evaluation, use 1h closed price for 1h trend, calculate prior swing levels, make rolling-range label accurate, require warm-up data, and derive weights from one source.
- [ ] Retain TP2 as a reported target but rename/document it until partial exit is implemented.
- [ ] Run tests.

### Task 6: Remove stale execution entry points and align operations/docs

**Files:**
- Modify: `hermes_skill.py`
- Modify: `bot_controller.py`
- Modify: `telegram_notifier.py`
- Modify: `README.md`
- Modify: `env_template`
- Modify: `.env.live.template`
- Modify: `Dockerfile`

- [ ] Add failing contract tests for the Hermes scan/status path and safety guard against direct open actions.
- [ ] Make Hermes integration scan/status-only by default; direct trading requires an explicit dedicated env opt-in and uses the same settings/executor contract.
- [ ] Align user-visible values with actual settings; document testnet-first deployment, API token setup, emergency behavior, risk model, and validation command.
- [ ] Run tests and compile all modules.

### Task 7: Verify end-to-end and commit

**Files:** all touched files

- [ ] Run `python -m unittest discover -v`, `python -m py_compile *.py`, `git diff --check`, and a Docker Compose config render.
- [ ] Inspect diff for secret leakage and ensure no `.env`, journal database, or local state is tracked.
- [ ] Commit logical change groups with conventional messages.
