# Risk Management Doctrine: Omni-Trader

Todas as decisões algorítmicas e automações devem aderir estritamente a estas regras.

## 1. Kelly Fractional Criterion
Toda alocação dimensional ($f$) deve ser calculada individualmente por região usando a métrica de capital de referência isolada:
$$f = K \cdot \left(\frac{p \cdot b - q}{b}\right)$$
Onde:
- $p$ = Probabilidade de vitória empírica.
- $q = 1 - p$.
- $b$ = Ratio recompensa/risco.
- $K$ = Modificador de risco baseado no Estado da Máquina.

## 2. 4-Level Drawdown State Machine
O sistema deve monitorar o drawdown contínuo e ajustar $K$ ou cessar operações:

| Estado | Drawdown | Modificador ($K$) | Política de Execução |
| :--- | :--- | :--- | :--- |
| **Verde** | 0% a -10% | 0.25 | Operação Normal |
| **Amarelo** | -10% a -20% | 0.10 | De-risking defensivo |
| **Laranja** | -20% a -30% | 0.00 | Halt (Pausa 24h) |
| **Vermelho** | > -30% | 0.00 | **Shadow Mode** (Paper Trading) |

## 3. Shadow Mode Fail-Safe
Em Estado Vermelho, o `Smart Order Router` (SOR) deve interceptar todas as ordens reais e roteá-las para o log local no `TimescaleDB` (Paper Trading), mantendo a telemetria ativa para análise de recuperação.

## 4. Anti-Slippage Modeling
O treinamento do agente PPO deve incorporar penalidades de taxas (Maker/Taker) e slippage específicas da exchange regional (Alpaca vs Binance).

## 5. Security & Isolation
- **No Withdrawals**: Chaves de API devem ter permissões de saque desabilitadas.
- **IP Whitelisting**: Conexões só são permitidas via NAT Gateways com IP estático regional.
