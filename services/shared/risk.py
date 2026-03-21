from enum import Enum
import logging

class MarketState(Enum):
    NORMAL = "NORMAL"
    RED = "RED"

class RiskManager:
    KELLY_FRACTION = 0.25
    HARD_STOP_PCT = -50.0

    def __init__(self, initial_capital: float, region: str):
        self.capital_ref = initial_capital
        self.current_balance = initial_capital
        self.region = region
        self.state = MarketState.NORMAL
        self.logger = logging.getLogger(f"RiskManager-{self.region}")

    def get_drawdown(self) -> float:
        if self.capital_ref <= 0:
            return 0.0
        return ((self.current_balance - self.capital_ref) / self.capital_ref) * 100.0

    def update_state(self, current_balance: float) -> MarketState:
        self.current_balance = current_balance
        dd = self.get_drawdown()
        old_state = self.state

        if dd <= self.HARD_STOP_PCT:
            self.state = MarketState.RED
        elif self.state != MarketState.RED:
            self.state = MarketState.NORMAL

        if old_state != self.state:
            self.logger.warning(f"[{self.region}] {old_state.name} -> {self.state.name} | DD: {dd:.2f}%")

        return self.state

    def resume(self, new_capital: float):
        self.capital_ref = new_capital
        self.current_balance = new_capital
        self.state = MarketState.NORMAL
        self.logger.info(f"[{self.region}] RECALIBRATED: new base ${self.capital_ref}")

    def calculate_position_size(self, p: float, b: float, current_balance: float) -> float:
        if self.state == MarketState.RED or b <= 0:
            return 0.0
        q = 1.0 - p
        f = (p * b - q) / b
        if f <= 0:
            return 0.0
        position_size = current_balance * (f * self.KELLY_FRACTION)
        return min(position_size, current_balance)
