# Music Generation Backend

Backend для генерации музыки через **fal.ai** с монетизацией на токенах, подписками (Adapty / RuStore), кошельком и webhooks

**Стек:** Python 3.12, FastAPI, SQLAlchemy 2 (async), PostgreSQL 16, Alembic, httpx, Docker Compose.

---

## Быстрый старт

```bash
# 1. Подготовить .env
cp .env.example .env
# отредактировать .env: указать FAL_API_KEY, ADAPTY_WEBHOOK_SECRET, RF_BILLING_WEBHOOK_SECRET, API_KEY

# 2. Поднять контейнеры
docker compose up --build
```

Когда оба контейнера в статусе `healthy`, доступны:

- **API:** `http://localhost:8000/v1`
- **Swagger UI:** `http://localhost:8000/docs`
- **ReDoc:** `http://localhost:8000/redoc`
- **OpenAPI JSON:** `http://localhost:8000/openapi.json`
- **Healthcheck:** `GET http://localhost:8000/healthz`

При старте автоматически выполняется `alembic upgrade head` — все миграции применяются без ручных шагов.

---

## Авторизация

Для всех эндпоинтов под `/v1/` (кроме webhook'ов) требуются **два заголовка**:

```
Authorization: Bearer <API_KEY>
X-User-Id: <stable-device-or-profile-id>
```

`X-User-Id` — стабильный идентификатор клиента (Adapty profile id или аналог). По нему backend ведёт записи в `music_users` (атомарный upsert).

**Webhook'и** (`/v1/webhooks/...`) авторизуются подписью провайдера:
- `/v1/webhooks/fal` — HMAC-SHA256 в `X-Fal-Signature`
- `/v1/webhooks/billing/adapty` — bearer-секрет в `Authorization`
- `/v1/webhooks/billing/rf` — HMAC-SHA256 в `X-RuStore-Signature`

---

## Формат ответов

### Успех

camelCase JSON, например `POST /v1/tracks/generate`:

```json
{
  "jobId": "9b8a90d1-2dab-4d9b-a48c-3061a6f8a8e1",
  "status": "processing",
  "tokensReserved": 1
}
```

### Ошибки

Все ошибки 4xx/5xx возвращают единый envelope:

```json
{
  "error": {
    "code": "INSUFFICIENT_TOKENS",
    "message": "Not enough tokens to perform the operation",
    "details": { "required": 2, "available": 0 }
  },
  "requestId": "b5830b11dc4747d4b6b85217eff10177"
}
```

Коды (UPPER_SNAKE_CASE):

| Код | HTTP | Когда |
|---|---|---|
| `INVALID_INPUT` | 400 | Валидация payload (включая `auxiliary` ≠ 3, `tempo` вне 30..160) |
| `INVALID_SAMPLE_URL` | 400 | HEAD-проверка sample/voice URL провалилась |
| `MISSING_X_USER_ID` | 400 | Нет/пустой `X-User-Id` |
| `UNAUTHORIZED` | 401 | Нет/неверный Bearer |
| `WEBHOOK_SIGNATURE_INVALID` | 401 | Подпись webhook не сошлась |
| `WEBHOOK_PAYLOAD_INVALID` | 400 | Битый payload webhook'а |
| `SUBSCRIPTION_REQUIRED` | 402 | Подписка не оформлена |
| `SUBSCRIPTION_EXPIRED` | 402 | Подписка истекла; кошелёк frozen |
| `INSUFFICIENT_TOKENS` | 402 | Не хватает токенов на резерв |
| `FORBIDDEN` | 403 | Ресурс принадлежит другому пользователю |
| `BEAT_NOT_FOUND` / `JOB_NOT_FOUND` / `TRACK_NOT_FOUND` | 404 | — |
| `RATE_LIMITED` | 429 | Превышен лимит (см. `Retry-After`) |
| `PROVIDER_FAILED` | 502 | fal.ai вернул ошибку |
| `PROVIDER_TIMEOUT` | 504 | Таймаут fal.ai |
| `INTERNAL_ERROR` | 500 | Прочее |

Каждый ответ содержит заголовок `X-Request-Id` (можно прокинуть свой, отправив тот же заголовок в запросе) — удобно для логов.

---

## Эндпоинты под `/v1/`

| Метод и путь | Назначение |
|---|---|
| `GET /v1/beats` | Каталог битов (5 жанров) |
| `GET /v1/samples` | Sound-elements в 10 категориях с тегами |
| `POST /v1/tracks/generate` | Старт генерации: gate → URL check → reserve → pipeline → `jobId` |
| `GET /v1/tracks/jobs/{jobId}` | Статус задания + массив `pipeline` со стадиями |
| `GET /v1/tracks/{trackId}` | Готовый трек (`audioUrl`, `duration`, опционально `stems`) |
| `GET /v1/tokens/balance` | Баланс + reserved + флаг frozen |
| `GET /v1/tokens/products` | Каталог токен-паков Adapty/RuStore |
| `POST /v1/uploads/voice` | Multipart-загрузка голоса → URL fal storage |
| `POST /v1/webhooks/fal` | Финализация stage-by-stage пайплайна |
| `POST /v1/webhooks/billing/adapty` | События Adapty (5 типов) |
| `POST /v1/webhooks/billing/rf` | События RuStore (5 типов) |

---

## Логика генерации

Pipeline на **8 стадий**, оркеструемых через fal-webhook'и:

```
prepare_prompt → lyrics → music_generation → audio_to_audio_refine
                                                 ↓
                            vocal_tts → mix_master → upload_cdn → finalize
```

| Stage | Тип | Когда выполняется |
|---|---|---|
| `prepare_prompt` | inline | всегда |
| `lyrics` | inline | если есть `lyricsPrompt` (иначе `skipped`) |
| `music_generation` | async fal (`fal-ai/minimax-music`) | всегда |
| `audio_to_audio_refine` | async fal (`fal-ai/ace-step/audio-to-audio`) | если передан `beatId` |
| `vocal_tts` | async fal (`fal-ai/minimax/speech-02-turbo`) | если передан `voiceUrl` + `lyricsPrompt` |
| `mix_master` | inline | всегда |
| `upload_cdn` | inline | всегда |
| `finalize` | inline | INSERT в `tracks`, `capture` токенов, mark `succeeded` |

Все 8 стадий гарантированно записываются в `job_stage_log`. Стадии, которые не выполнялись, помечаются `skipped`. `GET /v1/tracks/jobs/{jobId}` отдаёт массив:

```json
{
  "jobId": "...",
  "status": "succeeded",
  "stage": "finalize",
  "pipeline": [
    {"stage": "prepare_prompt", "status": "succeeded", ...},
    {"stage": "lyrics", "status": "skipped"},
    {"stage": "music_generation", "status": "succeeded", "startedAt": "...", "finishedAt": "..."},
    {"stage": "audio_to_audio_refine", "status": "succeeded", ...},
    {"stage": "vocal_tts", "status": "skipped"},
    {"stage": "mix_master", "status": "succeeded", ...},
    {"stage": "upload_cdn", "status": "succeeded", ...},
    {"stage": "finalize", "status": "succeeded", ...}
  ],
  "trackId": "..."
}
```

**Защита от reorder** — `jobs.current_stage` хранит последний запущенный async-stage; webhook для не-текущей стадии логируется и игнорируется.

---

## Voice flow (двухшаговый)

```
1. POST /v1/uploads/voice (multipart, file=audio/wav|mp3|m4a, max 25 MiB)
   → response: { "voiceUrl": "https://fal-cdn/.../voice.wav" }

2. POST /v1/tracks/generate
   Body: { "voiceUrl": "https://fal-cdn/.../voice.wav", ... }
```

`voiceUrl` подставляется в payload `/tracks/generate` как обычное поле. Если voice не нужен — поле `null`.

---

## Idempotency-Key

`POST /v1/tracks/generate` принимает опциональный header:

```
Idempotency-Key: client-uuid-or-any-string-up-to-128
```

При повторном вызове с тем же ключом (для того же `X-User-Id`) backend вернёт ранее созданный `jobId` **без повторного списания токенов**. Безопасный retry при сетевых сбоях.

---

## Валидация payload

`POST /v1/tracks/generate` принимает strict JSON (extra fields → 400):

```json
{
  "beatId": "uuid",
  "instruments": {
    "harmonic": {
      "bass":  { "sampleUrl": "https://..." },
      "lead":  { "sampleUrl": "https://..." },
      "chord": { "sampleUrl": "https://..." }
    },
    "drums": {
      "kick":        { "sampleUrl": "..." },
      "snare":       { "sampleUrl": "..." },
      "openHihat":   { "sampleUrl": "..." },
      "closedHihat": { "sampleUrl": "..." },
      "auxiliary":   [ {...}, {...}, {...} ]    // ровно 3
    },
    "mixing":       { "sampleUrl": "..." },
    "soundEffects": { "sampleUrl": "..." }
  },
  "equalizer": {
    "tempo": 124,            // 30..160
    "leadDensity": 7,        // 0..10
    "bassDensity": 8,        // 0..10
    "chordDensity": 5,       // 0..10
    "drumDensity": 9         // 0..10
  },
  "lyricsPrompt": null,
  "voiceUrl": null,          // получить через POST /v1/uploads/voice
  "production": null,        // null или одно из 13 значений (см. tags.py)
  "pitch": null,             // null или одно из 9 значений
  "storeStems": false,
  "language": "en",
  "desiredDurationSeconds": 60
}
```

**HEAD-проверка URL** (`MUSIC_URL_CHECK_ENABLED=true`): перед резервом токенов backend параллельно проверяет, что каждый `sampleUrl` и `voiceUrl` отвечают 2xx/3xx. При недоступности — `400 INVALID_SAMPLE_URL` (токены не резервируются).

---

## Токены и кошелёк

Все операции — **append-only ledger** + `with_for_update()` на `token_wallets` и `subscription_state`.

| Операция | Когда | Эффект |
|---|---|---|
| `reserve` | `POST /tracks/generate` | `available -= n`, `reserved += n`. Idempotent по `(job_id, debit_reserve)`. |
| `capture` | webhook → finalize | `reserved -= prev`, `available += (prev - actual)`. Работает на frozen wallet. |
| `release` | webhook failed/canceled | `reserved -= n`, `available += n`. |
| `credit` (purchase/subscription/refund) | billing webhook | `available += n`. Refund clamps в 0 + auxiliary `debit_adjustment` запись. |

CHECK CONSTRAINT'ы в БД: `available_tokens >= 0`, `reserved_tokens >= 0` — отрицательный баланс физически невозможен.

---

## Ценообразование

Конфигурируется через таблицу `pricing_rules`:

```sql
provider_model VARCHAR,           -- 'fal-ai/minimax-music', ...
billing_mode   ENUM(per_track|per_minute),
token_rate     NUMERIC(12,4),     -- 1 токен = 1 трек ИЛИ 1 минута
rounding_mode  ENUM(ceil|floor|nearest),
precharge_default_units NUMERIC,  -- для per_minute precharge
active_from    TIMESTAMPTZ        -- последнее правило по дате выигрывает
```

Pre-charge перед резервом: `required_tokens_for_precharge(rule, desired_duration_seconds)`.
Capture после fal: `required_tokens_for_capture(rule, actual_duration_seconds)` — разница `release`'ится обратно.

---

## Billing webhooks

Оба провайдера (Adapty + RuStore) нормализуются в `NormalizedBillingEvent` с 6 типами:

- `SUBSCRIPTION_PURCHASED` / `SUBSCRIPTION_RENEWED` → `status=active`, `wallet.frozen=false`, опционально кредит `token_amount`
- `SUBSCRIPTION_CANCELED` → `status=canceled`, `expires_at` сохраняется (доступ до конца периода)
- `SUBSCRIPTION_EXPIRED` → `status=expired`, `wallet.frozen=true` (токены замораживаются, не списываются)
- `ONE_TIME_PURCHASE` → резолв `token_products.token_amount` по `(platform, external_product_id)` → credit
- `REFUND` → debit с clamp в 0

**Атомарность**: user, subscription_state, wallet, ledger, processed_webhooks применяются в **одной транзакции**. При падении посередине — полный rollback.

**2-фазная обработка**: сначала `INSERT processed_webhooks(outcome='received')`, после успешного применения — `UPDATE outcome='applied'`. Recovery sweep при старте находит застрявшие `received`-события и алертит.

**Защита от reorder**: `subscription_state.last_event_occurred_at` — событие со старым timestamp применяется консервативно (логируется, не понижает `expires_at`).

**Идемпотентность**: PK `(provider, event_id)` в `processed_webhooks` — дубль возвращает `{"status": "duplicate"}` без эффекта.

---

## Конфигурация (ENV)

| Переменная | Назначение |
|---|---|
| `DATABASE_URL` | `postgresql+asyncpg://user:pass@host:5432/db` |
| `API_KEY` | Bearer для всех `/v1/` (кроме webhook'ов) |
| `RATE_LIMIT_PER_MINUTE` | `0` = выкл; иначе — лимит req/min на ключ |
| `PUBLIC_BASE_URL` | Базовый URL backend'а для webhook callback fal (`https://your-domain`) |
| `FAL_API_KEY` | Ключ fal.ai (без него `/tracks/generate` → 503) |
| `FAL_BASE_URL` | По умолчанию `https://queue.fal.run` |
| `FAL_WEBHOOK_SECRET` | HMAC-секрет для `/v1/webhooks/fal` |
| `FAL_USE_STUB` | `true` — in-process stub для dev (без реального fal) |
| `FAL_MUSIC_MODEL` / `FAL_REFINE_MODEL` / `FAL_SPEECH_MODEL` | Имена моделей |
| `ADAPTY_WEBHOOK_SECRET` | Bearer Adapty в `Authorization` |
| `RF_BILLING_WEBHOOK_SECRET` | HMAC-секрет RuStore |
| `MUSIC_URL_CHECK_ENABLED` | HEAD-проверка sample/voice URL; `false` для dev |
| `MUSIC_URL_CHECK_TIMEOUT_SECONDS` | Таймаут одной HEAD (3.0 default) |
| `MUSIC_VOICE_MAX_BYTES` | Лимит голосового файла (25 MiB) |
| `MUSIC_VOICE_ALLOWED_CONTENT_TYPES` | CSV allowlist |

Полный список — в `.env.example`.

---

## Seed-данные

Шаблонные beats/samples лежат в `app/music/seed/data/`. Залить в БД:

```bash
docker compose exec api python -m app.music.seed.run_seed \
  --beats   app/music/seed/data/beats.json \
  --samples app/music/seed/data/samples.json
```

`pricing_rules` и `token_products` применяются автоматически через миграцию `0002_music_seed_pricing.py`.

---

## Smoke-тест через Swagger

1. Откройте http://localhost:8000/docs
2. Нажмите **Authorize** → введите `API_KEY` (без `Bearer`, Swagger добавит сам).
3. Для каждого music-эндпоинта добавьте header `X-User-Id: smoke-1` (поле появится автоматически).
4. `GET /v1/beats` → 5 битов после seed.
5. `GET /v1/samples` → 10 категорий.
6. `GET /v1/tokens/balance` → `{"available": 0, "reserved": 0, "frozen": false}` для нового X-User-Id.
7. `POST /v1/tracks/generate` — без подписки → 402 `SUBSCRIPTION_REQUIRED`; без `FAL_API_KEY` → 503.

---

## Тесты

Тестам нужна реальная PostgreSQL (используется PG ENUM, JSONB, GIN-индексы).

```bash
# 1. Поднять отдельную тестовую БД на отдельном порту
docker run -d --name music_test_pg \
  -e POSTGRES_USER=music -e POSTGRES_PASSWORD=music -e POSTGRES_DB=music_test \
  -p 5433:5432 postgres:16-alpine

# 2. Установить dev-зависимости (через uv)
uv sync --extra dev

# 3. Запустить тесты
DATABASE_URL=postgresql+asyncpg://music:music@localhost:5433/music_test \
  .venv/Scripts/python -m pytest -q

# Покрытие
DATABASE_URL=postgresql+asyncpg://music:music@localhost:5433/music_test \
  .venv/Scripts/python -m pytest --cov=app --cov-report=term-missing
```

**Состав тестов:**
- `tests/unit/` — pricing, wallet, subscription_gate, fal_signature (26 тестов)
- `tests/integration/` — auth, error_envelope, catalog, tokens, tracks_generate, tracks_pipeline, webhooks_fal, webhooks_billing, uploads_voice (40 тестов)
- `tests/fakes/fake_fal.py` — тестовый double для fal.ai с `emit_webhook` helper для end-to-end pipeline тестов.

---

## Архитектура

```
HTTP → RequestContextMiddleware → RateLimitMiddleware → Router
     → Depends(auth + X-User-Id) → Service → Repository | Provider → DB | fal.ai
```

- **Routers** (`app/api/v1/music/`) — HTTP-валидация и маппинг ошибок.
- **Services** (`app/music/services/`) — бизнес-логика и транзакции:
  - `GenerationService` — orchestration entrypoint
  - `Pipeline` — state machine из 8 стадий
  - `WalletService` — `reserve/capture/release/credit` с idempotency через `token_ledger.idempotency_key`
  - `PricingService` — резолв активного `pricing_rule` + расчёт токенов
  - `SubscriptionGate` — `ensure_active` с lazy-expire и заморозкой кошелька
  - `BillingWebhookService` — атомарное применение adapty/rustore событий
  - `recovery` — startup sweep orphan jobs + `received`-webhooks
- **Repositories** (`app/music/repositories/`) — тонкие обёртки над `AsyncSession`. `WalletsRepository`, `SubscriptionsRepository`, `JobsRepository` используют `with_for_update()`.
- **Providers** (`app/music/providers/`) — fal client + adapty/rf billing parsers; в тестах подменяются через DI override.

---

## Production-деплой

Артефакты в `deploy/`:
- **Nginx** обрывает TLS, проксирует `/v1/`, `/healthz`, `/docs`, `/redoc`, `/openapi.json` в контейнер `api`.
- **Certbot** — Let's Encrypt + auto-renew.
- **GitHub Actions** (`.github/workflows/deploy.yml`) — push → build ghcr → ssh deploy.

Подробности — в [`deploy/README.md`](deploy/README.md).

---

## Структура проекта

```
app/
  api/v1/music/             routers: beats, samples, tokens, tracks, uploads, webhooks
  api/errors.py             APIError hierarchy + UPPER_CASE codes + envelope handlers
  auth/                     ApiKeyResolver (uuid5)
  db/                       async engine/session/enums
  middleware/               request_context (X-Request-Id), rate_limit (token bucket)
  music/
    enums.py                JobStatus, JobStage, SampleCategory, BeatGenre, ...
    models/                 ORM (11 таблиц) + JobStageLog
    providers/fal/          FalAiProvider + StubFalProvider + signature
    providers/billing/      adapty + rf parsers → NormalizedBillingEvent
    repositories/           тонкие обёртки + FOR UPDATE
    schemas/                Pydantic v2 (camelCase aliases)
    services/               business logic + Pipeline state machine
    seed/                   JSON + importers + run_seed
    tags.py                 HARMONIC_TAGS/DRUM_TAGS/PRODUCTION/PITCH таксономия
  config.py                 Settings (pydantic-settings)
  deps.py                   FastAPI Depends + bearer + X-User-Id
  main.py                   create_app + lifespan + recovery sweep
migrations/                 Alembic (0001 init, 0002 seed pricing, 0003 pipeline ext)
tests/
  unit/                     pricing, wallet, subscription_gate, fal_signature
  integration/              auth, error_envelope, catalog, tokens, tracks_*, webhooks_*, uploads
  fakes/fake_fal.py         FakeFal с emit_webhook helper
deploy/                     Nginx + certbot + production docker-compose + scripts
.github/workflows/          CI/CD
```

