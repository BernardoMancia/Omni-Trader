import sys
import os

# Adicionar o caminho do projeto ao sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from services.shared.risk import RiskManager, MarketState

def test_risk_50_limit():
    rm = RiskManager(initial_capital=1000.0, region="US")
    
    # Teste Normal
    rm.update_state(800.0) # -20%
    assert rm.state == MarketState.NORMAL
    
    # Teste Bate na Trave
    rm.update_state(501.0) # -49.9%
    assert rm.state == MarketState.NORMAL
    
    # Teste Falência / Hard Stop
    rm.update_state(490.0) # -51%
    assert rm.state == MarketState.RED
    
    # Teste de Subida sem Recalibragem (O Bot NÃO sai do RED sozinho se ganhar shadow mode trade)
    rm.update_state(600.0) 
    assert rm.state == MarketState.RED
    
    print("✅ Teste 50% Hard Stop e Bloqueio: PASSOU")

def test_risk_recalibration():
    rm = RiskManager(initial_capital=1000.0, region="US")
    rm.update_state(400.0) # -60% = RED
    assert rm.state == MarketState.RED
    
    # Usuário enviou /resume no Telegram e o saldo lido da corretora foi 400
    rm.resume(new_capital=400.0)
    assert rm.state == MarketState.NORMAL
    assert rm.capital_ref == 400.0
    
    # Se perder metade dos 400 (bater em 200), trava de novo
    rm.update_state(190.0) # -> -52.5% em relação ao novo capital_ref (400)
    assert rm.state == MarketState.RED

    print("✅ Teste de Recalibragem de Capital (Resume): PASSOU")

if __name__ == "__main__":
    test_risk_50_limit()
    test_risk_recalibration()
