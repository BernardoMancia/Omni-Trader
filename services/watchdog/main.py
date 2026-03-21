import os
import time
import logging
import psycopg2
import httpx
from datetime import datetime, timezone, timedelta

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Watchdog")

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TOPIC_LOGS = int(os.environ.get("TOPIC_LOGS", "0"))
TG_URL = f"https://api.telegram.org/bot{TOKEN}"
SILENCE_MINUTES = 10

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

REGION_MAP = {"US": "Alpaca (EUA)", "ASIA": "Binance (Cripto)"}

def fire_alert(region: str):
    msg = (
        f"🚨 <b>DEAD MAN'S SWITCH — {REGION_MAP.get(region, region)}</b>\n"
        f"Sem dados há mais de {SILENCE_MINUTES} minutos. Verifique imediatamente!"
    )
    payload = {"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}
    if TOPIC_LOGS > 0:
        payload["message_thread_id"] = TOPIC_LOGS
    try:
        httpx.post(f"{TG_URL}/sendMessage", json=payload, timeout=10)
        logger.error(f"ALERT FIRED: {region} silent")
    except Exception as exc:
        logger.critical(f"Failed to fire alert for {region}: {exc}")

def check():
    try:
        conn = psycopg2.connect(**DB_PARAMS)
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
        conn.close()
    except Exception as exc:
        logger.error(f"DB check failed: {exc}")

def run():
    logger.info("Watchdog armed.")
    while True:
        check()
        time.sleep(60)

if __name__ == "__main__":
    run()
