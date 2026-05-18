#!/usr/bin/env bash
# Backup-вариант на случай, если нужно ручное продление.
# В docker-compose.prod.yml certbot уже работает в цикле renew каждые 12 часов
# и nginx сам делает reload каждые 6 часов. Этот скрипт пригодится только
# для немедленного renew + reload.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/aibased}"
cd "$DEPLOY_DIR"

echo "[renew] Forcing certbot renewal check"
docker compose run --rm --entrypoint \
    "certbot renew --webroot -w /var/www/certbot --keep-until-expiring" \
    certbot

echo "[renew] Reloading nginx"
docker compose exec -T nginx nginx -t && docker compose exec -T nginx nginx -s reload

echo "[renew] Done"
