import os
import asyncio
import logging
from datetime import date
import httpx
import psycopg2
import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("Notifier")

TOKEN = os.environ["TELEGRAM_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]
TOPIC_EUA = int(os.environ.get("TOPIC_EUA", "0"))
TOPIC_CRIPTO = int(os.environ.get("TOPIC_CRIPTO", "0"))
TOPIC_LOGS = int(os.environ.get("TOPIC_LOGS", "0"))
TG_URL = f"https://api.telegram.org/bot{TOKEN}"
BR_TZ = pytz.timezone("America/Sao_Paulo")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}


async def tg_send(text: str, thread_id: int = 0, markup: dict = None):
    payload = {"chat_id": CHAT_ID, "text": text, "parse_mode": "HTML"}
    if thread_id > 0:
        payload["message_thread_id"] = thread_id
    if markup:
        payload["reply_markup"] = markup
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.post(f"{TG_URL}/sendMessage", json=payload)
            if r.status_code == 200:
                return
            logger.error(f"Telegram API Error ({r.status_code}): {r.text}")
            if thread_id > 0:
                payload.pop("message_thread_id", None)
                r_retry = await client.post(f"{TG_URL}/sendMessage", json=payload)
                if r_retry.status_code == 200:
                    return
                r_retry.raise_for_status()
            r.raise_for_status()
    except Exception as exc:
        logger.error(f"Telegram final error: {exc}")


def get_exchange_rate() -> float:
    try:
        r = httpx.get("https://economia.awesomeapi.com.br/json/last/USD-BRL", timeout=5)
        return float(r.json()["USDBRL"]["bid"])
    except Exception:
        return 5.80


def get_pnl_from_db(region: str) -> tuple[float, float]:
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cursor = conn.cursor()
        cursor.execute(
            "SELECT SUM(CASE WHEN side='BUY' THEN -quantity*price ELSE quantity*price END) FROM trade_logs WHERE region=%s AND mode='REAL' AND time >= CURRENT_DATE",
            (region,),
        )
        row = cursor.fetchone()
        pnl = float(row[0] or 0)
        cursor.execute("SELECT capital_ref FROM system_states WHERE region=%s ORDER BY time DESC LIMIT 1", (region,))
        base_row = cursor.fetchone()
        base = float(base_row[0]) if base_row else 10000.0
        conn.close()
        return pnl, base
    except Exception:
        return 0.0, 10000.0


async def send_daily_report(region: str, thread_id: int):
    pnl_usd, base = get_pnl_from_db(region)
    rate = get_exchange_rate()
    pnl_brl = pnl_usd * rate
    pct = (pnl_usd / base * 100) if base else 0
    dd_alert = ""
    if pct <= -50:
        dd_alert = "\n\n🚨 <b>LIMITE 50% ATINGIDO — SHADOW MODE ATIVO</b> 🚨"
    trend = "📈" if pnl_usd >= 0 else "📉"
    msg = (
        f"📊 <b>Relatório Diário — {region}</b>\n"
        f"📆 {date.today().strftime('%d/%m/%Y')}\n\n"
        f"🔹 <b>Capital Base:</b> ${base:,.2f}\n"
        f"💵 <b>PnL (USD):</b> <b>${pnl_usd:+,.2f}</b>\n"
        f"🇧🇷 <b>PnL (BRL):</b> <b>R${pnl_brl:+,.2f}</b>\n"
        f"{trend} <b>Variação:</b> {pct:+.2f}%"
        f"{dd_alert}"
    )
    await tg_send(msg, thread_id)


async def send_menu(thread_id: int):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🛑 HALT", "callback_data": "force_red"}],
            [{"text": "🔄 RESUME", "callback_data": "resume_bot"}],
            [{"text": "💻 REBOOT", "callback_data": "reboot"}],
        ]
    }
    await tg_send("⚙️ <b>Omni-Trader — Painel de Controle</b>", thread_id, keyboard)


async def handle_callback(action: str, thread_id: int):
    region = "EUA" if thread_id == TOPIC_EUA else "Cripto"
    messages = {
        "force_red": f"🚨 <b>HALT ATIVADO</b> — {region} entrou em Shadow Mode.",
        "resume_bot": f"🔄 <b>RECALIBRANDO</b> — Novo capital base registrado para {region}.",
        "reboot": f"💻 <b>REBOOT</b> — Reiniciando container {region}...",
    }
    if action in messages:
        await tg_send(messages[action], thread_id)


async def telegram_polling():
    last_update_id = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(f"{TG_URL}/getUpdates", params={"offset": last_update_id, "timeout": 20})
                if resp.status_code == 200:
                    data = resp.json()
                    for update in data.get("result", []):
                        last_update_id = update["update_id"] + 1
                        if "message" in update:
                            msg = update["message"]
                            text = msg.get("text", "")
                            thread_id = msg.get("message_thread_id", 0)
                            if text.startswith("/menu"):
                                await send_menu(thread_id)
                        elif "callback_query" in update:
                            cq = update["callback_query"]
                            thread_id = cq["message"].get("message_thread_id", 0)
                            await handle_callback(cq["data"], thread_id)
                            await client.post(f"{TG_URL}/answerCallbackQuery", json={"callback_query_id": cq["id"]})
        except Exception as e:
            logger.error(f"Polling error: {e}")
        await asyncio.sleep(1)


async def main():
    from datetime import datetime
    boot_time = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")

    scheduler = AsyncIOScheduler(timezone=BR_TZ)
    scheduler.add_job(send_daily_report, "cron", day_of_week="mon-fri", hour=10, minute=0, args=["EUA", TOPIC_EUA])
    scheduler.add_job(send_daily_report, "cron", hour=10, minute=0, args=["Cripto", TOPIC_CRIPTO])
    scheduler.start()

    logger.info("Notifier online, starting Telegram polling...")
    await tg_send(f"🔋 <b>Omni-Trader 9.1 — ONLINE</b>\n⏰ {boot_time}", TOPIC_LOGS)
    await telegram_polling()


if __name__ == "__main__":
    asyncio.run(main())
