FROM python:3.12-slim

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (excluding .env secrets via .dockerignore)
COPY . .

# Expose REST API
EXPOSE 8765

# Run bot — mặc định testnet, muốn live thì set BINANCE_TESTNET=false hoặc dùng /live
CMD ["python", "main.py", "--verbose"]
