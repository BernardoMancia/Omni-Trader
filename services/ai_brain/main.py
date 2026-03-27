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


def _build_thought(
    symbol: str, features: np.ndarray | None,
    sentiment_score: float, rf_decision: dict, ppo_decision: dict,
    final_action: str, market_open: bool
) -> str:
    if features is None:
        return f"{symbol}: Dados insuficientes para análise."

    rsi, macd_val, _, bb_pct, _, _, atr, vol_ratio, returns = features.tolist()

    rsi_desc = "sobrecomprado" if rsi > 70 else "sobrevendido" if rsi < 30 else "neutro"
    macd_desc = "bullish" if macd_val > 0 else "bearish"
    sent_desc = "defensivo" if sentiment_score < 0.4 else "cauteloso" if sentiment_score < 0.5 else "positivo"
    market_desc = "ABERTO" if market_open else "FECHADO"

    return (
        f"{symbol}: RSI={rsi:.1f} ({rsi_desc}), MACD {macd_desc} ({macd_val:.4f}), "
        f"BB%={bb_pct:.2f}, ATR={atr:.2f}, VolRatio={vol_ratio:.2f}, Ret={returns:.4f}. "
        f"Sentimento={sentiment_score:.2f} ({sent_desc}). "
        f"RF={rf_decision['signal']}({rf_decision['confidence']:.2f}), "
        f"PPO={ppo_decision['action']}({ppo_decision['confidence']:.2f}). "
        f"Mercado {market_desc}. Decisão: {final_action}."
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


async def main():
    ppo = PPOAgent(state_dim=9)
    forest = ForestEngine()
    sentiment = SentimentEngine()
    initial_capital = float(os.environ.get("INITIAL_CAPITAL_US", "10000"))

    if not forest.is_ready():
        logger.info("Tentando carregar histórico do DB para treino...")
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
            conn.close()
            forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS, data_map=db_data)
        except Exception as e:
            logger.error(f"Falha ao carregar dados do DB para treino: {e}")
            forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS)

    logger.info(f"AI Brain v2.0 online | capital_base=${initial_capital:,.2f}")
    await _notify_telegram("thoughts", f"🧠 AI Brain v2.0 inicializado | Capital: ${initial_capital:,.2f}")

    last_retrain_day = None

    while True:
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()

            now = datetime.now(timezone.utc)
            market_open = is_market_open()

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

                final_action = "HOLD"

                if strong and not is_defensive and market_open:
                    final_action = ppo_decision["action"]

                    cursor.execute("SELECT bid, ask FROM market_data WHERE symbol=%s AND region='US' ORDER BY time DESC LIMIT 1", (symbol,))
                    price_row = cursor.fetchone()
                    if price_row:
                        mid_price = (price_row[0] + price_row[1]) / 2.0
                        use_fractional = os.environ.get("USE_FRACTIONAL_SHARES", "false").lower() == "true"
                        risk_pct = float(os.environ.get("RISK_PCT_PER_TRADE", "0.02"))
                        equity = initial_capital
                        quantity = (equity * risk_pct) / mid_price if use_fractional else max(1, int((equity * risk_pct) / mid_price))
                        await _send_order(symbol, final_action, quantity, use_fractional, equity)
                elif strong and not market_open:
                    final_action = f"HOLD (mercado fechado, sinal era {ppo_decision['action']})"
                elif strong and is_defensive:
                    final_action = f"HOLD (defensivo, sent={sentiment_score:.2f})"

                thought = _build_thought(
                    symbol, features, sentiment_score, rf_decision, ppo_decision, final_action, market_open
                )
                _save_thought(cursor, conn, symbol, thought, features, sentiment_score, rf_decision, ppo_decision, final_action)
                thoughts_batch.append(thought)

                _log_sentiment(cursor, conn, symbol, sentiment_score)

            _update_system_state(cursor, conn, "NORMAL", 0.0, 0.0, initial_capital)

            if thoughts_batch:
                full_thoughts = "💡 <b>Análise IA</b>\n\n" + "\n\n".join(
                    [f"• {t}" for t in thoughts_batch]
                )
                market_status = "🟢 ABERTO" if market_open else "🔴 FECHADO"
                full_thoughts += f"\n\n📊 Mercado: {market_status} | Sentimento: {sentiment_score:.2f}"
                await _notify_telegram("thoughts", full_thoughts)

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
