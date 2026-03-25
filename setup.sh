#!/bin/bash
set -e

APP_DIR="/home/adm_luke/prod/docker"
LOG_DIR="/var/log/omni"
PYTHON="python3"
PM2_STARTUP_CMD=""

echo "==================================================="
echo "  Omni-Trader IBKR — Setup Script (Ubuntu 22.04)"
echo "==================================================="

echo "[1/8] Atualizando sistema..."
apt-get update -y && apt-get upgrade -y
apt-get install -y python3 python3-pip python3-venv git curl build-essential libpq-dev

echo "[2/8] Instalando Node.js e PM2..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y nodejs
npm install -g pm2

echo "[3/8] Criando diretórios..."
mkdir -p "$APP_DIR" "$LOG_DIR"

echo "[4/8] Clonando repositório (ou atualizando)..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR" && git pull origin main
else
    read -p "URL do repositório Git: " REPO_URL
    git clone "$REPO_URL" "$APP_DIR"
    cd "$APP_DIR"
fi

echo "[5/8] Criando ambiente virtual Python..."
$PYTHON -m venv "$APP_DIR/venv"
source "$APP_DIR/venv/bin/activate"
pip install --upgrade pip wheel
pip install -r services/requirements.txt

echo "[6/8] Configurando .env..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo ""
    echo "⚠️  Arquivo .env criado a partir do .env.example."
    echo "    Edite agora com suas credenciais IBKR e Telegram:"
    echo "    nano $APP_DIR/.env"
    echo ""
    read -p "Pressione ENTER após editar o .env para continuar..."
fi

echo "[7/8] Configurando firewall (UFW)..."
ufw allow 22/tcp comment "SSH"
ufw allow 24001:24004/tcp comment "IBKR Gateway API"
ufw allow 25900/tcp comment "IBKR VNC (monitoramento)"
ufw allow 28000/tcp comment "Order Router interno"
ufw --force enable

echo "[8/8] Iniciando serviços via PM2..."
cd "$APP_DIR"
pm2 start ecosystem.config.js
pm2 save

echo ""
echo "Configurando PM2 para reiniciar após reboot..."
PM2_STARTUP_CMD=$(pm2 startup systemd -u root --hp /root | tail -1)
if [[ "$PM2_STARTUP_CMD" == sudo* ]]; then
    eval "$PM2_STARTUP_CMD"
fi

echo ""
echo "==================================================="
echo "  ✅ Omni-Trader IBKR ONLINE!"
echo "==================================================="
echo "  Verificar logs:   pm2 logs"
echo "  Status serviços:  pm2 status"
echo "  Monitorar RAM:    pm2 monit"
echo "  Reiniciar tudo:   pm2 restart all"
echo "  Parar tudo:       pm2 stop all"
echo "  Abrir Router API: http://localhost:28000/health"
echo "==================================================="
