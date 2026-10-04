#!/usr/bin/env bash
# ==========================================================
# 🚀 BRN Bot — Inicialização (usa venv existente)
# ==========================================================
set -euo pipefail

# Cores
if [ -t 1 ]; then
    VERDE="\033[0;32m"; AMARELO="\033[1;33m"; VERMELHO="\033[0;31m"
    AZUL="\033[0;34m"; RESET="\033[0m"
else
    VERDE=""; AMARELO=""; VERMELHO=""; AZUL=""; RESET=""
fi

log()   { echo -e "${AZUL}[BRN]${RESET} $*"; }
ok()    { echo -e "${VERDE}✅ $*${RESET}"; }
aviso() { echo -e "${AMARELO}⚠️  $*${RESET}"; }
erro()  { echo -e "${VERMELHO}❌ $*${RESET}"; }

# Ir para o diretório do script (lida com espaços e acentos)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"
log "📂 Diretório: $SCRIPT_DIR"

echo
echo "============================================================"
echo "  🤖  BRN Bot — Mural de Ordens P2P"
echo "  🌐  https://brunoldo2312.github.io/brn-site/"
echo "============================================================"
echo

# ---------- 1. Python ----------
log "1️⃣  Verificando Python..."
if ! command -v python3 >/dev/null 2>&1; then
    erro "Python 3 não encontrado. Instale: sudo apt install python3 python3-venv"
    exit 1
fi
PY_VERSION=$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
ok "Python $PY_VERSION"

# ---------- 2. venv ----------
log "2️⃣  Verificando venv..."

# Tenta 'venv' primeiro (o que você já tem), depois '.venv'
if [ -d "venv" ]; then
    VENV_DIR="venv"
elif [ -d ".venv" ]; then
    VENV_DIR=".venv"
else
    log "   Criando venv..."
    python3 -m venv venv
    VENV_DIR="venv"
fi
ok "Venv: $VENV_DIR"

# Ativa
# shellcheck disable=SC1091
source "$VENV_DIR/bin/activate"
ok "Ativado ($(which python))"

# ---------- 3. pip ----------
log "3️⃣  Atualizando pip..."
python -m pip install --upgrade pip wheel setuptools >/dev/null 2>&1
ok "pip atualizado"

# ---------- 4. Dependências ----------
log "4️⃣  Instalando dependências..."
PACKAGES=(
    "python-telegram-bot[job-queue]==21.6"
    "google-generativeai==0.8.3"
    "playwright==1.48.0"
    "python-dotenv==1.0.1"
    "pytz==2024.2"
)
for pkg in "${PACKAGES[@]}"; do
    log "   → $pkg"
    python -m pip install --quiet "$pkg" || aviso "Falha: $pkg"
done
ok "Dependências OK"

# ---------- 5. Chromium ----------
log "5️⃣  Verificando Chromium..."
if [ ! -d "$HOME/.cache/ms-playwright" ] && [ ! -d "$HOME/.cache/ms-playwright/chromium" ]; then
    log "   Baixando Chromium (~150MB)..."
    playwright install chromium || aviso "Falha ao baixar Chromium"
fi
ok "Chromium OK"

# ---------- 6. .env ----------
log "6️⃣  Validando .env..."
if [ ! -f ".env" ]; then
    erro ".env não encontrado!"
    exit 1
fi

set -a
# shellcheck disable=SC1091
source .env
set +a

for var in TELEGRAM_TOKEN GEMINI_KEY CHAT_ID; do
    if [ -z "${!var:-}" ]; then
        erro "$var vazio no .env"
        exit 1
    fi
done
ok ".env OK"

# ---------- 7. Bot ----------
log "7️⃣  Verificando brn_bot.py..."
[ ! -f "brn_bot.py" ] && { erro "brn_bot.py não encontrado!"; exit 1; }
ok "brn_bot.py encontrado"

# ---------- 8. RODAR ----------
echo
echo "============================================================"
echo "  🤖  INICIANDO BOT BRN (Ctrl+C para parar)"
echo "============================================================"
echo

TENTATIVA=0
while true; do
    TENTATIVA=$((TENTATIVA + 1))

    if [ "$TENTATIVA" -gt 1 ]; then
        aviso "Reiniciando... (tentativa $TENTATIVA) — 5s"
        sleep 5
    fi

    if python brn_bot.py; then
        ok "Bot encerrado normalmente."
        break
    else
        CODE=$?
        erro "Bot caiu (código $CODE)."
        [ "$TENTATIVA" -ge 10 ] && { erro "Muitas falhas seguidas. Abortando."; exit 1; }
    fi
done

echo
ok "👋 Finalizado."