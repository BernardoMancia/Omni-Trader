from enum import Enum
import logging


class MarketState(Enum):
    NORMAL = "NORMAL"
    DEFENSIVE = "DEFENSIVE"
    RED = "RED"


class RiskManager:
    HARD_STOP_PCT = -50.0

    def __init__(
        self,
        initial_capital: float,
        region: str,
        risk_pct: float = 0.02,
        use_fractional: bool = False,
    ):
        self.capital_ref = initial_capital
        self.current_balance = initial_capital
        self.peak_balance = initial_capital
        self.max_drawdown = 0.0
        self.region = region
        self.risk_pct = risk_pct
        self.use_fractional = use_fractional
        self.state = MarketState.NORMAL
        self.logger = logging.getLogger(f"RiskManager-{self.region}")

    def get_drawdown(self) -> float:
        if self.capital_ref <= 0:
            return 0.0
        return ((self.current_balance - self.capital_ref) / self.capital_ref) * 100.0

    def update_state(self, current_balance: float, sentiment_score: float = 1.0) -> MarketState:
        self.current_balance = current_balance
        if current_balance > self.peak_balance:
            self.peak_balance = current_balance
        session_dd = ((self.current_balance - self.peak_balance) / self.peak_balance) * 100.0
        if abs(session_dd) > abs(self.max_drawdown):
            self.max_drawdown = session_dd

        dd = self.get_drawdown()
        old_state = self.state

        if dd <= self.HARD_STOP_PCT:
            self.state = MarketState.RED
        elif sentiment_score < 0.4:
            self.state = MarketState.DEFENSIVE
        else:
            self.state = MarketState.NORMAL

        if old_state != self.state:
            self.logger.warning(
                f"[{self.region}] {old_state.name} → {self.state.name} | DD: {dd:.2f}% | Sentimento: {sentiment_score:.2f}"
            )

        return self.state

    def resume(self, new_capital: float):
        self.capital_ref = new_capital
        self.current_balance = new_capital
        self.peak_balance = new_capital
        self.max_drawdown = 0.0
        self.state = MarketState.NORMAL
        self.logger.info(f"[{self.region}] RECALIBRATED: new base ${self.capital_ref:,.2f}")

    def get_position_size(self, price_per_share: float) -> float:
        """Retorna a quantidade de ações baseada em % do equity. Suporta frações."""
        if self.state == MarketState.RED or price_per_share <= 0:
            return 0.0
        risk_amount = self.current_balance * self.risk_pct
        qty = risk_amount / price_per_share
        if not self.use_fractional:
            qty = int(qty)
        return max(qty, 0)

    def get_risk_amount(self) -> float:
        """Retorna o valor em USD que será arriscado nesta operação."""
        if self.state == MarketState.RED:
            return 0.0
        return self.current_balance * self.risk_pct

    def validate_fee_viability(self, estimated_profit_usd: float, brokerage_fee_usd: float) -> bool:
        """
        Valida se a operação é viável dado o custo de corretagem.
        Regra: Lucro estimado deve ser > (taxa * 4) para compensar bid/ask spread e slippage.
        """
        if brokerage_fee_usd <= 0:
            return True
        viable = estimated_profit_usd > (brokerage_fee_usd * 4)
        if not viable:
            self.logger.warning(
                f"[{self.region}] Trade abortado: lucro ${estimated_profit_usd:.2f} < taxa×4 (${brokerage_fee_usd * 4:.2f})"
            )
        return viable

    def is_buy_allowed(self) -> bool:
        return self.state == MarketState.NORMAL

    def is_sell_allowed(self) -> bool:
        return self.state in (MarketState.NORMAL, MarketState.DEFENSIVE)
