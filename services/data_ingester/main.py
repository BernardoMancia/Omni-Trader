import os
import asyncio
import logging
import json
import time as _time_module
from psycopg2.extras import execute_values
from ib_insync import IB, Stock, util
from services.shared.db import get_conn, get_pool
from services.shared.utils import get_yf_session

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("DataIngester")
IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "4004"))
US_SYMBOLS = os.environ.get("IBKR_SYMBOLS", "AAPL,MSFT,TSLA,SPY,QQQ,VOO").split(",")
_br_raw = os.environ.get("BR_SYMBOLS", "")
BR_SYMBOLS = [s.strip() for s in _br_raw.split(",") if s.strip()]
CRYPTO_SYMBOLS = os.environ.get("CRYPTO_SYMBOLS", "btcusdt,ethusdt,bnbusdt").split(",")
HISTORY_YEARS = int(os.environ.get("RF_TRAIN_YEARS", "5"))
HISTORY_UPDATE_MINUTES = 10
INSERT_TICK = "INSERT INTO market_data (symbol, bid, ask, region) VALUES (%s, %s, %s, %s)"
INSERT_TICK_BATCH = "INSERT INTO market_data (symbol, bid, ask, region) VALUES %s"
INSERT_HIST = "INSERT INTO price_history (symbol, date, open, high, low, close, volume) VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (symbol, date) DO UPDATE SET open=EXCLUDED.open, high=EXCLUDED.high, low=EXCLUDED.low, close=EXCLUDED.close, volume=EXCLUDED.volume"
TICK_BATCH_SIZE = 100
TICK_FLUSH_INTERVAL = 1.0  # seconds


def get_db():
    """Get a raw connection from the shared pool."""
    pool = get_pool()
    return pool.getconn()


def _flush_ticks(cursor, conn, buffer: list) -> None:
    """Batch-insert buffered ticks using execute_values and commit."""
    if not buffer:
        return
    try:
        execute_values(cursor, INSERT_TICK_BATCH, buffer)
        conn.commit()
    except Exception as e:
        logger.error(f"Flush falhou: {e}. Tentando reconectar...")
        try:
            conn.rollback()
        except Exception:
            pass
        raise


def _reconnect_db():
    """Re-establish DB connection and cursor from pool after a failure."""
    logger.info("Reconectando ao banco de dados...")
    pool = get_pool()
    conn = pool.getconn()
    cursor = conn.cursor()
    return conn, cursor


def _download_history_yfinance(sym: str, period: str = "5y"):
    try:
        import yfinance as yf
        session = get_yf_session()
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
    with get_conn() as conn:
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
    logger.info("Download inicial do historico US concluido.")


async def run_history_fetcher_br():
    if not BR_SYMBOLS:
        logger.info("Sem ativos BR configurados, pulando.")
        return
    with get_conn() as conn:
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
    logger.info("Download inicial do historico BR concluido.")


async def run_history_updater():
    await asyncio.sleep(30)
    while True:
        try:
            with get_conn() as conn:
                cursor = conn.cursor()
                logger.info(f"Atualizando histórico (loop {HISTORY_UPDATE_MINUTES}min)...")
                for sym in US_SYMBOLS:
                    rows = _download_history_yfinance(sym, "5d")
                    if rows:
                        cursor.executemany(INSERT_HIST, rows)
                        conn.commit()
                        logger.debug(f"Histórico {sym} atualizado: {len(rows)} candles.")
                    await asyncio.sleep(1)
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
            with get_conn() as conn:
                cursor = conn.cursor()
                logger.info("Atualizando historico BR...")
                for sym in BR_SYMBOLS:
                    rows = _download_history_yfinance(sym, "5d")
                    if rows:
                        cursor.executemany(INSERT_HIST, rows)
                        conn.commit()
                    await asyncio.sleep(1)
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
    tick_buffer: list[tuple] = []
    last_flush = _time_module.monotonic()

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
                        tick_buffer.append((sym, bid, ask, "ASIA"))

                    now = _time_module.monotonic()
                    if len(tick_buffer) >= TICK_BATCH_SIZE or (tick_buffer and now - last_flush >= TICK_FLUSH_INTERVAL):
                        try:
                            _flush_ticks(cursor, conn, tick_buffer)
                        except Exception:
                            conn, cursor = _reconnect_db()
                            _flush_ticks(cursor, conn, tick_buffer)
                        tick_buffer.clear()
                        last_flush = now
        except Exception as e:
            # Flush remaining ticks before reconnecting
            if tick_buffer:
                try:
                    _flush_ticks(cursor, conn, tick_buffer)
                except Exception:
                    try:
                        conn, cursor = _reconnect_db()
                        _flush_ticks(cursor, conn, tick_buffer)
                    except Exception as flush_err:
                        logger.error(f"Perda de {len(tick_buffer)} ticks no flush de emergência: {flush_err}")
                tick_buffer.clear()
            logger.warning(f"Binance WS encerrada ({e}). Reconnect em {backoff}s...")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)


async def run_ibkr_ingester():
    util.patchAsyncio()
    ib = IB()
    conn = get_db()
    cursor = conn.cursor()
    tick_buffer: list[tuple] = []

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
        nonlocal conn, cursor
        if ticker.bid and ticker.ask:
            tick_buffer.append((ticker.contract.symbol, ticker.bid, ticker.ask, "US"))

    for contract in contracts:
        ib.reqMktData(contract, "", False, False)
    ib.pendingTickersEvent += lambda tickers: [on_tick(t) for t in tickers]

    last_flush = _time_module.monotonic()
    while ib.isConnected():
        await asyncio.sleep(0.5)
        now = _time_module.monotonic()
        if len(tick_buffer) >= TICK_BATCH_SIZE or (tick_buffer and now - last_flush >= TICK_FLUSH_INTERVAL):
            try:
                _flush_ticks(cursor, conn, tick_buffer)
            except Exception:
                conn, cursor = _reconnect_db()
                try:
                    _flush_ticks(cursor, conn, tick_buffer)
                except Exception as flush_err:
                    logger.error(f"IBKR perda de {len(tick_buffer)} ticks: {flush_err}")
            tick_buffer.clear()
            last_flush = now

    logger.error("IBKR ingester desconectado. Retornando para reinício pelo supervisor.")
    return


async def _supervised(coro_fn, name: str):
    """Supervisor that restarts a coroutine on crash with a 10s backoff."""
    while True:
        try:
            await coro_fn()
        except Exception as e:
            logger.error(f"[{name}] crash: {e}. Reiniciando em 10s...")
            await asyncio.sleep(10)


async def main():
    await run_history_fetcher()
    await run_history_fetcher_br()

    tasks = [
        _supervised(run_binance_ingester, "Binance"),
        _supervised(run_ibkr_ingester, "IBKR"),
        _supervised(run_history_updater, "HistoryUpdater"),
    ]
    if BR_SYMBOLS:
        tasks.append(_supervised(run_history_updater_br, "HistoryUpdaterBR"))
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
