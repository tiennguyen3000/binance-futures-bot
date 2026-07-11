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

| Lệnh | Chức năng |
|------|-----------|
| `/start` | Bật giao dịch (bot vào lệnh khi có tín hiệu) |
| `/stop` | Tắt giao dịch (bot quét nhưng KHÔNG vào lệnh) |
| `/scan 50` | Đặt số coin quét (5-100) |
| `/status` | Dashboard tổng quan |
| `/position` | Xem vị thế đang mở |
| `/pnl` | Tổng kết lãi lỗ |
| `/testnet` | Chuyển chế độ testnet (cần restart) |
| `/live` | Chuyển chế độ live (cần restart) |
| `/help` | Danh sách lệnh |

### Telegram Alert Real-time:

| Sự kiện | Emoji | Mô tả |
|---------|-------|-------|
| Tín hiệu mới | 🟢🔴 | Pair + giá + RSI + confidence score |
| Đã vào lệnh | 🟢🔴 | Entry + SL + TP + số dư |
| Đã đóng lệnh | ✅❌ | Entry→Exit + PnL + ROI% + lý do (SL/TP/manual) |
| Lỗi | ⚠️ | Context + chi tiết lỗi |
| Khởi động | 🤖 | Mode + vốn + đòn bẩy |
| Tắt máy | 🛑 | Cảnh báo vị thế còn mở nếu có |

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
