import os
import asyncio
import logging
from ib_insync import IB, Stock, MarketOrder
from services.shared.risk import RiskManager, MarketState

logger = logging.getLogger("IBKR-Router")

class IBKRRouter:
    def __init__(self, risk_manager: RiskManager):
        self.risk_manager = risk_manager
        self.ib = IB()
        self.host = os.environ.get("IB_HOST", "ibgateway")
        self.port = int(os.environ.get("IB_PORT", "4002"))
        self.client_id = int(os.environ.get("IB_CLIENT_ID", "1"))

    async def connect(self):
        try:
            await self.ib.connectAsync(self.host, self.port, clientId=self.client_id)
            logger.info(f"IBKR Connected to {self.host}:{self.port}")
        except Exception as e:
            logger.error(f"IBKR Connection failed: {e}")

    async def execute_order(self, symbol: str, quantity: float, side: str):
        if self.risk_manager.state == MarketState.RED:
            return {"status": "shadow", "symbol": symbol}
        
        contract = Stock(symbol, 'SMART', 'USD')
        await self.ib.qualifyContractsAsync(contract)
        order = MarketOrder(side.upper(), quantity)
        trade = self.ib.placeOrder(contract, order)
        return {"status": "submitted", "orderId": trade.order.orderId}

async def main():
    while True:
        await asyncio.sleep(3600)

if __name__ == "__main__":
    asyncio.run(main())
