# Production deploy

Развёртывание AI-Backend на одном Ubuntu 22.04+ сервере с TLS-сертификатами Let's Encrypt и автоматическим деплоем из GitHub Actions.

**Ключевые принципы:**
- Билд Docker-образа происходит на GitHub-раннере, на сервере выполняется только `docker pull` — экономит место на 15-гигабайтном диске.
- Старые образы автоматически удаляются после каждого деплоя (`docker image prune --filter until=72h`) и еженедельно по cron.
- TLS-сертификаты выпускаются и автоматически продлеваются Certbot в отдельном контейнере, без вмешательства в хост.
- Nginx автоматически перечитывает конфиг каждые 6 часов, чтобы подхватить обновлённые сертификаты.

---

## Архитектура

```
        Internet (443/80)
              │
        ┌─────▼─────┐
        │   nginx   │  TLS-termination, reverse proxy
        └─────┬─────┘
              │ http (внутри docker network)
        ┌─────▼─────┐
        │    api    │  FastAPI (image из ghcr.io)
        └─────┬─────┘
              │
        ┌─────▼─────┐
        │ postgres  │  volume pgdata
        └───────────┘

        ┌──────────┐  webroot challenge через
        │ certbot  │  /var/www/certbot, обновляется
        └──────────┘  каждые 12h
```

---

## Шаги развёртывания

### 1. Первичная настройка сервера

```bash
ssh user@your-server
git clone https://github.com/your-org/ai-based.git
cd ai-based/deploy
chmod +x scripts/*.sh
DEPLOY_USER=$USER ./scripts/init-server.sh
# перелогинься, чтобы группа docker применилась:
exit
ssh user@your-server
```

Скрипт делает:
- `apt update && apt upgrade`, ставит `docker-ce`, `docker-compose-plugin`, `ufw`, `fail2ban`
- Создаёт swapfile 2 ГБ (важно для 15 ГБ дисков, чтобы не упираться в OOM при пиковых вызовах LLM)
- Открывает UFW: только 22, 80, 443
- Включает unattended-upgrades для security-патчей
- Настраивает log-rotation для docker (10 МБ × 3)
- Создаёт `/opt/aibased/` с подкаталогами и нужными правами
- Добавляет cron `0 4 * * 0 docker image prune` (каждое воскресенье в 04:00)

### 2. Скопировать содержимое `deploy/` в `/opt/aibased/`

```bash
sudo cp -r deploy/. /opt/aibased/
sudo chown -R $USER:$USER /opt/aibased
cd /opt/aibased
mv docker-compose.prod.yml docker-compose.yml
```

### 3. Заполнить `.env`

```bash
cp .env.production.example .env
nano .env
```
Минимум: `POSTGRES_PASSWORD`, `API_KEY`, `OPENAI_API_KEY`. Поле `API_IMAGE` затирается CI на каждом деплое — пока можно оставить как есть.

### 4. Проверить DNS

A-запись `appstorepro.store` (и `www.appstorepro.store`) должна указывать на IP сервера. Проверка:
```bash
dig +short appstorepro.store
```

### 5. Выпустить TLS-сертификаты

```bash
LETSENCRYPT_EMAIL=you@example.com ./scripts/init-letsencrypt.sh
```

Скрипт:
1. Создаёт самоподписанную заглушку, чтобы Nginx смог стартовать на :443
2. Поднимает Nginx с временным HTTP-only конфигом (только ACME-challenge на :80)
3. Запускает certbot в режиме `webroot` для домена и `www`-поддомена
4. Возвращает финальный конфиг с HTTPS и делает `nginx -s reload`

После успешного завершения проверьте:
```bash
curl -I https://appstorepro.store/healthz
# HTTP/2 200
```

> При проблемах — добавьте `STAGING=1` в команду, тогда certbot обратится к staging-серверу LE (без рейт-лимитов на ошибки), но сертификат будет невалидным в браузере.

### 6. Настроить GitHub Secrets

В репозитории на GitHub: **Settings → Secrets and variables → Actions** добавьте:

| Имя | Значение |
|---|---|
| `SSH_HOST` | IP или hostname сервера |
| `SSH_PORT` | 22 (или ваш кастомный) — опционально |
| `SSH_USER` | sudo-юзер с группой `docker` |
| `SSH_PRIVATE_KEY` | Приватный ключ от SSH (без passphrase или с агентом) |
| `GHCR_PULL_TOKEN` | Personal Access Token (classic) с скоупом `read:packages` — для `docker login` на сервере |

### 7. Положить SSH-ключ на сервер

На сервере добавьте публичную часть `SSH_PRIVATE_KEY` в `~/.ssh/authorized_keys` для `SSH_USER`.

### 8. Запустить первый деплой

`git push origin main` или вручную: **Actions → Build and Deploy → Run workflow**.

CI:
1. Билдит образ из текущего `Dockerfile` на ubuntu-latest
2. Пушит в `ghcr.io/<owner>/<repo>:sha-XXXXXXX` и `:latest`
3. Подключается по SSH к серверу
4. Делает `docker login ghcr.io`, потом `bash scripts/deploy.sh`
5. `deploy.sh` делает `docker compose pull api`, `up -d api`, ждёт healthcheck, прунит старые образы
6. Финальный smoke `https://appstorepro.store/healthz`

---

## Что после деплоя

### Логи
```bash
cd /opt/aibased
docker compose logs -f api
docker compose logs -f nginx
docker compose logs -f certbot
```

### Перезагрузка
```bash
docker compose restart api
docker compose restart nginx
```

### Применить новый `.env`
```bash
cd /opt/aibased
docker compose up -d  # compose сам пересоздаст контейнеры с новыми переменными
```

### Ручное обновление сертификатов (на случай если что-то)
```bash
./scripts/renew-certs.sh
```

В обычной работе этого делать не нужно — certbot контейнер уже крутит цикл `renew → sleep 12h`, а Nginx делает `nginx -s reload` каждые 6 часов (см. `docker-compose.yml`).

### Бэкапы Postgres
В compose-файле PostgreSQL хранит данные в volume `pgdata`. Простейший бэкап:
```bash
docker compose exec -T postgres pg_dump -U aibased aibased | gzip > /opt/aibased/backups/db-$(date +%F).sql.gz
```
Можно добавить в cron.

### Очистка диска
```bash
docker image prune -af --filter "until=24h"  # принудительно
docker system df                              # сколько занято
df -h /
```
В CI это делается автоматически (см. `scripts/deploy.sh`).

---

## Откат

CI пушит образы по sha-тегам, так что любую старую сборку можно явно поднять:

```bash
ssh user@server
cd /opt/aibased
API_IMAGE=ghcr.io/your-org/ai-based:sha-abc1234 bash scripts/deploy.sh
```

История образов: https://github.com/your-org/ai-based/pkgs/container/ai-based

---

## Безопасность

- `.env` лежит на сервере с правами `0600` от `SSH_USER` — секреты не попадают в Git.
- Nginx добавляет HSTS, `X-Content-Type-Options: nosniff`, `Referrer-Policy`.
- UFW блокирует всё, кроме 22/80/443.
- `fail2ban` блокирует подбор паролей на SSH (включается init-server.sh).
- Postgres не выставлен наружу (нет `ports:`), доступен только api-контейнеру.
- API-ключ — **bearer-секрет**. Если он зашит в iOS-приложение и утёк — нужно сменить и пересобрать клиент. На сервере смена сводится к редактированию `.env` и `docker compose up -d`.

---

## Чек-лист готовности

- [ ] Сервер прошёл `init-server.sh`
- [ ] DNS `appstorepro.store` и `www.appstorepro.store` указывают на сервер
- [ ] `/opt/aibased/.env` заполнен
- [ ] `init-letsencrypt.sh` отработал успешно (есть `certbot/conf/live/appstorepro.store/`)
- [ ] GitHub Secrets заданы (`SSH_HOST`, `SSH_USER`, `SSH_PRIVATE_KEY`, `GHCR_PULL_TOKEN`)
- [ ] Публичный SSH-ключ положен в `~/.ssh/authorized_keys`
- [ ] Первый workflow прошёл, `https://appstorepro.store/healthz` отвечает 200
- [ ] `https://appstorepro.store/docs` показывает Swagger
