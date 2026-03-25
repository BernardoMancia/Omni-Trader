import os
import asyncio
import logging
import json
import psycopg2
from ib_insync import IB, Stock, util

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DataIngester")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}
IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "24004"))
US_SYMBOLS = os.environ.get("IBKR_SYMBOLS", "AAPL,MSFT,TSLA,SPY,QQQ,VOO").split(",")
CRYPTO_SYMBOLS = os.environ.get("CRYPTO_SYMBOLS", "btcusdt,ethusdt,bnbusdt").split(",")
HISTORY_YEARS = int(os.environ.get("RF_TRAIN_YEARS", "5"))
INSERT_TICK = "INSERT INTO market_data (symbol, bid, ask, region) VALUES (%s, %s, %s, %s)"
INSERT_HIST = "INSERT INTO price_history (symbol, date, open, high, low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, date) DO NOTHING"


def get_db():
    import time
    while True:
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            logger.info("Database conectado.")
            return conn
        except Exception as e:
            logger.error(f"DB connect falhou: {e}. Retry em 5s...")
            time.sleep(5)


async def run_history_fetcher():
    """Baixa 5 anos de OHLCV via yfinance e persiste no DB para treino do RandomForest."""
    try:
        import yfinance as yf
        conn = get_db()
        cursor = conn.cursor()
        logger.info(f"Iniciando download do histórico: {US_SYMBOLS}")
        for sym in US_SYMBOLS:
            try:
                df = yf.download(sym, period=f"{HISTORY_YEARS}y", interval="1d", progress=False, auto_adjust=True)
                if df.empty:
                    logger.warning(f"Sem histórico para {sym}.")
                    continue
                rows = []
                for date, row in df.iterrows():
                    rows.append((
                        sym,
                        date.date(),
                        float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]),
                        float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]),
                        float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]),
                        float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]),
                        float(row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"]),
                    ))
                cursor.executemany(INSERT_HIST, rows)
                conn.commit()
                logger.info(f"Histórico {sym}: {len(rows)} candles salvos.")
            except Exception as e:
                logger.error(f"Erro histórico {sym}: {e}")
        conn.close()
    except ImportError:
        logger.warning("yfinance não disponível. Histórico não carregado.")


async def run_binance_ingester():
    import websockets
    stream = "/".join([f"{s}@bookTicker" for s in CRYPTO_SYMBOLS])
    uri = f"wss://stream.binance.com:9443/ws/{stream}"
    conn = get_db()
    cursor = conn.cursor()
    backoff = 5

    while True:
        try:
            logger.info(f"Binance conectando: {CRYPTO_SYMBOLS}")
            async with websockets.connect(uri, ping_interval=20, ping_timeout=10) as ws:
                logger.info("Binance stream ativo.")
                backoff = 5
                async for raw in ws:
                    data = json.loads(raw)
                    sym = data.get("s")
                    bid = float(data.get("b", 0))
                    ask = float(data.get("a", 0))
                    if sym and bid and ask:
                        cursor.execute(INSERT_TICK, (sym, bid, ask, "ASIA"))
                        conn.commit()
        except Exception as e:
            logger.warning(f"Binance WS encerrada ({e}). Reconnect em {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def run_ibkr_ingester():
    util.patchAsyncio()
    ib = IB()
    conn = get_db()
    cursor = conn.cursor()

    while True:
        try:
            import random
            client_id = random.randint(20000, 29999)
            await ib.connectAsync(IB_HOST, IB_PORT, clientId=client_id, timeout=30)
            logger.info(f"IBKR ingester conectado em {IB_HOST}:{IB_PORT}")
            break
        except Exception as e:
            logger.error(f"IBKR connect falhou: {e}. Retry em 5s...")
            await asyncio.sleep(5)

    contracts = [Stock(sym, "SMART", "USD") for sym in US_SYMBOLS]
    await ib.qualifyContractsAsync(*contracts)

    def on_tick(ticker):
        if ticker.bid and ticker.ask:
            cursor.execute(INSERT_TICK, (ticker.contract.symbol, ticker.bid, ticker.ask, "US"))
            conn.commit()

    for contract in contracts:
        ib.reqMktData(contract, "", False, False)
    ib.pendingTickersEvent += lambda tickers: [on_tick(t) for t in tickers]

    while ib.isConnected():
        await asyncio.sleep(1)

    logger.error("IBKR ingester desconectado. Reiniciando...")
    raise ConnectionError("IBKR disconnected")


async def main():
    await run_history_fetcher()
    while True:
        try:
            await asyncio.gather(
                run_binance_ingester(),
                run_ibkr_ingester(),
            )
        except Exception as e:
            logger.error(f"Ingester crash: {e}. Reiniciando em 10s...")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
