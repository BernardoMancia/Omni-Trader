# Omni-Trader IBKR

> AI-powered precision trading system for Interactive Brokers (IBKR) — scalable for any account size.

---

## 🇺🇸 English

### Architecture

| Service | Role |
|---|---|
| `data_ingester` | Live ticks from IBKR + Binance WebSocket, OHLCV history via yfinance |
| `ai_brain` | RandomForest (5yr history) + PPO + Sentiment analysis (VADER + NewsAPI) |
| `order_router` | FastAPI SOR: validates fees, manages position sizing, executes IBKR orders |
| `notifier` | Telegram alerts (entries, exits, trend changes) + reports at 10:00 & 20:00 |
| `watchdog` | Dead-man switch: silence alert if no data for 10+ minutes |

### Risk Engine

- **Proportional sizing**: position = `equity × risk_pct` (default 2%)
- **Fee viability filter**: trade aborted if `estimated_profit < brokerage_fee × 4`
- **Fractional shares**: set `USE_FRACTIONAL_SHARES=true` (for ETFs like VOO, QQQ)
- **3 risk states**: `NORMAL → DEFENSIVE (sentiment < 0.4) → RED (drawdown > 50%)`

### AI System

- **RandomForest**: trained on 5 years OHLCV (RSI, MACD, Bollinger, EMA, ATR, Volume Ratio). Cached on disk.
- **Sentiment**: VADER on NewsAPI headlines, 30min cache. Score < 0.4 → defensive mode (sell/hold only)
- **PPO**: reinforcement learning agent (hold/buy/sell). Order sent only when RF + PPO agree.

---

### 🖥️ VPS Production Setup (Hostinger Ubuntu 22.04)

**IBKR Live API — Security Configuration**

```bash
# 1. In IB Gateway / TWS: Settings → API → Enable ActiveX and Socket Clients
# Trusted IP: your VPS IP (82.112.245.99)
# Socket port: 24004
# Read-only: NO
# Account: U12345678 (your account ID)
```

**Firewall ports required:**

| Port | Service |
|---|---|
| 24004 | IBKR Gateway API |
| 25900 | IBKR VNC (monitoring) |
| 28000 | Order Router internal |

**Running setup.sh:**

```bash
# Copy project to VPS
git clone https://github.com/your-user/omni-trader /opt/omni-trader

# Run setup (root required)
chmod +x /opt/omni-trader/setup.sh
sudo /opt/omni-trader/setup.sh
```

**PM2 Commands:**

```bash
pm2 status          # service status
pm2 logs            # all logs
pm2 logs omni-brain # specific service
pm2 restart all     # restart all
pm2 monit           # real-time RAM/CPU monitor
```

**Key environment variables:**

```env
RISK_PCT_PER_TRADE=0.02        # 2% equity risk per trade
USE_FRACTIONAL_SHARES=false    # true for ETFs like VOO/QQQ
IBKR_ACCOUNT_ID=U12345678      # your live account ID
NEWS_API_KEY=xxx               # newsapi.org (free tier ok)
IB_TRADING_MODE=live           # change from paper to live
```

---

### 🐳 Local Development (Docker)

```bash
cp .env.example .env
# Edit .env with your credentials
docker compose up -d
docker compose logs -f brain
```

---

## 🇧🇷 Português

### Arquitetura

| Serviço | Função |
|---|---|
| `data_ingester` | Ticks ao vivo IBKR + WebSocket Binance, histórico OHLCV via yfinance |
| `ai_brain` | RandomForest (5 anos) + PPO + Análise de sentimento (VADER + NewsAPI) |
| `order_router` | FastAPI SOR: valida taxas, dimensiona posição, executa ordens IBKR |
| `notifier` | Alertas Telegram (entradas, saídas, tendências) + relatórios 10h e 20h |
| `watchdog` | Dead-man switch: alerta se sem dados por 10+ minutos |

### Motor de Risco

- **Dimensionamento proporcional**: posição = `equity × risk_pct` (padrão 2%)
- **Filtro de taxa**: trade abortado se `lucro_estimado < taxa_corretagem × 4`
- **Fractional Shares**: ative com `USE_FRACTIONAL_SHARES=true` (para ETFs caros como VOO, QQQ)
- **3 estados de risco**: `NORMAL → DEFENSIVO (sentimento < 0.4) → RED (drawdown > 50%)`

### Sistema de IA

- **RandomForest**: treinado em 5 anos de OHLCV (RSI, MACD, Bollinger, EMA, ATR, Volume Ratio). Modelo salvo em disco.
- **Sentimento**: VADER em manchetes da NewsAPI, cache de 30min. Score < 0.4 → modo defensivo (apenas venda/hold)
- **PPO**: agente de reinforcement learning (hold/buy/sell). Ordem enviada somente quando RF + PPO concordam.

---

### 🖥️ Setup VPS de Produção (Hostinger Ubuntu 22.04)

**Vincular API Live da IBKR — Configuração de Segurança**

```bash
# 1. No IB Gateway / TWS: Settings → API → Enable ActiveX and Socket Clients
# IP confiável: IP da sua VPS (82.112.245.99)
# Porta socket: 24004
# Somente leitura: NÃO
# Conta: U12345678 (seu ID de conta real)
```

**Portas de firewall necessárias:**

| Porta | Serviço |
|---|---|
| 24004 | IBKR Gateway API |
| 25900 | IBKR VNC (monitoramento) |
| 28000 | Order Router interno |

**Executar o setup.sh:**

```bash
# Copie o projeto para a VPS
git clone https://github.com/seu-usuario/omni-trader /opt/omni-trader

# Execute o setup (requer root)
chmod +x /opt/omni-trader/setup.sh
sudo /opt/omni-trader/setup.sh
```

**Comandos PM2:**

```bash
pm2 status          # status dos serviços
pm2 logs            # todos os logs
pm2 logs omni-brain # serviço específico
pm2 restart all     # reiniciar tudo
pm2 monit           # monitor de RAM/CPU em tempo real
```

**Principais variáveis de ambiente:**

```env
RISK_PCT_PER_TRADE=0.02        # 2% do equity por trade
USE_FRACTIONAL_SHARES=false    # true para ETFs como VOO/QQQ
IBKR_ACCOUNT_ID=U12345678      # seu ID de conta real
NEWS_API_KEY=xxx               # newsapi.org (plano gratuito ok)
IB_TRADING_MODE=live           # mude de paper para live
```

---

### 🐳 Desenvolvimento Local (Docker)

```bash
cp .env.example .env
# Edite o .env com suas credenciais
docker compose up -d
docker compose logs -f brain
```

---

### Relatórios Telegram

| Horário | Conteúdo |
|---|---|
| Boot | Capital base, streams ativos, modo de IA |
| 10:00 e 20:00 (seg-sex) | Capital Inicial, Saldo Atual, P&L $ e %, P&L do dia, P&L BRL, Drawdown Máximo |
| Alertas imediatos | Entrada/saída, modo defensivo ativado, dead-man switch |
| `/menu` | Painel de controle com HALT, RESUME, STATUS, REBOOT |
| `/status` | Snapshot de performance em tempo real |
