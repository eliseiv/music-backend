#!/usr/bin/env bash
# Запускается на сервере при деплое (GitHub Actions через ssh).
#
# Модель: образ собирается ЛОКАЛЬНО на сервере (без registry).
# Workflow перед вызовом скрипта делает `git pull` в /opt/music-backend/repo,
# скрипт делает `docker compose build api` и `up -d api`.
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/music-backend}"
cd "$DEPLOY_DIR"

# 0. Sanity
if [ ! -d "./repo" ]; then
    echo "[deploy] ERROR: ./repo не найден. Сначала склонируйте репо в $DEPLOY_DIR/repo" >&2
    exit 1
fi
if [ ! -f "./.env" ]; then
    echo "[deploy] ERROR: .env не найден в $DEPLOY_DIR" >&2
    exit 1
fi

echo "[deploy] Building api image (this can take 1-3 min)"
docker compose build api

echo "[deploy] (Re)starting api + postgres"
docker compose up -d postgres api

echo "[deploy] Waiting for api healthcheck"
status="starting"
for i in $(seq 1 60); do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || echo "starting")
    if [ "$status" = "healthy" ]; then
        echo "[deploy] api is healthy"
        break
    fi
    sleep 2
done

if [ "$status" != "healthy" ]; then
    echo "[deploy] ERROR: api did not become healthy in 120s. Last 80 log lines:" >&2
    docker compose logs --tail 80 api >&2
    exit 1
fi

# Nginx: если запущен — reload, иначе up (на первом деплое nginx может быть не поднят)
if docker compose ps nginx --status running -q | grep -q .; then
    echo "[deploy] Reloading nginx config"
    docker compose exec -T nginx nginx -t && docker compose exec -T nginx nginx -s reload || true
else
    echo "[deploy] nginx не запущен — поднимаем"
    docker compose up -d nginx
fi

echo "[deploy] Pruning old images (старше 72h)"
docker image prune -af --filter "until=72h" || true
docker builder prune -af --filter "until=72h" >/dev/null 2>&1 || true

echo "[deploy] Disk usage:"
df -h / | tail -n +2

echo "[deploy] Done"
