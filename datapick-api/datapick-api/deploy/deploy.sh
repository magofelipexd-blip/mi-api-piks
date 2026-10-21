#!/usr/bin/env bash
# Sube el proyecto al servidor y lo instala o actualiza. Se ejecuta DESDE TU PC (Git Bash):
#   bash deploy/deploy.sh ubuntu@IP_DEL_SERVIDOR ruta/a/la/clave.key
set -euo pipefail
cd "$(dirname "$0")/.."

HOST=${1:?Uso: bash deploy/deploy.sh ubuntu@IP ruta/clave.key}
KEY=${2:?Falta la ruta de la clave SSH privada}

# ssh exige que la clave no sea legible por otros: se usa una copia con permisos correctos
mkdir -p ~/.ssh
cp "$KEY" ~/.ssh/datapick_oracle.key
chmod 600 ~/.ssh/datapick_oracle.key
SSH=(ssh -i ~/.ssh/datapick_oracle.key -o StrictHostKeyChecking=accept-new "$HOST")

echo "==> Subiendo proyecto a $HOST:~/datapick"
tar czf - api db deploy docker-compose.prod.yml release \
  --exclude='__pycache__' --exclude='.pytest_cache' --exclude='*.png' \
  | "${SSH[@]}" 'mkdir -p ~/datapick && tar xzf - -C ~/datapick'

echo "==> Instalando y arrancando"
"${SSH[@]}" "cd ~/datapick && DOMAIN='${DOMAIN:-}' bash deploy/setup.sh"
