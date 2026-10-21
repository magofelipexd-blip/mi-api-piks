#!/usr/bin/env bash
# Instala y arranca DataPick en un servidor Ubuntu (probado para Oracle Cloud Always Free, ARM o x86).
# Se ejecuta EN EL SERVIDOR, desde la carpeta del proyecto:  bash deploy/setup.sh
# Opcional: DOMAIN=midominio.com bash deploy/setup.sh   (por defecto usa <ip>.sslip.io)
set -euo pipefail
cd "$(dirname "$0")/.."

log() { printf '\n\033[1;32m==> %s\033[0m\n' "$*"; }

log "1/5 Docker"
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
  sudo usermod -aG docker "$USER" || true
fi
sudo systemctl enable --now docker

log "2/5 Cortafuegos del sistema (las imágenes de Oracle bloquean todo salvo SSH)"
for port in 80 443; do
  sudo iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null \
    || sudo iptables -I INPUT 1 -p tcp --dport "$port" -j ACCEPT
done
if command -v netfilter-persistent >/dev/null 2>&1; then
  sudo netfilter-persistent save
else
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y iptables-persistent >/dev/null && sudo netfilter-persistent save || true
fi

log "3/5 Configuración (.env.prod)"
if [ ! -f .env.prod ]; then
  IP=$(curl -fsS https://api.ipify.org || curl -fsS https://ifconfig.me)
  DOMAIN=${DOMAIN:-$(echo "$IP" | tr . -).sslip.io}
  cat > .env.prod <<EOF
DOMAIN=$DOMAIN
POSTGRES_PASSWORD=$(openssl rand -hex 24)
ADMIN_KEY=$(openssl rand -hex 24)
EOF
  chmod 600 .env.prod
  echo "Creado .env.prod para $DOMAIN (guarda una copia: contiene las claves)"
else
  echo ".env.prod ya existe: se conserva"
fi
set -a; . ./.env.prod; set +a

log "4/5 Construir y arrancar contenedores"
sudo docker compose -f docker-compose.prod.yml --env-file .env.prod up -d --build

log "5/5 Comprobar"
for i in $(seq 1 60); do
  if curl -fsS "https://$DOMAIN/health" >/dev/null 2>&1; then
    echo "API en línea: https://$DOMAIN"
    echo "Descarga del APK: https://$DOMAIN/descargar/"
    echo "Registro del motor: sudo docker compose -f docker-compose.prod.yml logs -f worker"
    exit 0
  fi
  sleep 5
done
echo "La API aún no responde por HTTPS. Revisa:"
echo "  - En Oracle: Networking > VCN > Security List, reglas de entrada TCP 80 y 443 desde 0.0.0.0/0"
echo "  - sudo docker compose -f docker-compose.prod.yml logs caddy api"
exit 1
