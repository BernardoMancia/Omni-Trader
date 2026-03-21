import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.shared.risk import RiskManager, MarketState

def test_risk_50_limit():
    rm = RiskManager(initial_capital=1000.0, region="US")
    rm.update_state(800.0)
    assert rm.state == MarketState.NORMAL
    rm.update_state(501.0)
    assert rm.state == MarketState.NORMAL
    rm.update_state(490.0)
    assert rm.state == MarketState.RED
    rm.update_state(600.0) 
    assert rm.state == MarketState.RED

def test_risk_recalibration():
    rm = RiskManager(initial_capital=1000.0, region="US")
    rm.update_state(400.0)
    assert rm.state == MarketState.RED
    rm.resume(new_capital=400.0)
    assert rm.state == MarketState.NORMAL
    assert rm.capital_ref == 400.0
    rm.update_state(190.0)
    assert rm.state == MarketState.RED

if __name__ == "__main__":
    test_risk_50_limit()
    test_risk_recalibration()
