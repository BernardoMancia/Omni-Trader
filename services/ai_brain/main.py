import os
import asyncio
import logging
import numpy as np
import pandas as pd
import yfinance as yf
import torch
import torch.nn as nn
import psycopg2
import httpx
from services.ai_brain.forest import ForestEngine
from services.ai_brain.sentiment import SentimentEngine
from services.shared.risk import MarketState

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("AIBrain")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}
ROUTER_URL = os.environ.get("ROUTER_URL", "http://router:28000/order")
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

    def __init__(self, state_dim: int = 32, action_dim: int = 3):
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


def _fetch_market_row(cursor) -> tuple | None:
    cursor.execute(
        "SELECT symbol, bid, ask FROM market_data WHERE region='US' ORDER BY time DESC LIMIT 1"
    )
    return cursor.fetchone()


def _fetch_history_from_db(cursor, symbols: list[str]) -> dict[str, pd.DataFrame]:
    history_map = {}
    for sym in symbols:
        try:
            cursor.execute(
                "SELECT date as \"Date\", open as \"Open\", high as \"High\", low as \"Low\", close as \"Close\", volume as \"Volume\" "
                "FROM price_history WHERE symbol=%s ORDER BY date ASC",
                (sym,)
            )
            rows = cursor.fetchall()
            if rows:
                cols = ["Date", "Open", "High", "Low", "Close", "Volume"]
                df = pd.DataFrame(rows, columns=cols)
                df.set_index("Date", inplace=True)
                history_map[sym] = df
        except Exception as e:
            logger.error(f"Erro ao buscar histórico de {sym} no DB: {e}")
    return history_map


def _log_sentiment_to_db(cursor, conn, symbol: str, score: float):
    try:
        cursor.execute(
            "INSERT INTO sentiment_scores (symbol, score) VALUES (%s, %s) ON CONFLICT DO NOTHING",
            (symbol, score),
        )
        conn.commit()
    except Exception:
        conn.rollback()


def _update_system_state(cursor, conn, state_name: str, drawdown: float, capital: float):
    try:
        cursor.execute(
            "INSERT INTO system_states (region, state, drawdown, capital_ref) VALUES (%s, %s, %s, %s)",
            ("US", state_name, drawdown, capital),
        )
        conn.commit()
    except Exception:
        conn.rollback()


async def _send_order(symbol: str, side: str, quantity: float, use_fractional: bool, equity: float):
    async with httpx.AsyncClient(timeout=8.0) as client:
        payload = {
            "symbol": symbol,
            "side": side,
            "quantity": quantity,
            "region": "US",
            "use_fractional": use_fractional,
            "equity": equity,
        }
        try:
            r = await client.post(ROUTER_URL, json=payload)
            logger.info(f"Router response [{side} {symbol}]: {r.status_code} {r.text[:120]}")
        except Exception as e:
            logger.error(f"Falha ao enviar ordem ao router: {e}")


async def main():
    ppo = PPOAgent()
    forest = ForestEngine()
    sentiment = SentimentEngine()
    initial_capital = float(os.environ.get("INITIAL_CAPITAL_US", "10000"))

    if not forest.is_ready():
        logger.info("Tentando carregar histórico do banco de dados para treino...")
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()
            db_data = _fetch_history_from_db(cursor, IBKR_SYMBOLS)
            conn.close()
            
            logger.info(f"Iniciando treino com {len(db_data)} símbolos vindos do DB.")
            forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS, data_map=db_data)
        except Exception as e:
            logger.error(f"Falha ao carregar dados do DB para treino: {e}")
            logger.info("Mantendo tentativa via yfinance como fallback...")
            forest.train(symbols=IBKR_SYMBOLS, years=RF_TRAIN_YEARS)
    else:
        logger.info("RandomForest carregado do cache, pronto para operar.")

    logger.info(f"AI Brain IBKR online | capital_base=${initial_capital:,.2f}")

    while True:
        try:
            conn = psycopg2.connect(**DB_PARAMS)
            cursor = conn.cursor()

            sentiment_score = sentiment.analyze(SENTIMENT_QUERY)
            is_defensive = sentiment.is_defensive(sentiment_score)

            price_row = _fetch_market_row(cursor)

            if price_row:
                symbol, bid, ask = price_row
                mid_price = (bid + ask) / 2.0

                state_vector = np.random.rand(32).astype(np.float32)
                ppo_decision = ppo.get_action(state_vector)

                rf_9_features = state_vector[:9]
                rf_decision = forest.predict(rf_9_features)

                ppo_action = ppo_decision["action"]
                rf_signal = rf_decision["signal"]
                rf_conf = rf_decision["confidence"]
                ppo_conf = ppo_decision["confidence"]

                if is_defensive:
                    if ppo_action == "BUY" or rf_signal == "BUY":
                        logger.info(f"Modo DEFENSIVO (sent={sentiment_score:.2f}): BUY bloqueado → HOLD")
                        _update_system_state(cursor, conn, MarketState.DEFENSIVE.name, 0.0, initial_capital)
                        conn.close()
                        await asyncio.sleep(LOOP_INTERVAL)
                        continue

                consensus = ppo_action == rf_signal
                strong_signal = (
                    consensus
                    and ppo_conf >= PPO_CONFIDENCE_MIN
                    and rf_conf >= 0.55
                    and ppo_action in ("BUY", "SELL")
                )

                if strong_signal:
                    logger.info(
                        f"SINAL [{ppo_action}] {symbol} | PPO={ppo_conf:.2f} RF={rf_conf:.2f} Sent={sentiment_score:.2f}"
                    )
                    use_fractional = os.environ.get("USE_FRACTIONAL_SHARES", "false").lower() == "true"
                    risk_pct = float(os.environ.get("RISK_PCT_PER_TRADE", "0.02"))
                    equity = initial_capital
                    quantity = (equity * risk_pct) / mid_price if use_fractional else max(1, int((equity * risk_pct) / mid_price))
                    await _send_order(symbol, ppo_action, quantity, use_fractional, equity)

                _log_sentiment_to_db(cursor, conn, symbol, sentiment_score)

            conn.close()

        except Exception as e:
            logger.error(f"AI Brain loop error: {e}")

        await asyncio.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
