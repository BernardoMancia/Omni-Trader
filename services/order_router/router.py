import os
import hmac
import hashlib
import time
import logging
import asyncio
import httpx
import psycopg2
from services.shared.risk import RiskManager, MarketState

logger = logging.getLogger("SmartOrderRouter")

class SmartOrderRouter:
    BINANCE_BASE = "https://api.binance.com"
    ALPACA_BASE_DEFAULT = "https://paper-api.alpaca.markets"

    def __init__(self, risk_manager: RiskManager, exchange: str):
        self.risk_manager = risk_manager
        self.exchange = exchange
        self.api_key = os.environ["BINANCE_API_KEY"] if exchange == "binance" else os.environ["ALPACA_API_KEY"]
        self.api_secret = os.environ["BINANCE_SECRET_KEY"] if exchange == "binance" else os.environ["ALPACA_SECRET_KEY"]
        self.base_url = self.BINANCE_BASE if exchange == "binance" else os.environ.get("ALPACA_BASE_URL", self.ALPACA_BASE_DEFAULT)
        self.db_conn = psycopg2.connect(
            host=os.environ["DB_HOST"], port=os.environ["DB_PORT"],
            dbname=os.environ["DB_NAME"], user=os.environ["DB_USER"],
            password=os.environ["DB_PASSWORD"],
        )

    async def execute_order(self, symbol: str, quantity: float, side: str) -> dict:
        if self.risk_manager.state == MarketState.RED:
            return await self._shadow_trade(symbol, quantity, side)
        if self.exchange == "binance":
            return await self._binance_order(symbol, quantity, side)
        return await self._alpaca_order(symbol, quantity, side)

    async def _binance_order(self, symbol: str, quantity: float, side: str) -> dict:
        ts = int(time.time() * 1000)
        params = f"symbol={symbol}&side={side}&type=MARKET&quantity={quantity}&timestamp={ts}"
        sig = hmac.new(self.api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
        url = f"{self.base_url}/api/v3/order?{params}&signature={sig}"
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers={"X-MBX-APIKEY": self.api_key})
            r.raise_for_status()
            return r.json()

    async def _alpaca_order(self, symbol: str, quantity: float, side: str) -> dict:
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
            "Content-Type": "application/json",
        }
        payload = {"symbol": symbol, "qty": str(quantity), "side": side.lower(), "type": "market", "time_in_force": "gtc"}
        async with httpx.AsyncClient() as client:
            r = await client.post(f"{self.base_url}/v2/orders", json=payload, headers=headers)
            r.raise_for_status()
            return r.json()

    async def _shadow_trade(self, symbol: str, quantity: float, side: str) -> dict:
        cursor = self.db_conn.cursor()
        cursor.execute(
            "INSERT INTO trade_logs (symbol, side, quantity, mode, region) VALUES (%s, %s, %s, %s, %s)",
            (symbol, side, quantity, "SHADOW", "ASIA" if self.exchange == "binance" else "US"),
        )
        self.db_conn.commit()
        logger.info(f"SHADOW: {side} {quantity} {symbol}")
        return {"mode": "SHADOW", "symbol": symbol, "side": side, "quantity": quantity}

async def main():
    from services.shared.risk import RiskManager
    risk_us = RiskManager(initial_capital=float(os.environ.get("INITIAL_CAPITAL_US", "10000")), region="US")
    risk_asia = RiskManager(initial_capital=float(os.environ.get("INITIAL_CAPITAL_ASIA", "10000")), region="ASIA")
    router_binance = SmartOrderRouter(risk_manager=risk_asia, exchange="binance")
    router_alpaca = SmartOrderRouter(risk_manager=risk_us, exchange="alpaca")
    logger.info("SOR initialized for BINANCE and ALPACA")
    await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
