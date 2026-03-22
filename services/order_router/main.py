import os
import asyncio
import logging
import hmac
import hashlib
import time
import httpx
import psycopg2
from ib_insync import IB, Stock, MarketOrder, util
from services.shared.risk import RiskManager, MarketState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("SmartOrderRouter")

IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "1"))
BINANCE_BASE = "https://api.binance.com"

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

class IBKRRouter:
    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        self.ib = IB()
        self.db = psycopg2.connect(**DB_PARAMS)

    async def connect(self):
        util.patchAsyncio()
        while True:
            try:
                await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=IB_CLIENT_ID)
                logger.info(f"IBKR connected to {IB_HOST}:{IB_PORT}")
                break
            except Exception as e:
                logger.error(f"IBKR connect failed: {e}. Retrying in 5s...")
                await asyncio.sleep(5)

    async def execute_order(self, symbol: str, quantity: float, side: str) -> dict:
        if self.risk.state == MarketState.RED:
            return await self._shadow_trade(symbol, quantity, side, "US")
        contract = Stock(symbol, "SMART", "USD")
        await self.ib.qualifyContractsAsync(contract)
        order = MarketOrder(side.upper(), quantity)
        trade = self.ib.placeOrder(contract, order)
        logger.info(f"IBKR Order: {side} {quantity} {symbol}")
        return {"status": "submitted", "orderId": trade.order.orderId}

    async def _shadow_trade(self, symbol: str, quantity: float, side: str, region: str) -> dict:
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT INTO trade_logs (symbol, side, quantity, mode, region) VALUES (%s, %s, %s, %s, %s)",
            (symbol, side, quantity, "SHADOW", region),
        )
        self.db.commit()
        logger.info(f"SHADOW: {side} {quantity} {symbol}")
        return {"mode": "SHADOW", "symbol": symbol}

class BinanceRouter:
    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        self.api_key = os.environ["BINANCE_API_KEY"]
        self.api_secret = os.environ["BINANCE_SECRET_KEY"]
        self.db = psycopg2.connect(**DB_PARAMS)

    async def execute_order(self, symbol: str, quantity: float, side: str) -> dict:
        if self.risk.state == MarketState.RED:
            return await self._shadow_trade(symbol, quantity, side, "ASIA")
        ts = int(time.time() * 1000)
        params = f"symbol={symbol}&side={side}&type=MARKET&quantity={quantity}&timestamp={ts}"
        sig = hmac.new(self.api_secret.encode(), params.encode(), hashlib.sha256).hexdigest()
        url = f"{BINANCE_BASE}/api/v3/order?{params}&signature={sig}"
        async with httpx.AsyncClient() as client:
            r = await client.post(url, headers={"X-MBX-APIKEY": self.api_key})
            r.raise_for_status()
            return r.json()

    async def _shadow_trade(self, symbol: str, quantity: float, side: str, region: str) -> dict:
        cursor = self.db.cursor()
        cursor.execute(
            "INSERT INTO trade_logs (symbol, side, quantity, mode, region) VALUES (%s, %s, %s, %s, %s)",
            (symbol, side, quantity, "SHADOW", region),
        )
        self.db.commit()
        logger.info(f"SHADOW BINANCE: {side} {quantity} {symbol}")
        return {"mode": "SHADOW", "symbol": symbol}

async def main():
    risk_us = RiskManager(initial_capital=float(os.environ.get("INITIAL_CAPITAL_US", "10000")), region="US")
    risk_asia = RiskManager(initial_capital=float(os.environ.get("INITIAL_CAPITAL_ASIA", "10000")), region="ASIA")
    ibkr_router = IBKRRouter(risk_manager=risk_us)
    await ibkr_router.connect()
    logger.info("SOR online: IBKR (US) + Binance (ASIA)")
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
