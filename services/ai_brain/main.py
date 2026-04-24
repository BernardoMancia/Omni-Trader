import os
import asyncio
import logging
import numpy as np
import pandas as pd
import torch
import torch.nn as nn
import psycopg2
import httpx
import pytz
from datetime import datetime, timezone
from services.ai_brain.forest import ForestEngine
from services.ai_brain.sentiment import SentimentEngine
from services.shared.risk import MarketState

try:
    import exchange_calendars as xcals
    _XCALS_OK = True
except Exception:
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
LOOP_INTERVAL = int(os.environ.get("BRAIN_LOOP_INTERVAL", "15"))
PPO_CONFIDENCE_MIN = float(os.environ.get("PPO_CONFIDENCE_MIN", "0.70"))
SLEEP_OUTSIDE_MARKET = int(os.environ.get("SLEEP_OUTSIDE_MARKET", "300"))
DEFENSIVE_THRESHOLD = float(os.environ.get("DEFENSIVE_THRESHOLD", "0.3"))
RF_TRAIN_YEARS = int(os.environ.get("RF_TRAIN_YEARS", "5"))
TRADING_MODE = os.environ.get("IB_TRADING_MODE", "paper").upper()


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


async def _notify_telegram(topic: str, text: str):
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.post(NOTIFIER_URL, json={"topic": topic, "text": text})
    except Exception:
        pass


async def _send_order(symbol: str, side: str, quantity: float, use_fractional: bool, equity: float, region: str = "US"):
    async with httpx.AsyncClient(timeout=8.0) as client:
        payload = {
            "symbol": symbol, "side": side, "quantity": quantity,
            "region": region, "use_fractional": use_fractional, "equity": equity,
        }
        try:
            r = await client.post(ROUTER_URL, json=payload)
            logger.info(f"Router [{region}] [{side} {symbol}]: {r.status_code}")
        except Exception as e:
            logger.error(f"Falha ao enviar ordem ao router: {e}")


def _compute_score(rf_decision: dict, ppo_decision: dict, sentiment_score: float) -> float:
    rf_conf = rf_decision["confidence"]
    ppo_conf = ppo_decision["confidence"]
    consensus_bonus = 0.15 if rf_decision["signal"] == ppo_decision["action"] else 0.0
    return (rf_conf * 0.7) + (ppo_conf * 0.2) + consensus_bonus + (sentiment_score * 0.1)


class MarketEngine:
    def __init__(self, region: str, symbols: list, calendar_code: str,
                 tz_name: str, currency: str, capital: float,
                 sentiment_query: str, topic_thoughts: str, topic_invest: str):
        self.region = region
        self.symbols = symbols
        self.calendar_code = calendar_code
        self.tz = pytz.timezone(tz_name)
        self.tz_name = tz_name
        self.currency = currency
        self.initial_capital = capital
        self.capital = capital
        self.sentiment_query = sentiment_query
        self.topic_thoughts = topic_thoughts
        self.topic_invest = topic_invest

        risk_pct = float(os.environ.get("RISK_PCT_PER_TRADE", "0.02"))
        use_frac = os.environ.get("USE_FRACTIONAL_SHARES", "false").lower() == "true"
        self.risk = RiskManager(initial_capital=capital, region=region, risk_pct=risk_pct, use_fractional=use_frac)
        self.forest = ForestEngine(model_prefix=region.lower())
        self.ppo = PPOAgent(state_dim=9)
        self.sentiment = SentimentEngine()

        self.calendar = None
        if _XCALS_OK:
            try:
                self.calendar = xcals.get_calendar(calendar_code)
            except Exception:
                logger.warning(f"Calendario {calendar_code} nao encontrado")

        self.was_market_open = None
        self.last_retrain_day = None
        self._tag = f"[{region}]"

    def is_market_open(self) -> bool:
        if self.calendar is None:
            return True
        now = pd.Timestamp.now(tz=self.tz_name)
        return self.calendar.is_open_on_minute(now)

    def _local_time(self) -> datetime:
        return datetime.now(self.tz)

    def _br_time(self) -> datetime:
        return datetime.now(pytz.timezone("America/Sao_Paulo"))

    def _build_thought(self, symbol, features, sentiment_score, rf, ppo, final_action, score):
        if features is None:
            return f"\u26a0\ufe0f <code>{symbol:>8}</code> \u2502 Dados insuficientes"
        rsi = features[0]
        macd_val = features[1]
        ema_20 = features[4]
        ema_50 = features[5]
        fa_clean = final_action.split(" ")[0] if isinstance(final_action, str) else final_action
        fa_em = _signal_emoji(fa_clean)
        trend = "\u25b2" if ema_20 > ema_50 else "\u25bc"
        macd_dir = "+" if macd_val > 0 else "-"
        rf_s = rf["signal"][0]
        ppo_s = ppo["action"][0]
        return (
            f"{fa_em} <code>{symbol:>8}</code> \u2502 "
            f"RSI <code>{rsi:5.1f}</code> \u2502 "
            f"MACD {macd_dir} \u2502 {trend} \u2502 "
            f"RF:{rf_s} <code>{rf['confidence']:.0%}</code> \u2502 "
            f"PPO:{ppo_s} <code>{ppo['confidence']:.0%}</code> \u2502 "
            f"<b>{fa_clean}</b>"
        )

    def _save_thought(self, cursor, conn, symbol, thought, features, sentiment, rf, ppo, final_action):
        try:
            rsi = float(features[0]) if features is not None else None
            macd = float(features[1]) if features is not None else None
            cursor.execute(
                "INSERT INTO ai_thoughts (symbol, thought, rsi, macd, sentiment, rf_signal, rf_conf, ppo_action, ppo_conf, final_action, region) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                (symbol, thought, rsi, macd, sentiment, rf["signal"], rf["confidence"], ppo["action"], ppo["confidence"], final_action, self.region)
            )
            conn.commit()
        except Exception:
            conn.rollback()

    def _save_prediction(self, cursor, conn, symbol, direction, confidence, source):
        try:
            cursor.execute(
                "INSERT INTO predictions (symbol, direction, confidence, horizon_days, source, region) VALUES (%s, %s, %s, %s, %s, %s)",
                (symbol, direction, confidence, 1, source, self.region)
            )
            conn.commit()
        except Exception:
            conn.rollback()

    def _get_position(self, cursor, symbol) -> float:
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

    def _log_trade(self, cursor, conn, symbol, side, quantity, price):
        try:
            cursor.execute(
                "INSERT INTO trade_logs (symbol, side, quantity, price, mode, region, is_deleted) "
                "VALUES (%s, %s, %s, %s, %s, %s, FALSE)",
                (symbol, side, quantity, price, TRADING_MODE, self.region)
            )
            conn.commit()
            logger.info(f"{self._tag} Trade: {side} {quantity:.4f} {symbol} @ {self.currency}{price:.2f}")
        except Exception as e:
            conn.rollback()
            logger.error(f"{self._tag} Erro ao registrar trade: {e}")

    def _log_sentiment(self, cursor, conn, symbol, score):
        try:
            cursor.execute("INSERT INTO sentiment_scores (symbol, score, region) VALUES (%s, %s, %s)", (symbol, score, self.region))
            conn.commit()
        except Exception:
            conn.rollback()

    def _sync_capital(self, cursor):
        try:
            cursor.execute(
                "SELECT COALESCE(SUM(CASE WHEN side='BUY' THEN -quantity*COALESCE(price,0) ELSE quantity*COALESCE(price,0) END), 0) "
                "FROM trade_logs WHERE region=%s AND is_deleted=FALSE",
                (self.region,)
            )
            pnl = float(cursor.fetchone()[0] or 0)
            self.capital = self.initial_capital + pnl
            self.risk.update_state(self.capital)
        except Exception as e:
            logger.warning(f"{self._tag} Erro ao sincronizar capital: {e}")

    def _update_system_state(self, cursor, conn):
        try:
            cursor.execute(
                "INSERT INTO system_states (region, state, drawdown, max_drawdown, capital_ref) VALUES (%s, %s, %s, %s, %s)",
                (self.region, self.risk.state.name, self.risk.get_drawdown(), self.risk.max_drawdown, self.capital)
            )
            conn.commit()
        except Exception:
            conn.rollback()

    async def _initial_train(self):
        if self.forest.is_ready():
            return
        logger.info(f"{self._tag} Aguardando dados para treino...")
        for _ in range(30):
            try:
                conn = psycopg2.connect(**DB_PARAMS)
                cur = conn.cursor()
                sym_list = ",".join([f"'{s}'" for s in self.symbols])
                cur.execute(f"SELECT COUNT(*) FROM price_history WHERE symbol IN ({sym_list})")
                count = cur.fetchone()[0]
                conn.close()
                if count >= 100:
                    break
            except Exception:
                pass
            await asyncio.sleep(10)

        try:
            conn = psycopg2.connect(**DB_PARAMS)
            cur = conn.cursor()
            db_data = {}
            for sym in self.symbols:
                cur.execute(
                    'SELECT date as "Date", open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" '
                    "FROM price_history WHERE symbol=%s ORDER BY date ASC", (sym,)
                )
                rows = cur.fetchall()
                if rows:
                    df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                    df.set_index("Date", inplace=True)
                    db_data[sym] = df
                    logger.info(f"{self._tag} {sym}: {len(df)} registros do DB")
            conn.close()
            if db_data:
                self.forest.train(symbols=self.symbols, years=RF_TRAIN_YEARS, data_map=db_data)
            else:
                self.forest.train(symbols=self.symbols, years=RF_TRAIN_YEARS)
        except Exception as e:
            logger.error(f"{self._tag} Falha no treino inicial: {e}")
            self.forest.train(symbols=self.symbols, years=RF_TRAIN_YEARS)

    async def run_loop(self):
        logger.info(f"{self._tag} Engine online | capital={self.currency}{self.capital:,.2f} | mode={TRADING_MODE}")
        await _notify_telegram(self.topic_thoughts,
            f"\U0001f9e0 <b>Omni-Trader {self.region}</b> online\n"
            f"\U0001f4b0 {self.currency}{self.capital:,.2f} | {TRADING_MODE} | {len(self.symbols)} ativos\n"
            f"\U0001f504 Treinando modelo RF..."
        )
        await self._initial_train()
        await _notify_telegram(self.topic_thoughts,
            f"\u2705 <b>{self.region}</b> Modelo treinado | Engine pronta"
        )

        while True:
            try:
                market_open = self.is_market_open()
                local_time = self._local_time()
                br_time = self._br_time()
                now = datetime.now(timezone.utc)

                if self.was_market_open is True and not market_open:
                    logger.info(f"{self._tag} Mercado FECHOU")
                    await _notify_telegram("logs",
                        f"\U0001f534 <b>{self.region} Mercado FECHOU</b>\n"
                        f"\U0001f552 {br_time.strftime('%d/%m/%Y %H:%M BRT')} ({local_time.strftime('%H:%M')} local)\n"
                        f"\U0001f634 Engine {self.region} em espera"
                    )

                if (self.was_market_open is False or self.was_market_open is None) and market_open:
                    logger.info(f"{self._tag} Mercado ABRIU")
                    await _notify_telegram("logs",
                        f"\U0001f7e2 <b>{self.region} Mercado ABRIU</b>\n"
                        f"\U0001f552 {br_time.strftime('%d/%m/%Y %H:%M BRT')} ({local_time.strftime('%H:%M')} local)\n"
                        f"\U0001f4b0 {self.currency}{self.capital:,.2f} | {len(self.symbols)} ativos"
                    )

                self.was_market_open = market_open

                if not market_open:
                    await asyncio.sleep(SLEEP_OUTSIDE_MARKET)
                    continue

                conn = psycopg2.connect(**DB_PARAMS)
                cursor = conn.cursor()

                if self.last_retrain_day != now.date() and now.hour >= 6:
                    db_data = {}
                    for sym in self.symbols:
                        cursor.execute(
                            'SELECT date as "Date", open as "Open", high as "High", low as "Low", close as "Close", volume as "Volume" '
                            "FROM price_history WHERE symbol=%s ORDER BY date ASC", (sym,)
                        )
                        rows = cursor.fetchall()
                        if rows:
                            df = pd.DataFrame(rows, columns=["Date", "Open", "High", "Low", "Close", "Volume"])
                            df.set_index("Date", inplace=True)
                            db_data[sym] = df
                    self.forest.train(symbols=self.symbols, years=RF_TRAIN_YEARS, data_map=db_data)
                    self.last_retrain_day = now.date()

                sentiment_score = self.sentiment.analyze(self.sentiment_query)
                self.risk.update_state(self.capital, sentiment_score)

                if self.risk.state == MarketState.RED:
                    await _notify_telegram(self.topic_thoughts,
                        f"\U0001f6a8 <b>{self.region} HARD-STOP ATIVADO</b>\n"
                        f"Drawdown: {self.risk.get_drawdown():.2f}% | Shadow Mode"
                    )
                    await asyncio.sleep(SLEEP_OUTSIDE_MARKET)
                    conn.close()
                    continue

                thoughts_batch = []
                executed_trades = []

                for symbol in self.symbols:
                    features = _fetch_latest_features(cursor, symbol)
                    if features is None:
                        continue

                    rf_decision = self.forest.predict(features)
                    ppo_decision = self.ppo.get_action(features)

                    self._save_prediction(cursor, conn, symbol, rf_decision["signal"], rf_decision["confidence"], "forest")
                    self._save_prediction(cursor, conn, symbol, ppo_decision["action"], ppo_decision["confidence"], "ppo")

                    score = _compute_score(rf_decision, ppo_decision, sentiment_score)
                    desired_action = rf_decision["signal"]
                    final_action = "HOLD"

                    rf_strong = rf_decision["confidence"] >= 0.55 and desired_action in ("BUY", "SELL")

                    if desired_action == "SELL":
                        position = self._get_position(cursor, symbol)
                        if position <= 0:
                            final_action = "HOLD (sem pos)"
                            thought = self._build_thought(symbol, features, sentiment_score, rf_decision, ppo_decision, final_action, score)
                            self._save_thought(cursor, conn, symbol, thought, features, sentiment_score, rf_decision, ppo_decision, final_action)
                            thoughts_batch.append(thought)
                            self._log_sentiment(cursor, conn, symbol, sentiment_score)
                            continue

                    if rf_strong and desired_action == "BUY" and not self.risk.is_buy_allowed():
                        final_action = f"HOLD ({self.risk.state.name})"
                    elif rf_strong and desired_action == "SELL" and not self.risk.is_sell_allowed():
                        final_action = f"HOLD ({self.risk.state.name})"
                    elif rf_strong:
                        final_action = desired_action
                        cursor.execute(
                            "SELECT bid, ask FROM market_data WHERE symbol=%s AND region=%s ORDER BY time DESC LIMIT 1",
                            (symbol, self.region)
                        )
                        price_row = cursor.fetchone()
                        if price_row and price_row[0] and price_row[1]:
                            mid_price = (price_row[0] + price_row[1]) / 2.0

                            if desired_action == "BUY":
                                quantity = self.risk.get_position_size(mid_price)
                            else:
                                position = self._get_position(cursor, symbol)
                                risk_qty = self.risk.get_position_size(mid_price)
                                quantity = min(position, risk_qty) if risk_qty > 0 else position
                                quantity = max(0.0001, quantity)

                            if quantity > 0:
                                self._log_trade(cursor, conn, symbol, final_action, quantity, mid_price)
                                await _send_order(symbol, final_action, quantity, self.risk.use_fractional, self.capital, self.region)

                                executed_trades.append({
                                    "symbol": symbol, "action": final_action,
                                    "quantity": quantity, "price": mid_price, "score": score,
                                })
                                final_action = f"\u2705 {final_action}"
                            else:
                                final_action = "HOLD (qty=0)"

                    thought = self._build_thought(symbol, features, sentiment_score, rf_decision, ppo_decision, final_action, score)
                    self._save_thought(cursor, conn, symbol, thought, features, sentiment_score, rf_decision, ppo_decision, final_action)
                    thoughts_batch.append(thought)
                    self._log_sentiment(cursor, conn, symbol, sentiment_score)

                self._sync_capital(cursor)
                self._update_system_state(cursor, conn)

                if thoughts_batch:
                    br_str = br_time.strftime("%d/%m/%Y %H:%M BRT")
                    local_str = local_time.strftime("%H:%M")

                    if sentiment_score >= 0.6:
                        sent_tag = "\U0001f7e2 Otimista"
                    elif sentiment_score >= 0.5:
                        sent_tag = "\U0001f535 Neutro"
                    elif sentiment_score >= 0.4:
                        sent_tag = "\U0001f7e1 Cauteloso"
                    else:
                        sent_tag = "\U0001f534 Defensivo"

                    risk_tag = {"NORMAL": "\U0001f7e2", "CAUTION": "\U0001f7e1", "DEFENSIVE": "\U0001f7e0", "RED": "\U0001f534"}.get(self.risk.state.name, "\u26aa")
                    dd = self.risk.get_drawdown()
                    sep_thick = "\u2501" * 42
                    sep_thin = "\u2500" * 42
                    flag = "\U0001f1fa\U0001f1f8" if self.region == "US" else "\U0001f1e7\U0001f1f7"

                    lines = [
                        f"\U0001f9e0 <b>OMNI-TRADER {flag} {self.region}</b>",
                        f"\U0001f552 {br_str} ({local_str} local) \u2502 \U0001f7e2 ABERTO",
                        f"\U0001f4b0 {self.currency}{self.capital:,.2f} \u2502 {risk_tag} {self.risk.state.name} \u2502 DD: {dd:+.1f}%",
                        f"\U0001f30d Sent: <b>{sentiment_score:.2f}</b> {sent_tag} \u2502 \U0001f4cb {len(thoughts_batch)} ativos",
                        sep_thick,
                        f"\U0001f4dd    ATIVO   \u2502  RSI  \u2502MACD\u2502\u25b2\u25bc\u2502  RF  \u2502  PPO \u2502 ACAO",
                        sep_thin,
                    ]
                    for t in thoughts_batch:
                        lines.append(t)
                    lines.append(sep_thick)

                    if executed_trades:
                        lines.append(f"\n\U0001f4b0 <b>TRADES {self.region} ({len(executed_trades)})</b>")
                        for et in executed_trades:
                            lines.append(
                                f"  {_signal_emoji(et['action'])} <b>{et['action']} {et['symbol']}</b> "
                                f"Qty:<code>{et['quantity']:.4f}</code> @<code>{self.currency}{et['price']:.2f}</code>"
                            )
                    else:
                        lines.append(f"\n\U0001f7e1 Nenhum trade neste ciclo")

                    lines.append(f"\n\U0001f916 <i>Omni-Trader v4.0 {flag} {TRADING_MODE}</i>")

                    await _notify_telegram(self.topic_thoughts, "\n".join(lines))

                    for et in executed_trades:
                        invest_msg = (
                            f"\U0001f4c8 <b>Nova Operacao {flag}</b>\n\n"
                            f"{_signal_emoji(et['action'])} <b>{et['action']} {et['symbol']}</b>\n"
                            f"\U0001f4b5 Qty: <code>{et['quantity']:.4f}</code>\n"
                            f"\U0001f4b2 Preco: <code>{self.currency}{et['price']:.2f}</code>\n"
                            f"\U0001f3af Score: <code>{et['score']:.3f}</code>\n"
                            f"\U0001f30d Sent: <code>{sentiment_score:.2f}</code>\n"
                            f"\U0001f4bc Capital: <code>{self.currency}{self.capital:,.2f}</code>\n"
                            f"\U0001f552 {br_str}"
                        )
                        await _notify_telegram(self.topic_invest, invest_msg)

                conn.close()

            except Exception as e:
                logger.error(f"{self._tag} Loop error: {e}")
                try:
                    conn.close()
                except Exception:
                    pass

            await asyncio.sleep(LOOP_INTERVAL)


async def main():
    us_symbols = os.environ.get("IBKR_SYMBOLS", "AAPL,MSFT,TSLA,SPY,QQQ,VOO").split(",")
    br_symbols = os.environ.get("BR_SYMBOLS", "").split(",")
    br_symbols = [s.strip() for s in br_symbols if s.strip()]

    us_capital = float(os.environ.get("INITIAL_CAPITAL_US", "500"))
    br_capital = float(os.environ.get("INITIAL_CAPITAL_BR", "500"))

    us_engine = MarketEngine(
        region="US", symbols=us_symbols, calendar_code="XNYS",
        tz_name="America/New_York", currency="$", capital=us_capital,
        sentiment_query=os.environ.get("SENTIMENT_QUERY", "US stock market economy"),
        topic_thoughts="thoughts", topic_invest="invest",
    )

    tasks = [us_engine.run_loop()]

    if br_symbols:
        br_engine = MarketEngine(
            region="BR", symbols=br_symbols, calendar_code="BVMF",
            tz_name="America/Sao_Paulo", currency="R$", capital=br_capital,
            sentiment_query=os.environ.get("SENTIMENT_QUERY_BR", "Brazil Bovespa economia mercado"),
            topic_thoughts="brasil", topic_invest="invest",
        )
        tasks.append(br_engine.run_loop())

    logger.info(f"Omni-Trader v4.0 | US={len(us_symbols)} ativos | BR={len(br_symbols)} ativos")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    asyncio.run(main())
