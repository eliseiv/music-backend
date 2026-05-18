#!/usr/bin/env bash
# Запускается на сервере при деплое из GitHub Actions.
# Принимает имя образа в $API_IMAGE (env-переменная, прокидывается из workflow).
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/aibased}"
cd "$DEPLOY_DIR"

if [ -z "${API_IMAGE:-}" ]; then
    echo "ERROR: API_IMAGE не задан" >&2
    exit 1
fi

echo "[deploy] Pulling $API_IMAGE"
echo "API_IMAGE=$API_IMAGE" > .env.image

# Объединяем .env (общий) и .env.image (только тэг) при запуске compose.
docker compose --env-file .env --env-file .env.image pull api

echo "[deploy] Restarting api"
docker compose --env-file .env --env-file .env.image up -d api

echo "[deploy] Waiting for api healthcheck"
for i in $(seq 1 30); do
    status=$(docker inspect --format='{{.State.Health.Status}}' "$(docker compose ps -q api)" 2>/dev/null || echo "starting")
    if [ "$status" = "healthy" ]; then
        echo "[deploy] api is healthy"
        break
    fi
    sleep 2
done

if [ "$status" != "healthy" ]; then
    echo "[deploy] ERROR: api did not become healthy in 60s. Last 50 log lines:" >&2
    docker compose logs --tail 50 api >&2
    exit 1
fi

echo "[deploy] Reloading nginx (на случай изменений конфига)"
docker compose exec -T nginx nginx -t && docker compose exec -T nginx nginx -s reload || true

echo "[deploy] Pruning old images (старше 72h, не привязанные к контейнерам)"
docker image prune -af --filter "until=72h" || true
docker builder prune -af --filter "until=72h" >/dev/null 2>&1 || true

echo "[deploy] Disk usage after prune:"
df -h / | tail -n +2

echo "[deploy] Done"
