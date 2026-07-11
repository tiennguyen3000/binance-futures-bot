# 🤖 Binance Futures Trading Bot — @tiennk_future_auto_trading_bot

Bot giao dịch tự động **Binance Futures** với chiến lược **đa khung thời gian chuyên nghiệp**. Điều khiển qua Telegram, REST API, hoặc chạy hoàn toàn tự động.

> ⚠️ **Bot này dành cho mục đích giáo dục và giao dịch cá nhân. Sử dụng tiền thật có rủi ro mất vốn. Luôn kiểm tra kỹ trên testnet trước khi dùng live.**

---

## 📊 Chiến Lược Giao Dịch

### 5 bước thực chiến:

1. **Xu hướng khung 1h** — EMA(50) > EMA(200) → uptrend (chỉ LONG). EMA(50) < EMA(200) → downtrend (chỉ SHORT).
2. **Pullback khung 15m** — Giá pullback về gần EMA(9) hoặc EMA(21) + RSI(14) trong vùng **40-60**.
3. **Điểm vào** — EMA9 vừa cắt EMA21 (crossover) hoặc giá ở gần EMA.
4. **Stop Loss động theo ATR** — SL = entry ± ATR × 1.5, đảm bảo nằm ngoài swing low gần nhất (tránh bị quét).
5. **Take Profit đa mục tiêu** — TP1 = entry ± ATR × 2 (chốt lời), TP2 = entry ± ATR × 3.

**Lọc bổ sung:**
- Chỉ vào lệnh khi **R:R ≥ 1:1.5**
- **Stablecoin filter** — tự động loại USDC, BUSD, DAI, FDUSD, v.v.
- **Confidence scoring** — chọn tín hiệu tốt nhất trong top 30 coin (EMA crossover > near-EMA, RSI gần 50, trend mạnh, R:R tốt)
- **Optional DeepSeek AI filter** — tín hiệu qua AI kiểm tra trước khi vào lệnh

### Thông số mặc định:

| Tham số | Giá trị |
|---------|---------|
| Đòn bẩy | 10x |
| Rủi ro/lệnh | 1.5% vốn |
| Vốn | 100 USDT |
| Số coin quét | Top 30 volume |
| Tần suất quét | 120 giây |
| SL động | ATR × 1.5 (dưới swing low) |
| TP1 | ATR × 2 |
| TP2 | ATR × 3 |
| Tối đa vị thế | 1 |

---

## ⚙️ Cách Hoạt Động Chi Tiết

### Sơ đồ luồng tổng thể

```
┌─────────────────────────────────────────────────────────┐
│                  KHỞI ĐỘNG BOT                          │
│  1. Load .env (testnet/live theo mode đã lưu)           │
│  2. Init BinanceFuturesClient (kết nối Binance API)     │
│  3. Init StateManager (track vị thế)                    │
│  4. Init OrderExecutor (tính toán vào lệnh)             │
│  5. Init SmartScanner (cấu hình chỉ báo)                │
│  6. Mở Telegram polling thread (lắng nghe lệnh)         │
│  7. Mở REST API server thread (port 8765)               │
│  8. Gửi Telegram: "🤖 Bot đã khởi động"                 │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│              VÒNG LẶP CHÍNH (mỗi 120s)                  │
│                                                         │
│  Bước 1: Kiểm tra vị thế đã đóng chưa                   │
│   → check_and_sync_positions()                          │
│   → Nếu SL/TP bị quét, cập nhật state + Telegram       │
│                                                         │
│  Bước 2: Cập nhật trạng thái cho Telegram               │
│   → Lấy PnL real-time từ exchange                       │
│   → Gán vào bot_cfg.positions / balance / total_pnl     │
│                                                         │
│  Bước 3: Kiểm tra restart_needed từ Telegram            │
│   → Nếu có (/testnet hoặc /live), shutdown ngay         │
│                                                         │
│  Bước 4: Kiểm tra còn slot mở lệnh không?              │
│   → can_open() = True?                                  │
│   → trading_enabled từ Telegram có BẬT không?           │
│   → Balance ≥ 50 USDT?                                  │
│                                                         │
│  Bước 5: QUÉT TÍN HIỆU (SmartScanner)                  │
│   → Lấy top 30 volume từ Binance                        │
│   → Lọc stablecoin (USDC, BUSD, FDUSD...)               │
│   → Với mỗi coin:                                       │
│     ├─ Lấy kline 1h (100 nến) → xác định trend          │
│     ├─ Lấy kline 15m (100 nến) → tính EMA/RSI/ATR      │
│     ├─ Đánh giá 5 bước → signal hoặc None               │
│     └─ Tính confidence score (0-100)                    │
│   → Chọn tín hiệu có confidence CAO NHẤT               │
│                                                         │
│  Bước 6: [Optional] DeepSeek AI filter                  │
│   → Nếu bật --deepseek, kiểm tra verdict               │
│   → "no" → skip, "yes" → continue                       │
│                                                         │
│  Bước 7: VÀO LỆNH (OrderExecutor)                      │
│   → set_leverage(10x) + isolated margin                 │
│   → calculate_position_size(risk=1.5%)                  │
│   → place_market_order(BUY/SELL)                        │
│   → place_stop_loss (reduce-only @ sl_price)            │
│   → place_take_profit (reduce-only @ tp1_price)         │
│   → StateManager.add_position()                         │
│   → Telegram: 🟢/🔴 TÍN HIỆU + 🟢/🔴 ĐÃ VÀO LỆNH       │
│                                                         │
│  Bước 8: Ngủ 120 giây (hoặc đến khi nhận shutdown)     │
└─────────────────────────────────────────────────────────┘
```

### Chi tiết từng module

#### 1. `main.py` — Vòng lặp chính

Khi chạy, bot thực hiện:

1. **Giai đoạn khởi tạo** (`run_bot`):
   - Load credentials từ `.env` hoặc `.env.live` (dựa trên mode đã lưu trong `.bot_state/mode.json`)
   - Khởi tạo lần lượt: `BinanceFuturesClient` → `StateManager` → `OrderExecutor` → `SmartScanner`
   - Mở **Telegram polling thread** (`bot_controller.start_polling()`) — lắng nghe lệnh trong nền
   - Mở **REST API server thread** (`api_server.start_api_thread()`) — HTTP endpoint `:8765`
   - Gửi Telegram thông báo khởi động kèm mode, vốn, đòn bẩy
   - Đăng ký signal handler để graceful shutdown (Ctrl+C)

2. **Giai đoạn chạy** (vòng lặp `while not killer.kill_now`):
   - **Đồng bộ vị thế**: Gọi `check_and_sync_positions()` để dò trên exchange xem vị thế nào đã bị SL/TP đóng. Nếu có, xoá khỏi local state và gửi Telegram.
   - **Cập nhật dashboard**: Lấy unrealized PnL, balance từ exchange, gán vào `bot_cfg` để Telegram command `/status`, `/position`, `/pnl` có dữ liệu.
   - **Kiểm tra restart**: Nếu người dùng gửi `/testnet` hoặc `/live` trên Telegram, bot set `restart_needed=True` → vòng lặp break để shutdown.
   - **Quét tín hiệu**: Chỉ nếu `can_open() == True` (chưa có vị thế), `trading_enabled == True` (không bị `/stop`), và `balance >= 50 USDT`.
   - **Vào lệnh**: Nếu có tín hiệu, gọi `executor.open_position()` với SL/TP từ scanner.
   - **Ngủ**: Đếm ngược 120 giây, kiểm tra `killer.kill_now` mỗi giây.

3. **Giai đoạn tắt**:
   - Log các vị thế còn mở (nếu có)
   - Gửi Telegram shutdown notification
   - Telegram polling thread tự dừng (daemon thread)

#### 2. `scanner.py` — SmartScanner (trái tim của bot)

**Đánh giá 5 bước cho mỗi coin:**

| Bước | Mô tả | Công thức |
|------|-------|-----------|
| **Bước 0** | Lấy dữ liệu | `get_klines(symbol, "1h", 100)` + `get_klines(symbol, "15m", 100)` |
| **Bước 1** | Xác định trend 1h | `price > EMA50 > EMA200` → uptrend. `price < EMA50 < EMA200` → downtrend. Còn lại → sideway (bỏ qua) |
| **Bước 2** | Kiểm tra pullback | Giá trong khoảng `EMA9 × 0.995` đến `EMA21 × 1.005` (LONG) hoặc ngược lại (SHORT) |
| **Bước 3** | Kiểm tra RSI | RSI(14) phải trong **40-60** (không quá mua/quá bán) |
| **Bước 4** | Tính ATR SL/TP | SL = entry ± ATR × 1.5 (đảm bảo dưới swing low), TP1 = ± ATR × 2, TP2 = ± ATR × 3 |
| **Bước 5** | Kiểm tra R:R | Risk:Reward ≥ 1:1.5 (tính trên TP1) hoặc ≥ 1:2.0 (tính trên TP2) |

**Công thức ATR (Average True Range):**

```python
TR = max(high - low, |high - prev_close|, |low - prev_close|)
ATR = EMA(TR, 14)  # Exponential moving average của True Range
```

**Công thức Dynamic SL (LONG):**
```python
sl_calc = price - ATR × 1.5                 # SL cơ bản
swing_low = min(low[-20:])                  # Đáy 20 nến gần nhất
sl_price = min(sl_calc, swing_low × 0.998)  # SL dưới swing low
sl_price = max(sl_price, price - ATR × 3.0) # Nhưng không quá 3 ATR
```

**Công thức Confidence Score (0-100):**

| Yếu tố | Trọng số | Cách tính |
|--------|----------|-----------|
| Entry type | 30 điểm | Crossover = 30, near_EMA = 15 |
| RSI position | 25 điểm | Gần 50 (cách ≤5) = 25, cách 5-10 = 20, cách 10-15 = 12, xa hơn = 5 |
| Trend strength | 20 điểm | `min(|EMA50-EMA200|/EMA200 × 1000, 100) × 0.2` |
| R:R ratio | 25 điểm | R:R ≥ 3.0 = 25, ≥ 2.5 = 22, ≥ 2.0 = 18, ≥ 1.5 = 12, còn lại = 5 |

#### 3. `executor.py` — Vào lệnh & quản lý rủi ro

**Công thức Position Sizing:**

```python
risk_amount = capital × risk_per_trade     # 100 × 1.5% = 1.5 USDT
price_risk_pct = |entry - sl| / entry      # VD: |100 - 96| / 100 = 4%
position_value = (risk_amount / price_risk_pct) × leverage  # (1.5 / 0.04) × 10 = 375 USDT
quantity = position_value / entry_price    # 375 / 100 = 3.75 contracts
```

**Quy trình vào lệnh:**
1. Set leverage 10x cho symbol
2. Set margin type ISOLATED
3. Tính quantity từ ATR-based SL
4. Place market order (BUY/SELL)
5. Place STOP_LOSS order (reduce-only) — tự động đóng khi chạm SL
6. Place TAKE_PROFIT order (reduce-only) — tự động chốt lời khi chạm TP1
7. Ghi nhận vị thế vào StateManager
8. Gửi Telegram thông báo kèm entry price, SL, TP

**Quy trình đóng lệnh (manual):**
1. Cancel tất cả open orders (SL + TP)
2. Place market order ngược chiều
3. Tính PnL = `(exit - entry) × quantity` (LONG) hoặc `(entry - exit) × quantity` (SHORT)
4. Xoá khỏi StateManager
5. Gửi Telegram kèm PnL + ROI%

#### 4. `bot_controller.py` — Lắng nghe lệnh Telegram

Chạy trong `threading.Thread` (daemon), sử dụng **long-polling** với Telegram Bot API:

```python
# Gọi API getUpdates với timeout 30s
GET https://api.telegram.org/bot<TOKEN>/getUpdates?offset=<last_id+1>&timeout=30

# Khi có update mới:
# 1. Kiểm tra chat_id có khớp với TELEGRAM_CHAT_ID không
# 2. Parse text command
# 3. Gọi _handle_command() để cập nhật BotConfig
# 4. Gửi reply qua sendMessage
```

**Cơ chế chia sẻ trạng thái:**

```
Telegram lệnh (/start, /stop, /scan...) 
    → _handle_command() 
    → cập nhật bot_cfg (BotConfig dataclass)

Main loop (mỗi 120s)
    → đọc bot_cfg.trading_enabled, bot_cfg.top_n, bot_cfg.restart_needed
    → cập nhật bot_cfg.positions, bot_cfg.balance_usdt, bot_cfg.total_pnl
```

Các field của `BotConfig`:

| Field | Kiểu | Ghi bởi | Đọc bởi | Mô tả |
|-------|------|---------|---------|-------|
| `trading_enabled` | bool | Telegram `/start`/`/stop` | Main loop | Cho phép vào lệnh? |
| `top_n` | int | Telegram `/scan N` | Main loop → Scanner | Số coin quét |
| `mode` | str | `load_saved_mode()` + `/testnet`/`/live` | Main loop | TESTNET / LIVE |
| `restart_needed` | bool | `/testnet`/`/live` | Main loop | Cần restart? |
| `positions` | list | Main loop | Telegram `/position`/`/pnl` | Vị thế + PnL |
| `total_pnl` | float | Main loop | Telegram `/status` | Tổng PnL |
| `balance_usdt` | float | Main loop | Telegram `/status` | Số dư |

#### 5. Graceful Shutdown

Khi nhận `SIGINT` (Ctrl+C) hoặc `SIGTERM`:
- Bot **không tắt ngay** — đánh dấu `kill_now = True`
- Vòng lặp hiện tại chạy xong rồi mới thoát
- Gửi Telegram: "🛑 BOT DỪNG" + danh sách vị thế còn mở (nếu có)
- **Không tự động đóng vị thế** — để tránh thua lỗ ngoài ý muốn
- Vị thế vẫn tồn tại trên Binance, có thể quản lý thủ công

### Xử lý lỗi

| Tình huống | Xử lý |
|------------|-------|
| Mất kết nối Binance API | Log warning, vòng lặp tiếp theo sẽ thử lại |
| Telegram API timeout | Bỏ qua, poll tiếp ở chu kỳ sau |
| Lỗi khi quét 1 coin | Skip coin đó, tiếp tục coin khác |
| Vào lệnh thất bại | Log error + Telegram cảnh báo |
| DeepSeek API timeout | Fallback: pass signal (không reject) |
| Balance < 50 USDT | Skip quét, chờ chu kỳ sau |

---

## 🏗️ Kiến Trúc

```
trade-bot-test/
├── main.py                   # Entry point + main loop
├── api_client.py             # Binance Futures REST client
├── scanner.py                # SmartScanner — multi-TF + ATR + confidence
├── executor.py               # Order placement + position sizing
├── state_manager.py          # Bot state + position tracking
├── bot_controller.py         # Telegram command listener (/start, /stop, /status...)
├── telegram_notifier.py      # Telegram alert sender (signal, entry, exit, error)
├── api_server.py             # REST API server (HTTP :8765)
├── deepseek_integration.py   # Optional DeepSeek AI signal filter
├── hermes_skill.py           # Hermes Agent integration
│
├── .env                      # Testnet credentials (template: env_template)
├── .env.live                 # Live credentials (mẫu: .env.live.template)
├── .env.live.template        # Mẫu key live
├── env_template              # Mẫu key testnet
│
├── Dockerfile                # Docker image
├── docker-compose.yml        # Docker Compose
├── Makefile                  # Convenience commands
├── requirements.txt          # Python dependencies
└── .gitignore                # Ignore .env, logs, __pycache__
```

### Module Flow:

```
main.py (main loop every 120s)
  ├── SmartScanner.scan()
  │     ├── BinanceFuturesClient.get_top_volume_symbols(30)
  │     ├── BinanceFuturesClient.get_klines(symbol, "15m")
  │     ├── BinanceFuturesClient.get_klines(symbol, "1h")
  │     ├── Tính EMA/RSI/ATR bằng pandas-native
  │     ├── Đánh giá 5 bước → signal + confidence score
  │     └── Chọn tín hiệu tốt nhất
  │
  ├── [Optional] DeepSeek AI filter
  │
  └── OrderExecutor.open_position()
        ├── set_leverage(10x) + isolated margin
        ├── calculate_position_size(risk=1.5%)
        ├── place_market_order(BUY/SELL)
        ├── place_stop_loss (reduce-only)
        ├── place_take_profit (reduce-only)
        └── StateManager.add_position()

bot_controller.py (Telegram polling thread)
  └── Lắng nghe lệnh → cập nhật BotConfig

api_server.py (HTTP thread on :8765)
  └── REST endpoints cho dashboard
```

---

## 🚀 Cài Đặt & Chạy

### 1. Clone & Setup

```bash
git clone https://github.com/tiennguyen3000/binance-futures-bot.git
cd binance-futures-bot
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 2. Config API Keys

```bash
# Tạo key testnet tại: https://testnet.binancefuture.com/
cp env_template .env
# Sửa .env với key testnet của bạn
```

### 3. Telegram (tùy chọn nhưng khuyến nghị)

```bash
# Tạo bot tại @BotFather → nhận token
# Lấy chat_id từ @userinfobot
# Thêm vào .env:
# TELEGRAM_BOT_TOKEN=...
# TELEGRAM_CHAT_ID=6844103069
```

### 4. Chạy thử

```bash
# Quick test — quét 1 lần rồi thoát
python main.py --test

# Chạy bot (testnet, tự động)
python main.py

# Với DEBUG log
python main.py --verbose

# Bật DeepSeek AI filter
python main.py --deepseek

# Live mode
python main.py --live
```

---

## 📱 Điều Khiển Qua Telegram

Gửi lệnh tới bot Telegram **@tiennk_future_auto_trading_bot** (hoặc bot của bạn):

| Lệnh | Chức năng | Ví dụ |
|------|-----------|-------|
| `/start` | Bật giao dịch — bot được phép vào lệnh khi có tín hiệu | `/start` |
| `/stop` | Tắt giao dịch — bot quét NHƯNG KHÔNG vào lệnh | `/stop` |
| `/scan` | Xem hoặc đặt số coin quét (5-100) | `/scan 50` |
| `/status` | Dashboard tổng quan: mode, balance, PnL, vị thế | `/status` |
| `/position` | Xem chi tiết vị thế đang mở (entry, SL, TP, PnL) | `/position` |
| `/pnl` | Tổng kết lãi lỗ tất cả vị thế | `/pnl` |
| `/testnet` | Chuyển sang testnet (cần restart bot để áp dụng) | `/testnet` |
| `/live` | Chuyển sang live (cần restart bot để áp dụng) | `/live` |
| `/help` | Danh sách tất cả lệnh | `/help` |

### Chi tiết từng lệnh

#### `/start` — Bật giao dịch

Cho phép bot vào lệnh khi phát hiện tín hiệu. Mặc định là **BẬT** khi khởi động.

```
Người dùng: /start
Bot: ✅ Bot đang BẬT giao dịch rồi.

Hoặc (nếu đang tắt):
Bot: ✅ Đã BẬT giao dịch. Bot sẽ vào lệnh khi có tín hiệu.
```

Cơ chế: Bot set `bot_cfg.trading_enabled = True`. Main loop kiểm tra flag này trước khi gọi `executor.open_position()`.

#### `/stop` — Tắt giao dịch

Bot vẫn quét thị trường và gửi tín hiệu Telegram, nhưng **KHÔNG vào lệnh**.

```
Người dùng: /stop
Bot: ⏸ Đã TẮT giao dịch. Bot sẽ quét nhưng KHÔNG vào lệnh.
```

Dùng khi: thị trạng biến động mạnh, muốn theo dõi tín hiệu nhưng không risk.

#### `/scan <N>` — Điều chỉnh số coin quét

Mặc định quét **top 30** volume. Có thể tăng/giảm:

```
Người dùng: /scan 50
Bot: 🔍 Đã đặt số coin quét = 50.

Người dùng: /scan
Bot: 🔍 Số coin hiện tại: 30. Dùng: /scan <số> (5-100)
```

Giới hạn: **5-100**. Nếu nhập ngoài khoảng, bot tự động clamp.

Cơ chế: Main loop mỗi chu kỳ kiểm tra `scanner.top_n != bot_cfg.top_n`. Nếu khác, cập nhật scanner.

#### `/status` — Dashboard tổng quan

Hiển thị trạng thái real-time của bot:

```
📊 BOT DASHBOARD
🧪 Mode: TESTNET
🟢 Giao dịch: BẬT
🔍 Quét: top 30 coins
📦 Vị thế: 1/1
💰 Ví: 10,234.56 USDT
📈 PnL: +45.20 USDT
💵 Vốn: 100 USDT | Đòn bẩy: 10x
📈 SL: ATR×1.5 | TP1: ATR×2 | TP2: ATR×3
🤖 Bot: @tiennk_future_auto_trading_bot
```

Nếu đang chờ restart (sau khi đổi mode):
```
⚠️ Cần restart để đổi mode!
```

#### `/position` — Vị thế đang mở

Chi tiết vị thế hiện tại, entry price, SL, TP, PnL:

```
📦 VỊ THẾ ĐANG MỞ
🟢 BTCUSDT LONG
  • Vào: 67,234.50
  • SL: 66,500.00 | TP: 69,800.00
  • PnL: +125.30 USDT (+1.86%)

Hoặc (không có vị thế):
📭 Không có vị thế nào — bot đang quét tìm tín hiệu.
```

Dữ liệu được main loop cập nhật mỗi 120s từ exchange (unrealized PnL).

#### `/pnl` — Tổng kết lãi lỗ

Tổng hợp PnL + ROI% của tất cả vị thế và tổng:

```
📊 TỔNG KẾT P&L
📈 BTCUSDT: +125.30 USDT (+1.86%)
📉 ETHUSDT: -22.50 USDT (-0.34%)

💰 Ví: 10,234.56 USDT
📈 Tổng PnL: +102.80 USDT
```

#### `/testnet` — Chuyển sang testnet

Khi bot đang ở LIVE, chuyển về TESTNET:

```
🔄 Chuyển sang TESTNET. Vui lòng RESTART bot để áp dụng.
```

Cơ chế:
1. Set `config.mode = "TESTNET"`
2. Lưu `{"mode": "TESTNET"}` vào `.bot_state/mode.json`
3. Set `restart_needed = True` → main loop break
4. Lần chạy sau sẽ load `.env` (testnet keys)

#### `/live` — Chuyển sang live

Kiểm tra `.env.live` có tồn tại không trước khi chuyển:

```
Người dùng: /live
Bot (nếu thiếu .env.live):
⚠️ Chưa có key Live!
Tạo file .env.live trong thư mục bot với nội dung:
  BINANCE_API_KEY=your_live_key
  BINANCE_API_SECRET=your_live_secret
  BINANCE_TESTNET=false
Coi mẫu tại: .env.live.template

Bot (thành công):
⚠️ Chuyển sang LIVE. Vui lòng RESTART bot để áp dụng.
```

Lưu ý: Sau khi restart, bot load `.env.live` thay vì `.env`. API key live **không bao giờ** được log ra console.

#### `/help` — Danh sách lệnh

Hiển thị tất cả lệnh có sẵn:

```
🤖 HƯỚNG DẪN ĐIỀU KHIỂN BOT

/start — Bật giao dịch
/stop — Tắt giao dịch
/testnet — Chuyển testnet (cần restart)
/live — Chuyển live (cần restart)
/scan 50 — Đặt số coin quét
/position — Xem vị thế đang mở
/pnl — Tổng kết lãi lỗ
/status — Dashboard tổng quan
/help — Danh sách lệnh
```

### Cơ chế hoạt động

Bot sử dụng **long-polling** với Telegram Bot API (không webhook):

1. Một `threading.Thread` (daemon) chạy nền, gọi `getUpdates` với timeout 30s
2. Mỗi lần có update mới, kiểm tra `chat_id` có khớp với `TELEGRAM_CHAT_ID` không
3. Parse text command → gọi `_handle_command()` → cập nhật `BotConfig`
4. Gửi reply HTML qua `sendMessage`
5. **Bảo mật**: Chỉ chấp nhận lệnh từ `TELEGRAM_CHAT_ID` đã cấu hình. Các chat khác bị im lặng bỏ qua.

### Telegram Alert Real-time

Bot tự động gửi thông báo qua Telegram khi có sự kiện quan trọng:

| Sự kiện | Emoji | Nội dung |
|---------|-------|----------|
| 🟢 Tín hiệu LONG | 🟢 | `TÍN HIỆU LONG` — Pair + giá + RSI + confidence |
| 🔴 Tín hiệu SHORT | 🔴 | `TÍN HIỆU SHORT` — Pair + giá + RSI + confidence |
| 🟢 Đã vào lệnh LONG | 🟢 | `ĐÃ VÀO LỆNH LONG` — Entry + SL + TP1 + TP2 + số dư |
| 🔴 Đã vào lệnh SHORT | 🔴 | `ĐÃ VÀO LỆNH SHORT` — Entry + SL + TP1 + TP2 + số dư |
| ✅ Đã đóng lệnh lời | ✅ | `ĐÃ ĐÓNG LỆNH (TP/manual)` — Entry→Exit + PnL + ROI% |
| ❌ Đã đóng lệnh lỗ | ❌ | `ĐÃ ĐÓNG LỆNH (SL/manual)` — Entry→Exit + PnL + ROI% |
| ⚠️ Lỗi | ⚠️ | Context + chi tiết lỗi (truncated 200 ký tự) |
| 🤖 Khởi động | 🤖 | Mode (TESTNET/LIVE) + vốn + đòn bẩy + lời nhắn `/help` |
| 🛑 Tắt máy | 🛑 | Danh sách vị thế còn mở (nếu có), khuyên dùng restart |

Ví dụ alert khi có tín hiệu + vào lệnh:

```
🟢 TÍN HIỆU LONG (🎯 Độ tin tưởng: 78%)
• Cặp: BTCUSDT
• Giá: 67234.50 USDT
• RSI(14): 48.2

🟢 ĐÃ VÀO LỆNH LONG
• Cặp: BTCUSDT
• Vào: 67234.50
• Khối lượng: 0.0184
• 🛑 SL: 66500.00
• ✅ TP1: 69800.00
• 🎯 TP2: 71000.00
• 💰 Ví: 9987.50 USDT
```

---

## 🌐 REST API

Bot tự động mở HTTP server tại `http://0.0.0.0:8765`.

| Method | Endpoint | Mô tả |
|--------|----------|-------|
| GET | `/status` | Dashboard tổng quan |
| GET | `/positions` | Danh sách vị thế + unrealized PnL |
| GET | `/balance` | Số dư USDT |
| GET | `/config` | Cấu hình hiện tại |
| POST | `/start` | Bật giao dịch |
| POST | `/stop` | Tắt giao dịch |
| POST | `/scan` | Set số coin quét (`{"top_n": 50}`) |

```bash
curl http://127.0.0.1:8765/status
curl -X POST http://127.0.0.1:8765/start
```

---

## 🐳 Docker

```bash
# Build image
make build

# Chạy testnet
make run-testnet

# Chạy live
make run-live

# Xem log
make logs

# Test nhanh trong container
make test

# Dừng
make stop
```

Hoặc trực tiếp:

```bash
docker compose up -d
docker compose logs -f
```

Container tự động restart trừ khi bị dừng thủ công (`restart: unless-stopped`). Secrets (`.env`, `.env.live`) được mount từ host, không build vào image.

---

## 🔧 Cấu Hình Chi Tiết

Các tham số có thể tùy chỉnh trong `main.py`:

| Biến | Mặc định | Mô tả |
|------|----------|-------|
| `SCAN_INTERVAL_SECONDS` | 120 | Tần suất quét (giây) |
| `MAX_POSITIONS` | 1 | Tối đa vị thế đồng thời |
| `CAPITAL_USDT` | 100.0 | Vốn khởi tạo |
| `RISK_PER_TRADE` | 0.015 | Rủi ro mỗi lệnh (1.5%) |
| `LEVERAGE` | 10 | Đòn bẩy |
| `TOP_N_SYMBOLS` | 30 | Số coin quét |
| `INTERVAL` | "15m" | Khung thời gian vào lệnh |

---

## 🧪 Optional: DeepSeek AI Filter

Khi bật `--deepseek`, mỗi tín hiệu được gửi qua DeepSeek AI để kiểm tra trước khi vào lệnh:

```bash
python main.py --deepseek
```

Yêu cầu `DEEPSEEK_API_KEY` trong `.env`. DeepSeek đánh giá dựa trên:
1. RSI có ở vùng quá mua/quá bán không?
2. Khoảng cách EMA9-EMA21 có đủ lớn?
3. Có dấu hiệu nhiễu (chop) không?

~150 tokens/tín hiệu, chi phí ~$0.003/1000 tín hiệu (DeepSeek pricing).

---

## ⚙️ Yêu Cầu Hệ Thống

- Python 3.12+ (tested trên 3.12–3.14)
- Binance Futures API key (testnet hoặc live)
- Pip packages: `python-binance`, `pandas`, `python-dotenv`, `requests`, `openai` (optional)

> **Lưu ý Python 3.14+**: Bot dùng pandas-native (không pandas-ta, không numba) nên tương thích hoàn toàn với Python 3.14+.

---

## 🔒 Rủi Ro & Bảo Mật

- ⚠️ **Luôn chạy testnet trước** — mặc định `BINANCE_TESTNET=true`
- 🔐 API key live lưu riêng trong `.env.live` — không commit lên Git
- 📉 **Rủi ro tối đa 1.5%/lệnh** — không all-in
- 🛑 **Tối đa 1 vị thế** — tập trung vốn, giảm rủi ro
- 📊 **ATR-based dynamic SL** — tránh bị quét thanh lý
- 🔄 **Testnet/Live switch qua Telegram** — không cần SSH vào server

---

## 📂 File Cấu Hình Mẫu

### `.env` (testnet)

```bash
BINANCE_API_KEY=your_testnet_key
BINANCE_API_SECRET=your_testnet_secret
BINANCE_TESTNET=true
TELEGRAM_BOT_TOKEN=your_bot_token
TELEGRAM_CHAT_ID=your_chat_id
```

### `.env.live` (live trading)

```bash
BINANCE_API_KEY=your_live_key
BINANCE_API_SECRET=your_live_secret
BINANCE_TESTNET=false
```

---

## 📝 License

MIT License — sử dụng cho mục đích cá nhân và giáo dục. Giao dịch tiền thật có rủi ro, tác giả không chịu trách nhiệm về tổn thất tài chính.
