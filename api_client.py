"""
Binance Futures REST API client.
Supports testnet and live trading.
"""
import os
import time
import hashlib
import hmac
import requests
import logging

logger = logging.getLogger(__name__)

class BinanceFuturesClient:
    BASE_URL_TESTNET = "https://testnet.binancefuture.com"
    BASE_URL_LIVE = "https://fapi.binance.com"

    def __init__(self, testnet: bool = True, api_key: str = None, api_secret: str = None):
        self.testnet = testnet
        self.base_url = self.BASE_URL_TESTNET if testnet else self.BASE_URL_LIVE
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")

        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set in .env")

        self.session = requests.Session()
        self.session.headers.update({
            "X-MBX-APIKEY": self.api_key,
            "Content-Type": "application/json",
        })

        mode = "TESTNET" if testnet else "LIVE"
        logger.info(f"Binance Futures client initialized ({mode})")

    def _sign(self, params: dict) -> dict:
        """Sign parameters with HMAC-SHA256.
        QUAN TRỌNG: Không sort params — requests gửi theo insertion order,
        chữ ký phải tính trên ĐÚNG query string mà requests gửi đi."""
        query_string = "&".join([f"{k}={v}" for k, v in params.items()])
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            query_string.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        params["signature"] = signature
        return params

    def _request(self, method: str, path: str, signed: bool = False, params: dict = None) -> dict:
        """Send signed or unsigned request to Binance Futures API."""
        url = f"{self.base_url}{path}"
        params = params or {}

        if signed:
            params["timestamp"] = int(time.time() * 1000)
            params = self._sign(params)

        try:
            resp = self.session.request(method, url, params=params, timeout=10)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.RequestException as e:
            logger.error(f"API request failed: {method} {path} — {e}")
            if resp is not None and resp.text:
                logger.error(f"Response: {resp.text}")
            return {}

    # ---- Public endpoints ----

    def get_exchange_info(self) -> dict:
        """Get exchange trading rules and symbol list."""
        return self._request("GET", "/fapi/v1/exchangeInfo")

    def get_top_volume_symbols(self, limit: int = 10) -> list[dict]:
        """
        Get top N USDT-margined futures symbols by 24h quote volume.
        Returns list of dicts with symbol, volume, price, etc.
        """
        tickers = self._request("GET", "/fapi/v1/ticker/24hr")
        if not tickers:
            return []

        # Filter USDT pairs only
        usdt_pairs = [t for t in tickers if t.get("symbol", "").endswith("USDT")]

        # Filter out non-trading pairs
        active_usdt = [t for t in usdt_pairs if t.get("symbol")]

        # Sort by quote volume descending
        sorted_pairs = sorted(
            active_usdt,
            key=lambda x: float(x.get("quoteVolume", 0)),
            reverse=True,
        )

        return sorted_pairs[:limit]

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 100) -> list[list]:
        """Get kline/candlestick data.
        
        Returns list of klines, each as [open_time, open, high, low, close, volume, ...]
        """
        params = {"symbol": symbol, "interval": interval, "limit": limit}
        return self._request("GET", "/fapi/v1/klines", params=params)

    def get_symbol_price(self, symbol: str) -> float:
        """Get current mark price for a symbol."""
        params = {"symbol": symbol}
        data = self._request("GET", "/fapi/v1/ticker/price", params=params)
        return float(data.get("price", 0))

    def get_mark_price(self, symbol: str) -> dict:
        """Get current mark price with funding info."""
        params = {"symbol": symbol}
        return self._request("GET", "/fapi/v1/premiumIndex", params=params)

    # ---- Signed endpoints ----

    def get_account_info(self) -> dict:
        """Get futures account info (balance, positions)."""
        return self._request("GET", "/fapi/v2/account", signed=True)

    def get_balance(self, asset: str = "USDT") -> float:
        """Get wallet balance for a specific asset."""
        account = self.get_account_info()
        if not account:
            return 0.0
        for a in account.get("assets", []):
            if a.get("asset") == asset:
                return float(a.get("walletBalance", 0))
        return 0.0

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        """Set leverage for a symbol."""
        params = {"symbol": symbol, "leverage": leverage}
        return self._request("POST", "/fapi/v1/leverage", signed=True, params=params)

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        """Set margin type: ISOLATED or CROSSED."""
        params = {"symbol": symbol, "marginType": margin_type}
        return self._request("POST", "/fapi/v1/marginType", signed=True, params=params)

    def place_market_order(self, symbol: str, side: str, quantity: float) -> dict:
        """Place a market order.
        
        Args:
            symbol: Trading pair (e.g. 'BTCUSDT')
            side: 'BUY' or 'SELL'
            quantity: Contract quantity
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "MARKET",
            "quantity": self._normalize_qty(symbol, quantity),
            "newOrderRespType": "RESULT",
        }
        return self._request("POST", "/fapi/v1/order", signed=True, params=params)

    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float, price: float = None) -> dict:
        """Place a stop-loss order.
        
        For LONG: side=SELL, stop_price below entry
        For SHORT: side=BUY, stop_price above entry
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "STOP_MARKET",
            "quantity": self._normalize_qty(symbol, quantity),
            "stopPrice": self._normalize_price(symbol, stop_price),
            "reduceOnly": True,
            "newOrderRespType": "RESULT",
        }
        if price:
            params["price"] = self._normalize_price(symbol, price)
        return self._request("POST", "/fapi/v1/order", signed=True, params=params)

    def place_take_profit(self, symbol: str, side: str, quantity: float, price: float) -> dict:
        """Place a take-profit limit order.
        
        For LONG: side=SELL, price above entry
        For SHORT: side=BUY, price below entry
        """
        params = {
            "symbol": symbol,
            "side": side,
            "type": "TAKE_PROFIT_MARKET",
            "quantity": self._normalize_qty(symbol, quantity),
            "stopPrice": self._normalize_price(symbol, price),
            "reduceOnly": True,
            "newOrderRespType": "RESULT",
        }
        return self._request("POST", "/fapi/v1/order", signed=True, params=params)

    def cancel_all_orders(self, symbol: str) -> dict:
        """Cancel all open orders for a symbol."""
        params = {"symbol": symbol}
        return self._request("DELETE", "/fapi/v1/allOpenOrders", signed=True, params=params)

    def get_open_orders(self, symbol: str = None) -> list[dict]:
        """Get all open orders (optionally filtered by symbol)."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._request("GET", "/fapi/v1/openOrders", signed=True, params=params)
        return data if isinstance(data, list) else []

    def get_position_risk(self, symbol: str = None) -> list[dict]:
        """Get current position risk info."""
        params = {}
        if symbol:
            params["symbol"] = symbol
        data = self._request("GET", "/fapi/v2/positionRisk", signed=True, params=params)
        return data if isinstance(data, list) else []

    def get_position_amt(self, symbol: str) -> float:
        """Get current position amount for a symbol (positive=LONG, negative=SHORT)."""
        positions = self.get_position_risk(symbol)
        for p in positions:
            if p.get("symbol") == symbol:
                return float(p.get("positionAmt", 0))
        return 0.0

    # ---- Helpers ----

    def get_symbol_info(self, symbol: str) -> dict:
        """Get trading rules for a specific symbol."""
        info = self.get_exchange_info()
        for s in info.get("symbols", []):
            if s.get("symbol") == symbol:
                return s
        return {}

    def _get_step_size(self, symbol: str) -> float:
        """Get quantity step size (lot size filter) for a symbol."""
        sym_info = self.get_symbol_info(symbol)
        for f in sym_info.get("filters", []):
            if f.get("filterType") == "LOT_SIZE":
                return float(f.get("stepSize", "0.001"))
        return 0.001

    def _get_tick_size(self, symbol: str) -> float:
        """Get price tick size for a symbol."""
        sym_info = self.get_symbol_info(symbol)
        for f in sym_info.get("filters", []):
            if f.get("filterType") == "PRICE_FILTER":
                return float(f.get("tickSize", "0.01"))
        return 0.01

    def _normalize_qty(self, symbol: str, qty: float) -> float:
        """Round quantity to the symbol's step size precision."""
        step = self._get_step_size(symbol)
        precision = len(str(step).split(".")[1]) if "." in str(step) else 0
        return round(qty - (qty % step), precision)

    def _normalize_price(self, symbol: str, price: float) -> float:
        """Round price to the symbol's tick size precision."""
        tick = self._get_tick_size(symbol)
        precision = len(str(tick).split(".")[1]) if "." in str(tick) else 0
        return round(price - (price % tick), precision)
