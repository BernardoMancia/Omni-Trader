# Omni-Trader 9.1

[Português](#português) | [English](#english)

---

## Português

### Visão Geral
Omni-Trader 9.1 é um ecossistema de trading quantitativo otimizado para VPS única via Docker Compose. O sistema opera simultaneamente em Ações (EUA) e Criptomoedas (ÁSIA).

### Arquitetura VPS
- **Single Node**: Orquestração via Docker Compose.
- **Banco de Dados**: TimescaleDB.
- **NOC**: Bot de Telegram unificado com tópicos.

### Gestão de Risco
- **Hard Stop**: Trava automática em 50% de drawdown.
- **Shadow Mode**: Operação simulada compulsória após o hard stop.
- **Recalibragem**: Comando /resume via Telegram para resetar base de capital.

### Tecnologias
- **Core**: Python 3.11.
- **IA**: PPO (Reinforcement Learning).
- **Infra**: Docker, Docker Compose, PostgreSQL.

---

## English

### Overview
Omni-Trader 9.1 is a quantitative trading ecosystem optimized for single VPS deployment via Docker Compose. It operates simultaneously in Stocks (US) and Cryptocurrencies (ASIA).

### VPS Architecture
- **Single Node**: Orchestration via Docker Compose.
- **Database**: TimescaleDB.
- **NOC**: Unified Telegram Bot with forum topics.

### Risk Management
- **Hard Stop**: Automatic halt at 50% drawdown.
- **Shadow Mode**: Compulsory simulated trading after hard stop.
- **Recalibration**: /resume command via Telegram to reset capital base.

### Tech Stack
- **Core**: Python 3.11.
- **AI**: PPO (Reinforcement Learning).
- **Infra**: Docker, Docker Compose, PostgreSQL.
