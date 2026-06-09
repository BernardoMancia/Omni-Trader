import os
import time
import logging
import httpx
from datetime import datetime, timezone, timedelta
from services.shared.db import get_conn

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Watchdog")

NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://notifier:8001/notify")
SILENCE_MINUTES = 10

REGION_MAP = {"US": "IBKR (EUA)", "ASIA": "Binance (Cripto)", "BR": "B3 (Brasil)"}

def fire_alert(region: str):
    msg = (
        f"🚨 <b>DEAD MAN'S SWITCH — {REGION_MAP.get(region, region)}</b>\n"
        f"Sem dados há mais de {SILENCE_MINUTES} minutos. Verifique imediatamente!"
    )
    try:
        httpx.post(NOTIFIER_URL, json={"topic": "logs", "text": msg}, timeout=10.0)
        logger.error(f"ALERT FIRED: {region} silent")
    except Exception as exc:
        logger.critical(f"Failed to fire alert for {region}: {exc}")

def check():
    try:
        with get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT region, MAX(time) FROM market_data GROUP BY region;")
            now = datetime.now(timezone.utc)
            seen = set()
            for region, last_seen in cursor.fetchall():
                seen.add(region)
                diff_min = (now - last_seen).total_seconds() / 60
                if diff_min > SILENCE_MINUTES:
                    fire_alert(region)
            for region in set(REGION_MAP.keys()) - seen:
                logger.warning(f"Region {region} has no data yet.")
    except Exception as exc:
        logger.error(f"DB check failed: {exc}")

def run():
    logger.info("Watchdog armed.")
    while True:
        check()
        time.sleep(60)

if __name__ == "__main__":
    run()
