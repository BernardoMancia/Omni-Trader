import os
import asyncio
import logging
import psycopg2
import uvicorn
from fastapi import FastAPI
from pydantic import BaseModel
from services.shared.risk import RiskManager, MarketState
from services.order_router.ibkr import IBKRRouter

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger("SmartOrderRouter")

DB_PARAMS = {
    "host": os.environ["DB_HOST"], "port": os.environ["DB_PORT"],
    "dbname": os.environ["DB_NAME"], "user": os.environ["DB_USER"],
    "password": os.environ["DB_PASSWORD"],
}

app = FastAPI(title="Omni-Trader Smart Order Router")
ibkr_router: IBKRRouter | None = None
risk_us: RiskManager | None = None


class OrderRequest(BaseModel):
    symbol: str
    side: str
    quantity: float = 1.0
    region: str = "US"
    use_fractional: bool = False
    equity: float | None = None


def _log_trade(symbol: str, side: str, quantity: float, mode: str, region: str, price: float = 0.0):
    try:
        conn = psycopg2.connect(**DB_PARAMS)
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO trade_logs (symbol, side, quantity, price, mode, region) VALUES (%s, %s, %s, %s, %s, %s)",
            (symbol, side, quantity, price, mode, region),
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Erro ao logar trade: {e}")


@app.on_event("startup")
async def startup_event():
    global ibkr_router, risk_us
    initial_capital = float(os.environ.get("INITIAL_CAPITAL_US", "10000"))
    risk_pct = float(os.environ.get("RISK_PCT_PER_TRADE", "0.02"))
    use_fractional = os.environ.get("USE_FRACTIONAL_SHARES", "false").lower() == "true"

    risk_us = RiskManager(
        initial_capital=initial_capital,
        region="US",
        risk_pct=risk_pct,
        use_fractional=use_fractional,
    )
    ibkr_router = IBKRRouter(risk_manager=risk_us)
    await ibkr_router.connect()
    logger.info(f"SOR online | IBKR pronto | capital=${initial_capital:,.2f} | risco={risk_pct*100:.1f}%")


@app.post("/order")
async def place_order(order: OrderRequest):
    if order.region == "US":
        result = await ibkr_router.execute_order(
            symbol=order.symbol,
            side=order.side,
            quantity=order.quantity,
            use_fractional=order.use_fractional,
            equity=order.equity,
        )
        if result.get("status") == "submitted":
            _log_trade(order.symbol, order.side, order.quantity, "REAL", "US")
        elif result.get("mode") == "SHADOW":
            _log_trade(order.symbol, order.side, order.quantity, "SHADOW", "US")
        return result
    return {"error": f"Região não suportada: {order.region}"}


@app.get("/health")
async def health():
    state = risk_us.state.name if risk_us else "UNKNOWN"
    balance = risk_us.current_balance if risk_us else 0
    dd = risk_us.max_drawdown if risk_us else 0
    return {
        "status": "ok",
        "risk_state": state,
        "balance": balance,
        "max_drawdown_pct": round(dd, 2),
    }


@app.get("/risk")
async def risk_snapshot():
    if not risk_us:
        return {"error": "não iniciado"}
    return {
        "state": risk_us.state.name,
        "current_balance": risk_us.current_balance,
        "capital_ref": risk_us.capital_ref,
        "drawdown_pct": round(risk_us.get_drawdown(), 2),
        "max_drawdown_pct": round(risk_us.max_drawdown, 2),
        "risk_pct": risk_us.risk_pct,
        "use_fractional": risk_us.use_fractional,
    }


def main():
    uvicorn.run(app, host="0.0.0.0", port=28000)


if __name__ == "__main__":
    main()
