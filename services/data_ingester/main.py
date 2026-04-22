import os
import asyncio
import logging
import json
import psycopg2
from datetime import datetime
from ib_insync import IB, Stock, util

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DataIngester")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}
IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "4004"))
US_SYMBOLS = os.environ.get("IBKR_SYMBOLS", "AAPL,MSFT,TSLA,SPY,QQQ,VOO").split(",")
_br_raw = os.environ.get("BR_SYMBOLS", "")
BR_SYMBOLS = [s.strip() for s in _br_raw.split(",") if s.strip()]
CRYPTO_SYMBOLS = os.environ.get("CRYPTO_SYMBOLS", "btcusdt,ethusdt,bnbusdt").split(",")
HISTORY_YEARS = int(os.environ.get("RF_TRAIN_YEARS", "5"))
HISTORY_UPDATE_MINUTES = 10
INSERT_TICK = "INSERT INTO market_data (symbol, bid, ask, region) VALUES (%s, %s, %s, %s)"
INSERT_HIST = "INSERT INTO price_history (symbol, date, open, high, low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume"


def get_db():
    import time as _time
    while True:
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            logger.info("Database conectado.")
            return conn
        except Exception as e:
            logger.error(f"DB connect falhou: {e}. Retry em 5s...")
            _time.sleep(5)


def _get_yf_session():
    import requests
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    })
    return session


def _download_history_yfinance(sym: str, period: str = "5y"):
    try:
        import yfinance as yf
        session = _get_yf_session()
        df = yf.download(sym, period=period, interval="1d", progress=False, auto_adjust=True, session=session)
        if df.empty:
            return []
        rows = []
        for date, row in df.iterrows():
            rows.append((
                sym, date.date(),
                float(row["Open"].iloc[0] if hasattr(row["Open"], "iloc") else row["Open"]),
                float(row["High"].iloc[0] if hasattr(row["High"], "iloc") else row["High"]),
                float(row["Low"].iloc[0] if hasattr(row["Low"], "iloc") else row["Low"]),
                float(row["Close"].iloc[0] if hasattr(row["Close"], "iloc") else row["Close"]),
                int(row["Volume"].iloc[0] if hasattr(row["Volume"], "iloc") else row["Volume"]),
            ))
        return rows
    except Exception as e:
        logger.warning(f"yfinance falhou para {sym}: {e}")
        return []


async def _fetch_from_ibkr(sym: str, years: int):
    import random
    ib = IB()
    try:
        client_id = random.randint(30000, 39999)
        await ib.connectAsync(IB_HOST, IB_PORT, clientId=client_id, timeout=20)
        contract = Stock(sym, "SMART", "USD")
        await ib.qualifyContractsAsync(contract)
        bars = await ib.reqHistoricalDataAsync(
            contract, endDateTime="", durationStr=f"{years} Y",
            barSizeSetting="1 day", whatToShow="TRADES", useRTH=True
        )
        ib.disconnect()
        return [(sym, b.date, b.open, b.high, b.low, b.close, b.volume) for b in bars]
    except Exception as e:
        logger.error(f"Fallback IBKR falhou para {sym}: {e}")
        if ib.isConnected():
            ib.disconnect()
        return []


async def run_history_fetcher():
    conn = get_db()
    cursor = conn.cursor()
    logger.info(f"Iniciando download do histórico completo ({HISTORY_YEARS}Y): {US_SYMBOLS}")

    for sym in US_SYMBOLS:
        rows = _download_history_yfinance(sym, f"{HISTORY_YEARS}y")
        if not rows:
            rows = await _fetch_from_ibkr(sym, HISTORY_YEARS)
        if rows:
            cursor.executemany(INSERT_HIST, rows)
            conn.commit()
            logger.info(f"Histórico {sym}: {len(rows)} candles salvos.")
        else:
            logger.error(f"Falha total ao obter histórico para {sym}.")
        await asyncio.sleep(2)

    conn.close()
    logger.info("Download inicial do historico US concluido.")


async def run_history_fetcher_br():
    if not BR_SYMBOLS:
        logger.info("Sem ativos BR configurados, pulando.")
        return
    conn = get_db()
    cursor = conn.cursor()
    logger.info(f"Iniciando download BR ({HISTORY_YEARS}Y): {BR_SYMBOLS}")

    for sym in BR_SYMBOLS:
        rows = _download_history_yfinance(sym, f"{HISTORY_YEARS}y")
        if rows:
            cursor.executemany(INSERT_HIST, rows)
            conn.commit()
            logger.info(f"Historico BR {sym}: {len(rows)} candles salvos.")
        else:
            logger.error(f"Falha ao obter historico BR para {sym}.")
        await asyncio.sleep(2)

    conn.close()
    logger.info("Download inicial do historico BR concluido.")


async def run_history_updater():
    await asyncio.sleep(30)
    while True:
        try:
            conn = get_db()
            cursor = conn.cursor()
            now = datetime.utcnow()
            logger.info(f"Atualizando histórico (loop {HISTORY_UPDATE_MINUTES}min)...")

            for sym in US_SYMBOLS:
                rows = _download_history_yfinance(sym, "5d")
                if rows:
                    cursor.executemany(INSERT_HIST, rows)
                    conn.commit()
                    logger.debug(f"Histórico {sym} atualizado: {len(rows)} candles.")
                await asyncio.sleep(1)

            conn.close()
            logger.info(f"Atualização de histórico concluída. Próxima em {HISTORY_UPDATE_MINUTES}min.")
        except Exception as e:
            logger.error(f"Erro no history updater: {e}")

        await asyncio.sleep(HISTORY_UPDATE_MINUTES * 60)


async def run_history_updater_br():
    if not BR_SYMBOLS:
        return
    await asyncio.sleep(60)
    while True:
        try:
            conn = get_db()
            cursor = conn.cursor()
            logger.info("Atualizando historico BR...")
            for sym in BR_SYMBOLS:
                rows = _download_history_yfinance(sym, "5d")
                if rows:
                    cursor.executemany(INSERT_HIST, rows)
                    conn.commit()
                await asyncio.sleep(1)
            conn.close()
            logger.info(f"Historico BR atualizado. Proxima em {HISTORY_UPDATE_MINUTES}min.")
        except Exception as e:
            logger.error(f"Erro no BR history updater: {e}")
        await asyncio.sleep(HISTORY_UPDATE_MINUTES * 60)


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
    ib.reqMarketDataType(3)

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
    await run_history_fetcher_br()
    while True:
        try:
            tasks = [
                run_binance_ingester(),
                run_ibkr_ingester(),
                run_history_updater(),
            ]
            if BR_SYMBOLS:
                tasks.append(run_history_updater_br())
            await asyncio.gather(*tasks)
        except Exception as e:
            logger.error(f"Ingester crash: {e}. Reiniciando em 10s...")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main())
