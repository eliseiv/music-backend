# Edge reverse-proxy (Traefik)

Общий веб-вход для всех сервисов на сервере. Терминирует TLS, роутит по
доменам, автоматически выпускает и продлевает Let's Encrypt сертификаты.

## Первичная установка (один раз на сервере)

```bash
# 1. Общая docker-сеть, к которой подключаются все сервисы
docker network create web

# 2. Поднять Traefik
mkdir -p /opt/edge && cd /opt/edge
# (сюда кладётся docker-compose.yml из этого каталога)
docker compose up -d
```

`acme/acme.json` создаётся автоматически и хранит сертификаты — **не удалять**.

## Как подключить новый сервис

Любой сервис (Docker, FastAPI/uvicorn на каком-то порту) добавляет себя в
прокси через docker-labels — **без правки конфигов Traefik**. Шаблон:

```yaml
# docker-compose.yml вашего сервиса
services:
  api:
    image: my-service:local
    restart: unless-stopped
    # НЕ публикуем порты наружу (ports:) — только Traefik смотрит в интернет.
    expose:
      - "8000"                     # порт, который слушает uvicorn внутри
    networks:
      - web                        # общая сеть с Traefik
      - default                    # своя внутренняя (БД и т.п.)
    labels:
      - "traefik.enable=true"
      # домен сервиса:
      - "traefik.http.routers.myservice.rule=Host(`example.com`) || Host(`www.example.com`)"
      - "traefik.http.routers.myservice.entrypoints=websecure"
      # на какой внутренний порт проксировать:
      - "traefik.http.services.myservice.loadbalancer.server.port=8000"

  # ... ваш postgres / redis в network default ...

networks:
  web:
    external: true
  default:
```

Замените `myservice` на уникальное имя, `example.com` — на ваш домен,
`8000` — на порт uvicorn.

### Чеклист добавления сервиса

1. **DNS**: A-запись домена → IP сервера (`87.239.135.154`).
2. В compose сервиса: подключить к сети `web`, убрать `ports:`, добавить labels.
3. `docker compose up -d` — Traefik подхватит сразу, SSL выпустится при первом
   запросе (DNS уже должен резолвиться).
4. Проверить: `curl -I https://example.com/healthz`.

### CI/CD

Деплой сервиса = `git pull` + `docker compose up -d --build` в каталоге сервиса.
Traefik **не трогается** — маршрут берётся из labels контейнера. Никаких
ручных шагов с прокси.

## Полезное

```bash
docker compose -f /opt/edge/docker-compose.yml logs -f traefik   # логи
cat /opt/edge/acme/acme.json | jq '.le.Certificates[].domain'    # выданные сертификаты
```
