import os
import asyncio
import logging
from datetime import date, datetime
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
INITIAL_CAPITAL_US = float(os.environ.get("INITIAL_CAPITAL_US", "10000"))


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
            logger.error(f"Telegram API error ({r.status_code}): {r.text}")
            if thread_id > 0:
                payload.pop("message_thread_id", None)
                await client.post(f"{TG_URL}/sendMessage", json=payload)
    except Exception as exc:
        logger.error(f"Telegram final error: {exc}")


def get_exchange_rate() -> float:
    try:
        r = httpx.get("https://economia.awesomeapi.com.br/json/last/USD-BRL", timeout=5)
        return float(r.json()["USDBRL"]["bid"])
    except Exception:
        return 5.80


def get_performance_from_db() -> dict:
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cursor = conn.cursor()

        cursor.execute(
            """
            SELECT SUM(CASE WHEN side='BUY' THEN -quantity*COALESCE(price,0)
                            ELSE quantity*COALESCE(price,0) END)
            FROM trade_logs
            WHERE region='US' AND mode='REAL' AND is_deleted=FALSE AND time >= CURRENT_DATE
            """,
        )
        row = cursor.fetchone()
        pnl = float(row[0] or 0)

        cursor.execute(
            "SELECT capital_ref, max_drawdown FROM system_states WHERE region='US' ORDER BY time DESC LIMIT 1"
        )
        state_row = cursor.fetchone()
        capital_ref = float(state_row[0]) if state_row else INITIAL_CAPITAL_US
        max_dd = float(state_row[1]) if state_row else 0.0

        cursor.execute(
            """
            SELECT (SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN -quantity*COALESCE(price,0)
                                             ELSE quantity*COALESCE(price,0) END), 0)
                    FROM trade_logs WHERE region='US' AND mode='REAL' AND is_deleted=FALSE)
            """
        )
        total_row = cursor.fetchone()
        total_pnl = float(total_row[0] or 0)
        current_balance = capital_ref + total_pnl

        conn.close()
        return {
            "pnl_day": pnl,
            "capital_ref": capital_ref,
            "current_balance": current_balance,
            "max_drawdown": max_dd,
        }
    except Exception as e:
        logger.error(f"Erro ao buscar performance no DB: {e}")
        return {"pnl_day": 0.0, "capital_ref": INITIAL_CAPITAL_US, "current_balance": INITIAL_CAPITAL_US, "max_drawdown": 0.0}


async def send_performance_report(thread_id: int = 0):
    data = get_performance_from_db()
    rate = get_exchange_rate()
    pnl_day = data["pnl_day"]
    pnl_brl = pnl_day * rate
    capital_ref = data["capital_ref"]
    current_balance = data["current_balance"]
    pnl_total = current_balance - capital_ref
    pnl_pct = (pnl_total / capital_ref * 100) if capital_ref else 0
    pnl_day_pct = (pnl_day / capital_ref * 100) if capital_ref else 0
    max_dd = data["max_drawdown"]
    now_br = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M")

    dd_alert = ""
    if pnl_pct <= -50:
        dd_alert = "\n\n🚨 <b>LIMITE -50% ATINGIDO — SHADOW MODE ATIVO</b> 🚨"
    elif max_dd <= -20:
        dd_alert = f"\n⚠️ <b>Drawdown máximo: {max_dd:.2f}%</b>"

    trend = "📈" if pnl_total >= 0 else "📉"
    msg = (
        f"📊 <b>Omni-Trader — Performance IBKR</b>\n"
        f"⏰ {now_br}\n\n"
        f"💼 <b>Capital Inicial:</b> ${capital_ref:,.2f}\n"
        f"💰 <b>Saldo Atual:</b> ${current_balance:,.2f}\n"
        f"💵 <b>P&amp;L Absoluto:</b> <b>${pnl_total:+,.2f}</b>\n"
        f"{trend} <b>P&amp;L Percentual:</b> {pnl_pct:+.2f}%\n"
        f"📅 <b>P&amp;L Hoje:</b> ${pnl_day:+,.2f} ({pnl_day_pct:+.2f}%)\n"
        f"🇧🇷 <b>P&amp;L BRL:</b> R${pnl_brl:+,.2f}\n"
        f"📉 <b>Drawdown Máximo:</b> {max_dd:.2f}%"
        f"{dd_alert}"
    )
    await tg_send(msg, thread_id)


async def send_alert(text: str, thread_id: int = 0):
    await tg_send(text, thread_id)


async def send_menu(thread_id: int):
    keyboard = {
        "inline_keyboard": [
            [{"text": "🛑 HALT", "callback_data": "force_red"}],
            [{"text": "🔄 RESUME", "callback_data": "resume_bot"}],
            [{"text": "📊 STATUS", "callback_data": "status"}],
            [{"text": "💻 REBOOT", "callback_data": "reboot"}],
        ]
    }
    await tg_send("⚙️ <b>Omni-Trader — Painel de Controle</b>", thread_id, keyboard)


async def handle_callback(action: str, thread_id: int):
    if action == "status":
        await send_performance_report(thread_id)
        return
    messages = {
        "force_red": "🚨 <b>HALT ATIVADO</b> — Bot entrou em Shadow Mode.",
        "resume_bot": "🔄 <b>RECALIBRANDO</b> — Novo capital base registrado.",
        "reboot": "💻 <b>REBOOT</b> — Reiniciando serviço...",
    }
    if action in messages:
        await tg_send(messages[action], thread_id)


async def telegram_polling():
    last_update_id = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.get(
                    f"{TG_URL}/getUpdates",
                    params={"offset": last_update_id, "timeout": 20},
                )
                if resp.status_code == 200:
                    for update in resp.json().get("result", []):
                        last_update_id = update["update_id"] + 1
                        if "message" in update:
                            msg = update["message"]
                            text = msg.get("text", "")
                            thread_id = msg.get("message_thread_id", TOPIC_LOGS)
                            if text.startswith("/menu"):
                                await send_menu(thread_id)
                            elif text.startswith("/status"):
                                await send_performance_report(thread_id)
                        elif "callback_query" in update:
                            cq = update["callback_query"]
                            thread_id = cq["message"].get("message_thread_id", TOPIC_LOGS)
                            await handle_callback(cq["data"], thread_id)
                            await client.post(
                                f"{TG_URL}/answerCallbackQuery",
                                json={"callback_query_id": cq["id"]},
                            )
        except Exception as e:
            logger.error(f"Polling error: {e}")
        await asyncio.sleep(1)


async def main():
    boot_time = datetime.now(BR_TZ).strftime("%d/%m/%Y %H:%M:%S")
    scheduler = AsyncIOScheduler(timezone=BR_TZ)
    scheduler.add_job(send_performance_report, "cron", day_of_week="mon-fri", hour=10, minute=0, args=[TOPIC_EUA])
    scheduler.add_job(send_performance_report, "cron", day_of_week="mon-fri", hour=20, minute=0, args=[TOPIC_EUA])
    scheduler.start()

    logger.info("Notifier online, polling Telegram...")
    await tg_send(
        f"🔋 <b>Omni-Trader IBKR — ONLINE</b>\n"
        f"⏰ {boot_time}\n"
        f"💼 Capital Base: ${INITIAL_CAPITAL_US:,.2f}\n"
        f"📡 Streams: IBKR + Binance\n"
        f"🤖 IA: RandomForest + PPO + Sentimento",
        TOPIC_LOGS,
    )
    await telegram_polling()


if __name__ == "__main__":
    asyncio.run(main())
