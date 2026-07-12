FROM python:3.12-slim

WORKDIR /app

# Copy requirements first for layer caching
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy source code (excluding .env secrets via .dockerignore)
COPY . .

# API stays internal by default; publish only behind an authenticated proxy.
# EXPOSE intentionally omitted

# Run testnet-safe bot by default
CMD ["python", "main.py", "--verbose"]
