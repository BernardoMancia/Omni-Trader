import sys
import os
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from services.shared.risk import RiskManager, MarketState


def test_proportional_position_size():
    rm = RiskManager(initial_capital=10000.0, region="US", risk_pct=0.02)
    qty = rm.get_position_size(price_per_share=200.0)
    assert 0 < qty <= 1, f"Expected ~1 share at $200 com 2% de $10k, got {qty}"

    rm2 = RiskManager(initial_capital=100000.0, region="US", risk_pct=0.02)
    qty2 = rm2.get_position_size(price_per_share=200.0)
    assert qty2 == 10, f"Expected 10 shares at $200 com 2% de $100k, got {qty2}"


def test_fractional_shares():
    rm = RiskManager(initial_capital=500.0, region="US", risk_pct=0.02, use_fractional=True)
    qty = rm.get_position_size(price_per_share=500.0)
    assert qty > 0, "Fractional deve retornar fração mesmo abaixo de 1 share"
    assert qty < 1.0, f"Com $500 e 2% = $10, deve ser 0.02 shares, got {qty}"


def test_fee_viability():
    rm = RiskManager(initial_capital=5000.0, region="US", risk_pct=0.02)
    assert rm.validate_fee_viability(20.0, 1.0) is True, "Lucro $20 > taxa $1*4: viável"
    assert rm.validate_fee_viability(3.0, 1.0) is False, "Lucro $3 < taxa $1*4: inviável"
    assert rm.validate_fee_viability(0.1, 1.0) is False, "Lucro $0.10 com saldo baixo: inviável"


def test_defensive_mode():
    rm = RiskManager(initial_capital=10000.0, region="US")
    state = rm.update_state(10000.0, sentiment_score=0.35)
    assert state == MarketState.DEFENSIVE, f"sentimento 0.35 deve → DEFENSIVE, got {state}"
    assert not rm.is_buy_allowed(), "BUY deve ser bloqueado em DEFENSIVE"
    assert rm.is_sell_allowed(), "SELL deve ser permitido em DEFENSIVE"


def test_risk_red_mode():
    rm = RiskManager(initial_capital=1000.0, region="US")
    rm.update_state(490.0)
    assert rm.state == MarketState.RED
    assert rm.get_position_size(100.0) == 0
    assert not rm.is_buy_allowed()
    assert not rm.is_sell_allowed()


def test_max_drawdown_tracking():
    rm = RiskManager(initial_capital=10000.0, region="US")
    rm.update_state(9000.0)
    rm.update_state(8500.0)
    rm.update_state(9200.0)
    assert rm.max_drawdown < -0.1, f"max_drawdown deve ser negativo, got {rm.max_drawdown}"


def test_risk_recalibration():
    rm = RiskManager(initial_capital=1000.0, region="US")
    rm.update_state(400.0)
    assert rm.state == MarketState.RED
    rm.resume(new_capital=400.0)
    assert rm.state == MarketState.NORMAL
    assert rm.capital_ref == 400.0
    assert rm.max_drawdown == 0.0


if __name__ == "__main__":
    test_proportional_position_size()
    test_fractional_shares()
    test_fee_viability()
    test_defensive_mode()
    test_risk_red_mode()
    test_max_drawdown_tracking()
    test_risk_recalibration()
    print("✅ Todos os testes passaram.")
