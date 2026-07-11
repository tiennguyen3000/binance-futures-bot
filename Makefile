.PHONY: build run stop logs shell restart

build:
	docker build -t binance-futures-bot:latest .

run:
	docker compose up -d

stop:
	docker compose down

logs:
	docker compose logs -f

shell:
	docker compose exec bot python

restart: stop run

# Test nhanh trong container rồi xoá
test:
	docker run --rm -v ./.env:/app/.env:ro -v ./.bot_state:/app/.bot_state binance-futures-bot:latest python main.py --test

# Chạy live mode
run-live:
	@echo "Switching to LIVE mode..."
	@mkdir -p .bot_state
	@echo '{"mode": "LIVE"}' > .bot_state/mode.json
	docker compose up -d

# Chạy testnet mode
run-testnet:
	@echo "Switching to TESTNET mode..."
	@mkdir -p .bot_state
	@echo '{"mode": "TESTNET"}' > .bot_state/mode.json
	docker compose up -d
