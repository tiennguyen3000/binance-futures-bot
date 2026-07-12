"""Binance USD-M Futures REST client with explicit, idempotent order contracts."""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
import uuid
from decimal import Decimal, ROUND_DOWN
from typing import Any
from urllib.parse import urlencode

import requests

logger = logging.getLogger(__name__)


class BinanceFuturesClient:
    BASE_URL_TESTNET = "https://testnet.binancefuture.com"
    BASE_URL_LIVE = "https://fapi.binance.com"

    def __init__(self, testnet: bool = True, api_key: str | None = None, api_secret: str | None = None):
        self.testnet = testnet
        self.base_url = self.BASE_URL_TESTNET if testnet else self.BASE_URL_LIVE
        self.api_key = api_key or os.getenv("BINANCE_API_KEY", "")
        self.api_secret = api_secret or os.getenv("BINANCE_API_SECRET", "")
        if not self.api_key or not self.api_secret:
            raise ValueError("BINANCE_API_KEY and BINANCE_API_SECRET must be set")
        self.session = requests.Session()
        self.session.headers.update({"X-MBX-APIKEY": self.api_key})
        self._time_offset_ms = 0
        self._exchange_info: dict[str, Any] | None = None

    def sync_time(self) -> None:
        started = time.time_ns() // 1_000_000
        data = self._request("GET", "/fapi/v1/time")
        ended = time.time_ns() // 1_000_000
        if data.get("serverTime"):
            self._time_offset_ms = int(data["serverTime"]) - ((started + ended) // 2)

    def _timestamp(self) -> int:
        return int(time.time() * 1000) + self._time_offset_ms

    def _sign(self, params: dict[str, Any]) -> dict[str, Any]:
        query = urlencode(params, doseq=True)
        params["signature"] = hmac.new(self.api_secret.encode(), query.encode(), hashlib.sha256).hexdigest()
        return params

    def _request(self, method: str, path: str, signed: bool = False, params: dict[str, Any] | None = None) -> dict | list:
        request_params = dict(params or {})
        if signed:
            request_params.setdefault("recvWindow", 5000)
            request_params["timestamp"] = self._timestamp()
            request_params = self._sign(request_params)
        response = None
        try:
            response = self.session.request(method, f"{self.base_url}{path}", params=request_params, timeout=10)
            response.raise_for_status()
            return response.json()
        except requests.RequestException as exc:
            payload: dict[str, Any] = {"_error": str(exc), "_retryable": response is None or (response.status_code >= 500)}
            if response is not None:
                try:
                    body = response.json()
                    if isinstance(body, dict):
                        payload.update({"_http_status": response.status_code, "_binance_code": body.get("code"), "_binance_msg": body.get("msg")})
                except ValueError:
                    payload["_http_status"] = response.status_code
            logger.error("Binance %s %s failed: %s", method, path, payload)
            return payload

    def get_exchange_info(self) -> dict:
        if self._exchange_info is None:
            data = self._request("GET", "/fapi/v1/exchangeInfo")
            self._exchange_info = data if isinstance(data, dict) else {}
        return self._exchange_info

    def get_symbol_info(self, symbol: str) -> dict:
        return next((item for item in self.get_exchange_info().get("symbols", []) if item.get("symbol") == symbol), {})

    def is_tradable(self, symbol: str) -> bool:
        info = self.get_symbol_info(symbol)
        return info.get("status") == "TRADING" and info.get("contractType") == "PERPETUAL" and info.get("quoteAsset") == "USDT"

    def get_top_volume_symbols(self, limit: int = 10) -> list[dict]:
        tickers = self._request("GET", "/fapi/v1/ticker/24hr")
        if not isinstance(tickers, list):
            return []
        valid = [ticker for ticker in tickers if self.is_tradable(ticker.get("symbol", ""))]
        return sorted(valid, key=lambda item: float(item.get("quoteVolume", 0)), reverse=True)[:limit]

    def get_klines(self, symbol: str, interval: str = "15m", limit: int = 100) -> list[list]:
        data = self._request("GET", "/fapi/v1/klines", params={"symbol": symbol, "interval": interval, "limit": limit})
        return data if isinstance(data, list) else []

    def get_symbol_price(self, symbol: str) -> float:
        data = self._request("GET", "/fapi/v1/ticker/price", params={"symbol": symbol})
        return float(data.get("price", 0)) if isinstance(data, dict) else 0.0

    def get_account_info(self) -> dict:
        data = self._request("GET", "/fapi/v2/account", signed=True)
        return data if isinstance(data, dict) else {"_error": "Unexpected account response"}

    def get_balance(self, asset: str = "USDT") -> float:
        return next((float(item.get("availableBalance", item.get("walletBalance", 0))) for item in self.get_account_info().get("assets", []) if item.get("asset") == asset), 0.0)

    def get_position_mode(self) -> bool:
        """Return True only when the account has Hedge Mode enabled."""
        data = self._request("GET", "/fapi/v1/positionSide/dual", signed=True)
        if not isinstance(data, dict) or "dualSidePosition" not in data:
            raise RuntimeError(f"Cannot determine Binance position mode: {data}")
        value = data["dualSidePosition"]
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return bool(value)

    def assert_one_way_mode(self) -> None:
        if self.get_position_mode():
            raise RuntimeError("Hedge Mode is unsupported; switch the Binance account to One-way Mode")

    def set_leverage(self, symbol: str, leverage: int) -> dict:
        data = self._request("POST", "/fapi/v1/leverage", signed=True, params={"symbol": symbol, "leverage": leverage})
        return data if isinstance(data, dict) else {}

    def set_margin_type(self, symbol: str, margin_type: str = "ISOLATED") -> dict:
        data = self._request("POST", "/fapi/v1/marginType", signed=True, params={"symbol": symbol, "marginType": margin_type})
        return data if isinstance(data, dict) else {}

    @staticmethod
    def _client_id(purpose: str) -> str:
        return f"tb-{purpose}-{uuid.uuid4().hex[:20]}"

    def _order(self, params: dict[str, Any], purpose: str) -> dict:
        params.setdefault("newClientOrderId", self._client_id(purpose))
        # USD-M Futures supports STOP_MARKET/TAKE_PROFIT_MARKET on the normal
        # order endpoint. Keeping one order lifecycle avoids unverified algo
        # endpoint/schema assumptions and makes cancellation/reconciliation exact.
        data = self._request("POST", "/fapi/v1/order", signed=True, params=params)
        return data if isinstance(data, dict) else {"_error": "Unexpected non-object response"}

    def place_market_order(self, symbol: str, side: str, quantity: float, position_side: str | None = None, reduce_only: bool = False, client_order_id: str | None = None) -> dict:
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": "MARKET", "quantity": self._normalize_qty(symbol, quantity), "newOrderRespType": "RESULT"}
        if client_order_id:
            params["newClientOrderId"] = client_order_id
        if position_side:
            params["positionSide"] = position_side
        if reduce_only and not position_side:
            params["reduceOnly"] = "true"
        return self._order(params, "entry" if not reduce_only else "exit")

    def _conditional_order(self, symbol: str, side: str, quantity: float, trigger_price: float, position_side: str | None, order_type: str) -> dict:
        params: dict[str, Any] = {"symbol": symbol, "side": side, "type": order_type, "quantity": self._normalize_qty(symbol, quantity), "stopPrice": self._normalize_price(symbol, trigger_price), "workingType": "MARK_PRICE"}
        if position_side:
            params["positionSide"] = position_side
        else:
            params["reduceOnly"] = "true"
        return self._order(params, order_type.lower())

    def place_stop_loss(self, symbol: str, side: str, quantity: float, stop_price: float, position_side: str | None = None) -> dict:
        return self._conditional_order(symbol, side, quantity, stop_price, position_side, "STOP_MARKET")

    def place_take_profit(self, symbol: str, side: str, quantity: float, price: float, position_side: str | None = None) -> dict:
        return self._conditional_order(symbol, side, quantity, price, position_side, "TAKE_PROFIT_MARKET")

    def get_order_by_client_id(self, symbol: str, client_order_id: str) -> dict:
        """Resolve an unknown submit outcome without sending another market order."""
        data = self._request("GET", "/fapi/v1/order", signed=True, params={"symbol": symbol, "origClientOrderId": client_order_id})
        return data if isinstance(data, dict) else {}

    def cancel_order(self, symbol: str, order_id: str) -> dict:
        data = self._request("DELETE", "/fapi/v1/order", signed=True, params={"symbol": symbol, "orderId": order_id})
        return data if isinstance(data, dict) else {}

    def get_open_orders(self, symbol: str | None = None) -> list[dict] | dict:
        params = {"symbol": symbol} if symbol else {}
        data = self._request("GET", "/fapi/v1/openOrders", signed=True, params=params)
        return data if isinstance(data, list) else {"_error": "Open-order read failed", "detail": data}

    def get_open_algo_orders(self, symbol: str | None = None) -> list[dict] | dict:
        params = {"symbol": symbol} if symbol else {}
        data = self._request("GET", "/fapi/v1/openAlgoOrders", signed=True, params=params)
        return data if isinstance(data, list) else {"_error": "Algo-order read failed", "detail": data}

    def cancel_algo_order(self, symbol: str, algo_id: str | None = None, client_algo_id: str | None = None) -> dict:
        params: dict[str, Any] = {"symbol": symbol}
        if algo_id:
            params["algoId"] = algo_id
        elif client_algo_id:
            params["clientAlgoId"] = client_algo_id
        else:
            return {"_error": "algoId or clientAlgoId is required"}
        data = self._request("DELETE", "/fapi/v1/algo/order", signed=True, params=params)
        return data if isinstance(data, dict) else {"_error": "Unexpected cancel-algo response"}

    def get_position_risk(self, symbol: str | None = None) -> list[dict] | dict:
        params = {"symbol": symbol} if symbol else {}
        data = self._request("GET", "/fapi/v2/positionRisk", signed=True, params=params)
        return data if isinstance(data, list) else {"_error": "Position-risk read failed", "detail": data}

    def get_position_amt(self, symbol: str, position_side: str | None = None) -> float:
        risk = self.get_position_risk(symbol)
        if not isinstance(risk, list):
            raise RuntimeError(f"Cannot confirm exchange position: {risk}")
        matching = [item for item in risk if item.get("symbol") == symbol and (position_side is None or item.get("positionSide") == position_side)]
        if position_side is None and any(item.get("positionSide") in {"LONG", "SHORT"} for item in matching):
            raise RuntimeError("Hedge-mode positions are not supported; operator intervention required")
        return sum(float(item.get("positionAmt", 0)) for item in matching)

    def get_funding_rate(self, symbol: str) -> float:
        data = self._request("GET", "/fapi/v1/premiumIndex", params={"symbol": symbol})
        return float(data.get("lastFundingRate", 0)) if isinstance(data, dict) else 0.0

    def _filter(self, symbol: str, filter_type: str) -> dict:
        return next((item for item in self.get_symbol_info(symbol).get("filters", []) if item.get("filterType") == filter_type), {})

    def _normalize(self, value: float, step: str) -> float:
        decimal_step = Decimal(step)
        if decimal_step <= 0:
            return float(value)
        value_decimal = Decimal(str(value))
        normalized = (value_decimal // decimal_step) * decimal_step
        return float(normalized)

    def _normalize_qty(self, symbol: str, quantity: float) -> float:
        rule = self._filter(symbol, "MARKET_LOT_SIZE") or self._filter(symbol, "LOT_SIZE")
        normalized = self._normalize(quantity, rule.get("stepSize", "0.001"))
        minimum = float(rule.get("minQty", 0))
        return normalized if normalized >= minimum else 0.0

    def _normalize_price(self, symbol: str, price: float) -> float:
        return self._normalize(price, self._filter(symbol, "PRICE_FILTER").get("tickSize", "0.01"))
