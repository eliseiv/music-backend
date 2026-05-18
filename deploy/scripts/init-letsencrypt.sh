#!/usr/bin/env bash
# Первичный выпуск Let's Encrypt сертификата для appstorepro.store.
# Запускать ОДИН раз после init-server.sh, когда:
#   - DNS A-запись уже указывает на сервер
#   - содержимое deploy/ скопировано в /opt/aibased
#   - .env заполнен (хотя бы POSTGRES_PASSWORD, API_KEY, OPENAI_API_KEY, API_IMAGE)
set -euo pipefail

DEPLOY_DIR="${DEPLOY_DIR:-/opt/aibased}"
DOMAIN="${DOMAIN:-appstorepro.store}"
EMAIL="${LETSENCRYPT_EMAIL:?Set LETSENCRYPT_EMAIL=your@email}"
STAGING="${STAGING:-0}"  # 1 = тест против LE staging (не считается в rate limit)

cd "$DEPLOY_DIR"

if [ ! -f docker-compose.yml ]; then
    echo "ERROR: $DEPLOY_DIR/docker-compose.yml не найден. Скопируйте deploy/docker-compose.prod.yml." >&2
    exit 1
fi
if [ ! -f .env ]; then
    echo "ERROR: $DEPLOY_DIR/.env не найден." >&2
    exit 1
fi

CERT_DIR="$DEPLOY_DIR/certbot/conf/live/$DOMAIN"
if [ -d "$CERT_DIR" ]; then
    echo "Сертификат для $DOMAIN уже существует в $CERT_DIR. Удалите его, если хотите перевыпустить."
    exit 0
fi

echo "[1/5] Подготавливаю каталоги для certbot"
mkdir -p "$DEPLOY_DIR/certbot/conf" "$DEPLOY_DIR/certbot/www"

# 1. Берём dummy-сертификат, чтобы nginx стартовал на :443 (нельзя слушать ssl без файлов)
echo "[2/5] Делаю самоподписанный сертификат-заглушку"
mkdir -p "$CERT_DIR"
docker run --rm -v "$DEPLOY_DIR/certbot/conf:/etc/letsencrypt" \
    --entrypoint /bin/sh alpine/openssl -c "
apk add --no-cache openssl >/dev/null 2>&1 || true
openssl req -x509 -nodes -newkey rsa:2048 -days 1 \
    -keyout /etc/letsencrypt/live/$DOMAIN/privkey.pem \
    -out /etc/letsencrypt/live/$DOMAIN/fullchain.pem \
    -subj '/CN=localhost'
cp /etc/letsencrypt/live/$DOMAIN/fullchain.pem /etc/letsencrypt/live/$DOMAIN/chain.pem
"

echo "[3/5] Поднимаю nginx с bootstrap-конфигом (HTTP-only)"
# Используем bootstrap-конфиг, чтобы Nginx не падал на отсутствии настоящего сертификата.
mkdir -p "$DEPLOY_DIR/nginx/conf.d.live"
if [ -f "$DEPLOY_DIR/nginx/conf.d/app.conf" ]; then
    mv "$DEPLOY_DIR/nginx/conf.d/app.conf" "$DEPLOY_DIR/nginx/conf.d.live/app.conf"
fi
cp "$DEPLOY_DIR/nginx/conf.d.bootstrap/app.conf" "$DEPLOY_DIR/nginx/conf.d/app.conf"

docker compose up -d nginx

echo "[4/5] Запускаю certbot (webroot challenge)"
STAGING_FLAG=""
[ "$STAGING" = "1" ] && STAGING_FLAG="--staging"
# Удаляем самоподписанный, certbot откажется выпускать поверх настоящих ключей нет, но безопаснее:
rm -rf "$CERT_DIR"

docker compose run --rm --entrypoint "\
    certbot certonly --webroot -w /var/www/certbot \
    $STAGING_FLAG \
    --email $EMAIL \
    --agree-tos --no-eff-email --non-interactive \
    -d $DOMAIN -d www.$DOMAIN" certbot

echo "[5/5] Возвращаю боевой nginx-конфиг (HTTPS)"
mv "$DEPLOY_DIR/nginx/conf.d.live/app.conf" "$DEPLOY_DIR/nginx/conf.d/app.conf"
docker compose exec -T nginx nginx -s reload || docker compose up -d nginx

echo
echo "Готово. Сертификат лежит в $CERT_DIR"
echo "Проверьте: curl -I https://$DOMAIN/healthz"
