import os
import asyncio
import logging
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import psycopg2
import httpx
from datetime import datetime, timezone
from services.ai_brain.forest import ForestEngine
from services.ai_brain.sentiment import SentimentEngine
from services.shared.risk import MarketState

try:
    import exchange_calendars as xcals
    NYSE_CAL = xcals.get_calendar("XNYS")
    _XCALS_OK = True
except Exception:
    NYSE_CAL = None
    _XCALS_OK = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AIBrain")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}
ROUTER_URL = os.environ.get("ROUTER_URL", "http://router:28000/order")
NOTIFIER_URL = os.environ.get("NOTIFIER_URL", "http://notifier:8001/notify")
SENTIMENT_QUERY = os.environ.get("SENTIMENT_QUERY", "US stock market economy recession")
IBKR_SYMBOLS = os.environ.get("IBKR_SYMBOLS", "AAPL,MSFT,TSLA,SPY,QQQ,VOO").split(",")
RF_TRAIN_YEARS = int(os.environ.get("RF_TRAIN_YEARS", "5"))
LOOP_INTERVAL = int(os.environ.get("BRAIN_LOOP_INTERVAL", "15"))
PPO_CONFIDENCE_MIN = float(os.environ.get("PPO_CONFIDENCE_MIN", "0.70"))
SLEEP_OUTSIDE_MARKET = int(os.environ.get("SLEEP_OUTSIDE_MARKET", "300"))


class PPOActorCritic(nn.Module):
    def __init__(self, state_dim: int, action_dim: int):
        super().__init__()
        self.shared = nn.Sequential(
            nn.Linear(state_dim, 256), nn.ReLU(),
            nn.Linear(256, 128), nn.ReLU(),
        )
        self.actor = nn.Sequential(nn.Linear(128, action_dim), nn.Softmax(dim=-1))
        self.critic = nn.Linear(128, 1)

    def forward(self, x: torch.Tensor):
        features = self.shared(x)
        return self.actor(features), self.critic(features)


class PPOAgent:
    ACTIONS = ["HOLD", "BUY", "SELL"]

    def __init__(self, state_dim: int = 9, action_dim: int = 3):
        self.device = torch.device("cpu")
        self.model = PPOActorCritic(state_dim, action_dim).to(self.device)
        self.optimizer = torch.optim.Adam(self.model.parameters(), lr=3e-4)

    def get_action(self, state_vector: np.ndarray) -> dict:
        state_tensor = torch.FloatTensor(state_vector).unsqueeze(0).to(self.device)
        with torch.no_grad():
            probs, _ = self.model(state_tensor)
        action_idx = int(torch.argmax(probs).item())
        confidence = float(probs[0][action_idx].item())
        return {"action": self.ACTIONS[action_idx], "confidence": round(confidence, 4)}


def is_market_open() -> bool:
    if not _XCALS_OK or NYSE_CAL is None:
        return True
    now = pd.Timestamp.now(tz="America/New_York")
    return NYSE_CAL.is_open_on_minute(now)


def _get_ny_time():
    import pytz
    return datetime.now(pytz.timezone("America/New_York"))


def _get_br_time():
    import pytz
    return datetime.now(pytz.timezone("America/Sao_Paulo"))


def _fetch_latest_features(cursor, symbol: str) -> np.ndarray | None:
    try:
        cursor.execute(
            "SELECT open, high, low, close, volume FROM price_history "
            "WHERE symbol=%s ORDER BY date DESC LIMIT 60",
            (symbol,)
        )
        rows = cursor.fetchall()
        if len(rows) < 50:
            return None

        import ta
        cols = ["Open", "High", "Low", "Close", "Volume"]
        df = pd.DataFrame(rows[::-1], columns=cols)
        close = df["Close"].astype(float)
        high = df["High"].astype(float)
        low = df["Low"].astype(float)
        volume = df["Volume"].astype(float)

        rsi = ta.momentum.RSIIndicator(close).rsi().iloc[-1]
        macd_obj = ta.trend.MACD(close)
        macd_val = macd_obj.macd().iloc[-1]
        macd_sig = macd_obj.macd_signal().iloc[-1]
        bb = ta.volatility.BollingerBands(close)
        bb_pct = bb.bollinger_pband().iloc[-1]
        ema_20 = ta.trend.EMAIndicator(close, window=20).ema_indicator().iloc[-1]
        ema_50 = ta.trend.EMAIndicator(close, window=50).ema_indicator().iloc[-1]
        atr = ta.volatility.AverageTrueRange(high, low, close).average_true_range().iloc[-1]
        vol_ratio = (volume / volume.rolling(20).mean()).iloc[-1]
        returns = close.pct_change().iloc[-1]

        features = np.array([
            rsi, macd_val, macd_sig, bb_pct, ema_20, ema_50, atr, vol_ratio, returns
        ], dtype=np.float32)

        features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
        return features
    except Exception as e:
        logger.error(f"Erro ao calcular features para {symbol}: {e}")
        return None


def _signal_emoji(signal: str) -> str:
    m = {"BUY": "\U0001f7e2", "SELL": "\U0001f534", "HOLD": "\U0001f7e1"}
    return m.get(signal, "\u26aa")


def _build_thought(
    symbol: str, features: np.ndarray | None,
    sentiment_score: float, rf_decision: dict, ppo_decision: dict,
    final_action: str, market_open: bool, score: float = 0.0
) -> str:
    if features is None:
        return f"\u26a0\ufe0f <code>{symbol:>5}</code> \u2502 Dados insuficientes"

    rsi, macd_val, macd_sig, bb_pct, ema_20, ema_50, atr, vol_ratio, returns = features.tolist()

    fa_clean = final_action.split(" ")[0] if isinstance(final_action, str) else final_action
    fa_em = _signal_emoji(fa_clean)
    trend = "\u25b2" if ema_20 > ema_50 else "\u25bc"
    macd_dir = "+" if macd_val > 0 else "-"
    rf_s = rf_decision['signal'][0]
    ppo_s = ppo_decision['action'][0]

    return (
        f"{fa_em} <code>{symbol:>5}</code> \u2502 "
        f"RSI <code>{rsi:5.1f}</code> \u2502 "
        f"MACD {macd_dir} \u2502 "
        f"{trend} \u2502 "
        f"RF:{rf_s} <code>{rf_decision['confidence']:.0%}</code> \u2502 "
        f"PPO:{ppo_s} <code>{ppo_decision['confidence']:.0%}</code> \u2502 "
        f"<b>{fa_clean}</b>"
    )


def _save_thought(cursor, conn, symbol: str, thought: str, features, sentiment, rf, ppo, final_action: str):
    try:
        rsi = float(features[0]) if features is not None else None
        macd = float(features[1]) if features is not None else None
        cursor.execute(
            "INSERT INTO ai_thoughts (symbol, thought, rsi, macd, sentiment, rf_signal, rf_conf, ppo_action, ppo_conf, final_action) "
            "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
            (symbol, thought, rsi, macd, sentiment,
             rf["signal"], rf["confidence"],
             ppo["action"], ppo["confidence"], final_action)
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _save_prediction(cursor, conn, symbol: str, direction: str, confidence: float, source: str):
    try:
        cursor.execute(
            "INSERT INTO predictions (symbol, direction, confidence, horizon_days, source) VALUES (%s, %s, %s, %s, %s)",
            (symbol, direction, confidence, 1, source)
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _update_system_state(cursor, conn, state_name: str, drawdown: float, max_dd: float, capital: float):
    try:
        cursor.execute(
            "INSERT INTO system_states (region, state, drawdown, max_drawdown, capital_ref) VALUES (%s, %s, %s, %s, %s)",
            ("US", state_name, drawdown, max_dd, capital),
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _get_position(cursor, symbol: str) -> float:
    try:
        cursor.execute(
            "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END), 0) "
            "FROM trade_logs WHERE symbol=%s AND is_deleted=FALSE",
            (symbol,)
        )
        row = cursor.fetchone()
        return float(row[0]) if row else 0.0
    except Exception:
        return 0.0


def _get_all_positions(cursor) -> dict:
    try:
        cursor.execute(
            "SELECT symbol, COALESCE(SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END), 0) as net_qty "
            "FROM trade_logs WHERE is_deleted=FALSE GROUP BY symbol HAVING SUM(CASE WHEN side='BUY' THEN quantity ELSE -quantity END) > 0"
        )
        return {row[0]: float(row[1]) for row in cursor.fetchall()}
    except Exception:
        return {}


def _log_trade(cursor, conn, symbol: str, side: str, quantity: float, price: float, mode: str = "PAPER"):
    try:
        cursor.execute(
            "INSERT INTO trade_logs (symbol, side, quantity, price, mode, region, is_deleted) "
            "VALUES (%s, %s, %s, %s, %s, %s, FALSE)",
            (symbol, side, quantity, price, mode, "US")
        )
        conn.commit()
        logger.info(f"\U0001f4be Trade registrado: {side} {quantity:.4f} {symbol} @ ${price:.2f} [{mode}]")
    except Exception as e:
        conn.rollback()
        logger.error(f"Erro ao registrar trade: {e}")


async def _send_order(symbol: str, side: str, quantity: float, use_fractional: bool, equity: float):
    async with httpx.AsyncClient(timeout=8.0) as client:
        payload = {
            "symbol": symbol, "side": side, "quantity": quantity,
            "region": "US", "use_fractional": use_fractional, "equity": equity,
        }
        try:
            r = await client.post(ROUTER_URL, json=payload)
            logger.info(f"Router response [{side} {symbol}]: {r.status_code} {r.text[:120]}")
        except Exception as e:
            logger.error(f"Falha ao enviar ordem ao router: {e}")


async def _notify_telegram(topic: str, text: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(NOTIFIER_URL, json={"topic": topic, "text": text})
    except Exception:
        pass


def _compute_score(rf_decision: dict, ppo_decision: dict, sentiment_score: float) -> float:
    rf_conf = rf_decision["confidence"]
    ppo_conf = ppo_decision["confidence"]
    consensus_bonus = 0.15 if rf_decision["signal"] == ppo_decision["action"] else 0.0
    return (rf_conf * 0.4) + (ppo_conf * 0.6) + consensus_bonus + (sentiment_score * 0.1)


async def main():
    ppo = PPOAgent(state_dim=9)
    forest = ForestEngine()
    sentiment = SentimentEngine()
    initial_capital = float(os.environ.get("INITIAL_CAPITAL_US", "10000"))
    trading_mode = os.environ.get("IB_TRADING_MODE", "paper").upper()

    if not forest.is_ready():
        logger.info("Aguardando ingester popular o DB com hist\u00f3rico...")
        await _notify_telegram("thoughts", "\u23f3 Brain aguardando dados do ingester...")

        for wait_attempt in range(30):
            try:
                conn = psycopg2.connect(**DB_PARAMS)
                cursor = conn.cursor()
                cursor.execute("SELECT COUNT(*) FROM price_history")
                count = cursor.fetchone()[0]
                conn.close()
                if count >= 200:
                    logger.info(f"DB pronto com {count} registros de price_history")
                    break
                logger.info(f"DB tem {count} registros. Aguardando ingester... ({wait_attempt + 1}/30)")
            except Exception:
                pass
            await asyncio.sleep(10)

        logger.info("Carregando hist\u00f3rico do DB para treino...")
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()
            db_data = {}
            for sym in IBKR_SYMBOLS:
                cursor.execute(
                    'SELECT date as "Date", open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" '
                    "FROM price_history WHERE symbol=%s ORDER BY date ASC", (sym,)
                )
                rows = cursor.fetchall()
                if rows:
                    cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
                    df = pd.DataFrame(rows, columns=cols)
                    df.set_index("Date", inplace=True)
                    db_data[sym] = df
                    logger.info(f"\U0001f4ca {sym}: {len(df)} registros do DB")
            conn.close()
            if db_data:
                forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS, data_map=db_data)
            else:
                logger.warning("DB vazio, treinando via yfinance...")
                forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS)
        except Exception as e:
            logger.error(f"Falha ao carregar dados do DB para treino: {e}")
            forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS)

    logger.info(f"AI Brain v2.0 online | capital_base=${initial_capital:,.2f} | mode={trading_mode}")
    await _notify_telegram("thoughts", f"\U0001f9e0 AI Brain v2.0 | Capital: ${initial_capital:,.2f} | Mode: {trading_mode}")

    last_retrain_day = None
    was_market_open = None

    while True:
        try:
            market_open = is_market_open()
            ny_time = _get_ny_time()
            br_time = _get_br_time()
            now = datetime.now(timezone.utc)

            if was_market_open is True and not market_open:
                logger.info("Mercado FECHOU")
                await _notify_telegram("logs",
                    f"\U0001f534 <b>Mercado FECHOU</b>\n"
                    f"\U0001f552 {br_time.strftime('%d/%m/%Y %H:%M BRT')} (NYSE {ny_time.strftime('%H:%M ET')})\n"
                    f"\U0001f4ca Pr\u00f3xima abertura: 09:30 ET (10:30 BRT)\n"
                    f"\U0001f634 Brain em modo espera"
                )

            if (was_market_open is False or was_market_open is None) and market_open:
                logger.info("Mercado ABRIU")
                await _notify_telegram("logs",
                    f"\U0001f7e2 <b>Mercado ABRIU</b>\n"
                    f"\U0001f552 {br_time.strftime('%d/%m/%Y %H:%M BRT')} (NYSE {ny_time.strftime('%H:%M ET')})\n"
                    f"\U0001f4b0 Capital: <code>${initial_capital:,.2f}</code> | Mode: {trading_mode}\n"
                    f"\U0001f9e0 Brain ativo \u2014 monitorando {len(IBKR_SYMBOLS)} ativos"
                )

            was_market_open = market_open

            if not market_open:
                await asyncio.sleep(SLEEP_OUTSIDE_MARKET)
                continue

            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()

            if last_retrain_day != now.date() and now.hour >= 6:
                logger.info("Retreinando RandomForest com dados atualizados...")
                db_data = {}
                for sym in IBKR_SYMBOLS:
                    cursor.execute(
                        'SELECT date as "Date", open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" '
                        "FROM price_history WHERE symbol=%s ORDER BY date ASC", (sym,)
                    )
                    rows = cursor.fetchall()
                    if rows:
                        df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                        df.set_index("Date", inplace=True)
                        db_data[sym] = df
                forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS, data_map=db_data)
                last_retrain_day = now.date()

            sentiment_score = sentiment.analyze(SENTIMENT_QUERY)
            is_defensive = sentiment.is_defensive(sentiment_score)

            thoughts_batch = []
            executed_trades = []

            for symbol in IBKR_SYMBOLS:
                features = _fetch_latest_features(cursor, symbol)
                if features is None:
                    continue

                rf_decision = forest.predict(features)
                ppo_decision = ppo.get_action(features)

                _save_prediction(cursor, conn, symbol, rf_decision["signal"], rf_decision["confidence"], "forest")
                _save_prediction(cursor, conn, symbol, ppo_decision["action"], ppo_decision["confidence"], "ppo")

                consensus = rf_decision["signal"] == ppo_decision["action"]
                strong = (
                    consensus
                    and ppo_decision["confidence"] >= PPO_CONFIDENCE_MIN
                    and rf_decision["confidence"] >= 0.55
                    and ppo_decision["action"] in ("BUY", "SELL")
                )

                score = _compute_score(rf_decision, ppo_decision, sentiment_score)
                desired_action = ppo_decision["action"]
                final_action = "HOLD"

                if desired_action == "SELL":
                    position = _get_position(cursor, symbol)
                    if position <= 0:
                        final_action = "HOLD (sem posicao)"
                        thought = _build_thought(
                            symbol, features, sentiment_score, rf_decision, ppo_decision, final_action, market_open, score
                        )
                        _save_thought(cursor, conn, symbol, thought, features, sentiment_score, rf_decision, ppo_decision, final_action)
                        thoughts_batch.append(thought)
                        _log_sentiment(cursor, conn, symbol, sentiment_score)
                        continue

                if strong and not is_defensive and market_open:
                    final_action = desired_action

                    cursor.execute(
                        "SELECT bid, ask FROM market_data WHERE symbol=%s AND region='US' ORDER BY time DESC LIMIT 1",
                        (symbol,)
                    )
                    price_row = cursor.fetchone()
                    if price_row and price_row[0] and price_row[1]:
                        mid_price = (price_row[0] + price_row[1]) / 2.0
                        use_fractional = os.environ.get("USE_FRACTIONAL_SHARES", "false").lower() == "true"
                        risk_pct = float(os.environ.get("RISK_PCT_PER_TRADE", "0.02"))
                        equity = initial_capital

                        if desired_action == "BUY":
                            quantity = (equity * risk_pct) / mid_price if use_fractional else max(1, int((equity * risk_pct) / mid_price))
                        else:
                            position = _get_position(cursor, symbol)
                            quantity = min(position, (equity * risk_pct) / mid_price) if use_fractional else min(position, max(1, int((equity * risk_pct) / mid_price)))
                            quantity = max(0.0001, quantity)

                        _log_trade(cursor, conn, symbol, final_action, quantity, mid_price, trading_mode)
                        await _send_order(symbol, final_action, quantity, use_fractional, equity)

                        executed_trades.append({
                            "symbol": symbol,
                            "action": final_action,
                            "quantity": quantity,
                            "price": mid_price,
                            "score": score,
                        })
                        final_action = f"\u2705 {final_action}"
                elif strong and not is_defensive and not market_open:
                    final_action = f"SINAL {desired_action}"
                elif strong and is_defensive:
                    final_action = "HOLD (defensivo)"

                thought = _build_thought(
                    symbol, features, sentiment_score, rf_decision, ppo_decision, final_action, market_open, score
                )
                _save_thought(cursor, conn, symbol, thought, features, sentiment_score, rf_decision, ppo_decision, final_action)
                thoughts_batch.append(thought)

                _log_sentiment(cursor, conn, symbol, sentiment_score)

            _update_system_state(cursor, conn, "NORMAL", 0.0, 0.0, initial_capital)

            if thoughts_batch:
                br_str = br_time.strftime("%d/%m/%Y %H:%M BRT")
                ny_str = ny_time.strftime("%H:%M ET")
                market_em = "\U0001f7e2" if market_open else "\U0001f534"
                market_tag = "ABERTO" if market_open else "FECHADO"

                if sentiment_score >= 0.6:
                    sent_tag = "\U0001f7e2 Otimista"
                elif sentiment_score >= 0.5:
                    sent_tag = "\U0001f535 Neutro"
                elif sentiment_score >= 0.4:
                    sent_tag = "\U0001f7e1 Cauteloso"
                else:
                    sent_tag = "\U0001f534 Defensivo"

                sep_thick = "\u2501" * 38
                sep_thin = "\u2500" * 38

                lines = [
                    f"\U0001f9e0 <b>OMNI-TRADER \u2014 An\u00e1lise IA</b>",
                    f"\U0001f552 {br_str} (NYSE {ny_str}) \u2502 {market_em} {market_tag}",
                    f"\U0001f30d Sent: <b>{sentiment_score:.2f}</b> {sent_tag} \u2502 \U0001f4cb {len(thoughts_batch)} ativos",
                    sep_thick,
                    f"\U0001f4dd  ATIVO  \u2502  RSI  \u2502MACD\u2502\u25b2\u25bc\u2502  RF  \u2502  PPO \u2502 A\u00c7\u00c3O",
                    sep_thin,
                ]
                for t in thoughts_batch:
                    lines.append(t)
                lines.append(sep_thick)

                if executed_trades:
                    lines.append(f"\n\U0001f4b0 <b>TRADES EXECUTADOS ({len(executed_trades)})</b>")
                    for et in executed_trades:
                        lines.append(
                            f"  {_signal_emoji(et['action'])} <b>{et['action']} {et['symbol']}</b> "
                            f"Qty:<code>{et['quantity']:.4f}</code> @<code>${et['price']:.2f}</code>"
                        )
                else:
                    lines.append(f"\n\U0001f7e1 Nenhum trade neste ciclo")

                lines.append(f"\n\U0001f916 <i>Omni-Trader v2.0 \u2502 {trading_mode}</i>")

                full_msg = "\n".join(lines)
                await _notify_telegram("thoughts", full_msg)

                for et in executed_trades:
                    invest_msg = (
                        f"\U0001f4c8 <b>Nova Opera\u00e7\u00e3o</b>\n\n"
                        f"{_signal_emoji(et['action'])} <b>{et['action']} {et['symbol']}</b>\n"
                        f"\U0001f4b5 Quantidade: <code>{et['quantity']:.4f}</code>\n"
                        f"\U0001f4b2 Pre\u00e7o: <code>${et['price']:.2f}</code>\n"
                        f"\U0001f3af Score IA: <code>{et['score']:.3f}</code>\n"
                        f"\U0001f30d Sentimento: <code>{sentiment_score:.2f}</code>\n"
                        f"\U0001f4bc Capital: <code>${initial_capital:,.2f}</code>\n"
                        f"\U0001f552 {br_str} (NYSE {ny_str})"
                    )
                    await _notify_telegram("invest", invest_msg)

            conn.close()

        except Exception as e:
            logger.error(f"AI Brain loop error: {e}")

        await asyncio.sleep(LOOP_INTERVAL)


def _log_sentiment(cursor, conn, symbol: str, score: float):
    try:
        cursor.execute(
            "INSERT INTO sentiment_scores (symbol, score) VALUES (%s, %s)",
            (symbol, score),
        )
        conn.commit()
    except Exception:
        conn.rollback()


if __name__ == "__main__":
    asyncio.run(main())

