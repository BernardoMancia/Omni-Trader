# Omni-Trader: Multi-Region MFT Ecosystem

[Português](#português) | [English](#english)

---

## Português

### Visão Geral
Omni-Trader é um ecossistema de negociação de média frequência (MFT) distribuído globalmente, projetado para operar simultaneamente no mercado de ações dos EUA e nos mercados de criptomoedas asiáticos.

### Arquitetura Geográfica
- **Omni-EUA (us-east-1)**: Focado em ações via API Alpaca. Latência < 5ms para Equinix NY4.
- **Omni-Cripto (ap-northeast-1)**: Focado em ativos digitais via API Binance. Latência otimizada para o teatro asiático.

### Gestão de Risco
O sistema implementa o **Critério de Kelly Fracionado** e uma máquina de estados de 4 níveis baseada em drawdown:
- **Verde**: Operação normal.
- **Amarelo**: Redução de risco.
- **Laranja**: Pausa temporária (Halt).
- **Vermelho**: Modo Sombra (Paper Trading).

### Tecnologias
- **Linguagem**: Python (AI Brain, Microserviços).
- **IA**: Temporal Fusion Transformer (TFT) & Proximal Policy Optimization (PPO).
- **Infraestrutura**: AWS ECS Fargate, ECR, VPC, Secrets Manager.
- **Dados**: TimescaleDB (PostgreSQL).
- **NOC**: Telegram Bots (FastAPI Webhooks).

---

## English

### Overview
Omni-Trader is a globally distributed Mid-Frequency Trading (MFT) ecosystem designed to operate simultaneously in the US stock market and Asian cryptocurrency markets.

### Geographic Architecture
- **Omni-USA (us-east-1)**: Focused on stocks via Alpaca API. Latency < 5ms to Equinix NY4.
- **Omni-Crypto (ap-northeast-1)**: Focused on digital assets via Binance API. Latency optimized for the Asian theater.

### Risk Management
The system implements the **Fractional Kelly Criterion** and a 4-level drawdown-based state machine:
- **Green**: Normal operation.
- **Yellow**: Risk reduction.
- **Orange**: Temporary pause (Halt).
- **Red**: Shadow Mode (Paper Trading).

### Tech Stack
- **Language**: Python (AI Brain, Microservices).
- **AI**: Temporal Fusion Transformer (TFT) & Proximal Policy Optimization (PPO).
- **Infrastructure**: AWS ECS Fargate, ECR, VPC, Secrets Manager.
- **Data**: TimescaleDB (PostgreSQL).
- **NOC**: Telegram Bots (FastAPI Webhooks).
