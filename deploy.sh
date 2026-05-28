#!/bin/bash
# deploy.sh — CamWatch
# Execute no VPS após o primeiro git clone ou para atualizar.
# Uso: bash deploy.sh

set -e

APP_DIR="/home/workuser/camwatch"
VENV="$APP_DIR/venv"

echo "==> Atualizando código..."
cd "$APP_DIR"
git pull origin main

echo "==> Instalando dependências..."
"$VENV/bin/pip" install -r requirements.txt --quiet

echo "==> Reiniciando serviços..."
sudo systemctl restart camwatch-web
sudo systemctl restart camwatch-checker

echo "==> Verificando status..."
sudo systemctl is-active camwatch-web
sudo systemctl is-active camwatch-checker

echo ""
echo "Deploy concluído."
