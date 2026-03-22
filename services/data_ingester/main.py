import os
import asyncio
import logging
import psycopg2
from ib_insync import IB, Stock, util

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DataIngester")

DB_PARAMS = {
    "host": os.environ["DB_HOST"],
    "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"],
    "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

INSERT_QUERY = "INSERT INTO market_data (symbol, bid, ask, region) VALUES (%s, %s, %s, %s)"

IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "4002"))
IB_CLIENT_ID = int(os.environ.get("IB_CLIENT_ID", "10"))

US_SYMBOLS = ["AAPL", "MSFT", "TSLA", "SPY", "QQQ"]
CRYPTO_SYMBOLS = ["btcusdt", "ethusdt", "bnbusdt"]

def get_db():
    while True:
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            logger.info("Database connected.")
            return conn
        except Exception as e:
            logger.error(f"DB connect failed: {e}. Retrying in 5s...")
            import time
            time.sleep(5)

async def run_binance_ingester():
    import json
    import websockets
    stream = "/".join([f"{s}@bookTicker" for s in CRYPTO_SYMBOLS])
    uri = f"wss://stream.binance.com:9443/ws/{stream}"
    conn = get_db()
    cursor = conn.cursor()
    
    while True:
        try:
            logger.info(f"Binance connecting: {CRYPTO_SYMBOLS}")
            async with websockets.connect(uri) as ws:
                logger.info("Binance connected and listening.")
                async for raw in ws:
                    data = json.loads(raw)
                    symbol = data.get("s")
                    bid = float(data.get("b", 0))
                    ask = float(data.get("a", 0))
                    if symbol and bid and ask:
                        cursor.execute(INSERT_QUERY, (symbol, bid, ask, "ASIA"))
                        conn.commit()
        except Exception as e:
            logger.warning(f"Binance WS dropped ({e}). Reconnecting in 5s...")
            await asyncio.sleep(5)

async def run_ibkr_ingester():
    util.patchAsyncio()
    ib = IB()
    conn = get_db()
    cursor = conn.cursor()

    while True:
        try:
            import random
            client_id = random.randint(10, 9999)
            await ib.connectAsync(IB_HOST, IB_PORT, clientId=client_id, timeout=30)
            logger.info(f"IBKR connected to {IB_HOST}:{IB_PORT}")
            break
        except Exception as e:
            logger.error(f"IBKR connect failed: {e}. Retrying in 5s...")
            await asyncio.sleep(5)

    contracts = [Stock(sym, "SMART", "USD") for sym in US_SYMBOLS]
    await ib.qualifyContractsAsync(*contracts)

    def on_tick(ticker):
        if ticker.bid and ticker.ask:
            cursor.execute(INSERT_QUERY, (ticker.contract.symbol, ticker.bid, ticker.ask, "US"))
            conn.commit()

    for contract in contracts:
        ib.reqMktData(contract, "", False, False)
    
    ib.pendingTickersEvent += lambda tickers: [on_tick(t) for t in tickers]

    while True:
        await asyncio.sleep(1)

async def main():
    await asyncio.gather(
        run_binance_ingester(),
        run_ibkr_ingester(),
    )

if __name__ == "__main__":
    asyncio.run(main())
