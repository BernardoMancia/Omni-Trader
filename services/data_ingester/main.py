import os
import json
import asyncio
import logging
import psycopg2
import websockets

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

def get_db():
    return psycopg2.connect(**DB_PARAMS)

async def run_binance_ingester():
    symbols = ["btcusdt", "ethusdt", "bnbusdt"]
    stream = "/".join([f"{s}@bookTicker" for s in symbols])
    uri = f"wss://stream.binance.com:9443/ws/{stream}"
    conn = get_db()
    cursor = conn.cursor()
    logger.info(f"Binance ingester connected: {symbols}")
    async with websockets.connect(uri) as ws:
        async for raw in ws:
            data = json.loads(raw)
            symbol = data.get("s")
            bid = float(data.get("b", 0))
            ask = float(data.get("a", 0))
            if symbol and bid and ask:
                cursor.execute(INSERT_QUERY, (symbol, bid, ask, "ASIA"))
                conn.commit()

async def run_alpaca_ingester():
    uri = "wss://stream.data.alpaca.markets/v2/sip"
    api_key = os.environ["ALPACA_API_KEY"]
    secret_key = os.environ["ALPACA_SECRET_KEY"]
    conn = get_db()
    cursor = conn.cursor()
    logger.info("Alpaca ingester connecting...")
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"action": "auth", "key": api_key, "secret": secret_key}))
        await ws.send(json.dumps({"action": "subscribe", "quotes": ["SPY", "QQQ", "AAPL", "TSLA"]}))
        async for raw in ws:
            messages = json.loads(raw)
            for msg in messages:
                if msg.get("T") == "q":
                    cursor.execute(INSERT_QUERY, (msg["S"], msg["bp"], msg["ap"], "US"))
                    conn.commit()

async def main():
    await asyncio.gather(
        run_binance_ingester(),
        run_alpaca_ingester(),
    )

if __name__ == "__main__":
    asyncio.run(main())
