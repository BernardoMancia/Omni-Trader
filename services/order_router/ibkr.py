import os
import asyncio
import logging
import random
from ib_insync import IB, Stock, Order, MarketOrder, util
from services.shared.risk import RiskManager, MarketState, MAX_SINGLE_POSITION_PCT

logger = logging.getLogger("IBKRRouter")

IB_HOST = os.environ.get("IB_HOST", "ibgateway")
IB_PORT = int(os.environ.get("IB_PORT", "4004"))
IBKR_ACCOUNT_ID = os.environ.get("IBKR_ACCOUNT_ID", "")
IBKR_COMMISSION_PER_SHARE = float(os.environ.get("IBKR_COMMISSION_PER_SHARE", "0.005"))
IBKR_COMMISSION_MIN = float(os.environ.get("IBKR_COMMISSION_MIN", "1.0"))

try:
    import exchange_calendars as xcals
    import pandas as pd
    NYSE_CAL = xcals.get_calendar("XNYS")
    _XCALS_OK = True
except Exception:
    NYSE_CAL = None
    _XCALS_OK = False


def _is_market_open() -> bool:
    if not _XCALS_OK or NYSE_CAL is None:
        return True
    now = pd.Timestamp.now(tz="America/New_York")
    return NYSE_CAL.is_open_on_minute(now)


class IBKRRouter:
    def __init__(self, risk_manager: RiskManager):
        self.risk = risk_manager
        self.ib = IB()

    async def connect(self):
        util.patchAsyncio()
        backoff = 5
        while True:
            try:
                client_id = random.randint(10000, 19999)
                await self.ib.connectAsync(IB_HOST, IB_PORT, clientId=client_id, timeout=30)
                logger.info(f"IBKR conectado em {IB_HOST}:{IB_PORT} (clientId={client_id})")
                backoff = 5
                break
            except Exception as e:
                logger.error(f"IBKR connect falhou: {e}. Retry em {backoff}s...")
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60)

    def _estimate_fee(self, quantity: float, price: float) -> float:
        fee = max(quantity * IBKR_COMMISSION_PER_SHARE, IBKR_COMMISSION_MIN)
        return round(fee, 4)

    def _get_equity(self) -> float:
        if not self.ib.isConnected():
            return self.risk.current_balance
        try:
            vals = self.ib.accountValues(IBKR_ACCOUNT_ID)
            for v in vals:
                if v.tag == "NetLiquidation" and v.currency == "USD":
                    return float(v.value)
        except Exception:
            pass
        return self.risk.current_balance

    async def execute_order(
        self,
        symbol: str,
        side: str,
        quantity: float,
        use_fractional: bool = False,
        equity: float | None = None,
    ) -> dict:
        if not _is_market_open():
            logger.info(f"Mercado fechado. Ordem {side} {symbol} adiada para SHADOW.")
            return await self._shadow_trade(symbol, quantity, side)

        if self.risk.state == MarketState.RED:
            return await self._shadow_trade(symbol, quantity, side)

        if side == "BUY" and not self.risk.is_buy_allowed():
            logger.warning(f"BUY bloqueado por RiskManager (state={self.risk.state.name})")
            return {"status": "blocked", "reason": self.risk.state.name}

        if side == "SELL" and not self.risk.is_sell_allowed():
            logger.warning(f"SELL bloqueado por RiskManager (state={self.risk.state.name})")
            return {"status": "blocked", "reason": self.risk.state.name}

        real_equity = equity or self._get_equity()
        self.risk.update_state(real_equity)

        try:
            contract = Stock(symbol, "SMART", "USD")
            await self.ib.qualifyContractsAsync(contract)
            ticker = self.ib.reqMktData(contract, "", True, False)
            await asyncio.sleep(1)

            mid_price = 0.0
            if ticker.bid and ticker.ask:
                mid_price = (ticker.bid + ticker.ask) / 2.0
            elif ticker.last:
                mid_price = ticker.last

            if mid_price <= 0:
                logger.warning(f"Preço não disponível para {symbol}, abortando ordem.")
                return {"status": "error", "reason": "no_price"}

            max_position_value = real_equity * MAX_SINGLE_POSITION_PCT
            if use_fractional:
                stake = min(self.risk.get_risk_amount(), max_position_value)
            else:
                qty_calc = self.risk.get_position_size(mid_price)
                quantity = max(1, int(qty_calc))
                if quantity * mid_price > max_position_value:
                    quantity = max(1, int(max_position_value / mid_price))
                stake = quantity * mid_price

            estimated_profit_usd = stake * 0.015
            fee = self._estimate_fee(quantity if not use_fractional else stake / mid_price, mid_price)

            if not self.risk.validate_fee_viability(estimated_profit_usd, fee):
                logger.warning(f"Trade {symbol} abortado: lucro < taxa×4.")
                return {"status": "aborted", "reason": "fee_not_viable", "fee": fee}

            if use_fractional:
                order = Order()
                order.action = side.upper()
                order.orderType = "MKT"
                order.totalQuantity = 0
                order.cashQty = stake
                order.tif = "DAY"
            else:
                order = MarketOrder(side.upper(), quantity)

            trade = self.ib.placeOrder(contract, order)
            logger.info(f"IBKR Ordem: {side} {quantity if not use_fractional else f'${stake:.2f}'} {symbol} (fee~${fee:.2f})")
            return {"status": "submitted", "orderId": trade.order.orderId, "symbol": symbol}

        except Exception as e:
            logger.error(f"Erro ao executar ordem IBKR {symbol}: {e}")
            return {"status": "error", "reason": str(e)}

    async def _shadow_trade(self, symbol: str, quantity: float, side: str) -> dict:
        logger.info(f"SHADOW IBKR: {side} {quantity} {symbol}")
        return {"mode": "SHADOW", "symbol": symbol, "side": side}
