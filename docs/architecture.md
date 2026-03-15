# Архитектура: WhatsApp авто-нотификации

**Версия:** 1.4
**Дата:** 2026-03-13
**Статус:** active

---

## Что делает система

Автоматически отправляет WhatsApp-сообщения клиентам Sternmeister при смене этапа воронки в Kommo:

1. **Первая линия** — клиент должен записаться на термин → напоминание
2. **Вторая линия** — клиент ожидает термин → уведомление о статусе

Повторная отправка через 24ч если нет ответа, максимум 2 раза. Отправка только с 8:00 до 22:00.

### Актуализация T17 final sync (2026-03-13)

- Send window сервиса переведён на `08:00-22:00 Europe/Berlin`.
- В коде и Wazzup синхронизирована финальная S02 utility-серия:
  - Г1 `gosniki_consultation_done` → `95ddec60-bb6b-44a8-b5fb-a98abd76f974` (2 переменные)
  - Б1 `berater_accepted` → `47d2946c-f66a-4697-b702-eb5d138bb1f1` (1 переменная)
  - Б2 `berater_day_minus_7` → `b028964c-9c27-4bc9-9b97-02a5e283df16` (4 переменные)
  - Б3 `berater_day_minus_3` → `e1cb07aa-5236-4f8a-84dc-fef26b3cccf6` (3 переменные + quick reply `Нужна помощь`)
  - Б4 `berater_day_minus_1` → `a9b04e05-6b6c-4a5f-9463-d8a0d96316f4` (2 переменные + quick reply `Да, буду` / `Нет, не смогу`)
  - Б5 `berater_day_0` остаётся на `176a8b5b-8704-4d04-aee5-0fbd08641806` (1 переменная)
- Для S02 customer-facing текстов используется единая формулировка `с Бератором`; legacy `Jobcenter` / `Agentur für Arbeit` / `AA` больше не должны попадать в `templateValues`, retry/backfill или логируемый `message_text`.
- Г1 теперь двухпеременный (`SternMeister`, `news_text`), Б1 — одно-переменный onboarding, Б2 не упоминает время, Б3 использует единый `schedule_text`, Б4 больше не зависит от shared S01-шаблона.
- Б1 не досылается через webhook/retry/pending/backfill, если по сделке уже активен более поздний temporal-state (`berater_day_minus_3/-1/0`); в БД остаётся терминальный marker без дальнейшего retry.
- Б2 по АА уходит только при сочетании `days_until_aa == 7` и этапа `102183943` / `102183947`.
- Ручная часть T17 по созданию/одобрению шаблонов завершена; вне репозитория остаётся только optional whitelist render-check.

---

## Технологический стек

| Слой | Технология | Назначение |
|------|-----------|------------|
| CRM | Kommo CRM (бывш. Kommo) | Источник данных, воронки "Госники" и "Бератер" |
| Триггер | Kommo Webhooks | POST-запрос при смене этапа воронки |
| Сервис | Python (FastAPI) | Приём webhooks, бизнес-логика, cron |
| Мессенджер | Wazzup24 WABA | Отправка WhatsApp (номер +49 3046690188, WABA-шаблоны) |
| Хранение | SQLite | Лог отправок, очередь повторов |
| Алерты | Telegram Bot API | Уведомление при ошибках |

---

## Компоненты

```
┌──────────┐    webhook     ┌──────────────┐    REST API   ┌───────────┐
│  Kommo   │ ──────────────→│ Python-сервис │─────────────→│   WABA    │
│ (воронка)│                │              │               │ (+49...)  │
└──────────┘                │  ┌────────┐  │               └───────────┘
                            │  │ SQLite │  │
                            │  │ (логи) │  │
                            │  └────────┘  │
                            │              │──→ Kommo API (запись примечания)
                            │  ┌────────┐  │
                            │  │  Cron  │  │──→ Telegram (алерты)
                            │  │(повтор)│  │
                            └──────────────┘
```

### 1. Webhook handler

Принимает POST от Kommo при смене этапа воронки.

**Логика:**
1. Парсим payload: контакт, новый этап
2. Определяем тип сообщения (первая/вторая линия)
3. Проверяем время (8:00–22:00) → если вне окна, откладываем в очередь
4. Формируем сообщение (шаблон + персонализация)
5. Отправляем через Wazzup24 WABA
6. Пишем примечание в Kommo: "Сообщение отправлено"
7. Логируем в SQLite

### 2. Retry cron

Запускается раз в час. Проверяет SQLite:
- Сообщения без ответа старше 24ч → повторная отправка (макс 2 раза)
- Отложенные сообщения (вне окна 8–22) → отправка при наступлении окна

### 3. Messenger layer

```python
# messenger/wazzup.py
class WazzupMessenger:
    def send_message(self, phone: str, message_data: MessageData) -> dict: ...

@dataclass
class MessageData:
    line: str               # "gosniki_consultation_done", "berater_accepted", "berater_day_minus_7", "berater_day_minus_3", "berater_day_minus_1", "berater_day_0"
    termin_date: str        # "25.02.2026" (DD.MM.YYYY); "" допустимо для Г1/Б1
    # S02 (optional):
    name: str | None = None
    news_text: str | None = None      # Г1: customer-facing текст новости
    institution: str | None = None    # customer-facing "с Бератором"
    weekday: str | None = None
    date: str | None = None
    time: str | None = None
    checklist_text: str | None = None # Б2: plain-text checklist block
    schedule_text: str | None = None  # Б3: "Среда, 19.03.2026"
    topic: str | None = None          # legacy B1 compatibility
    subject_text: str | None = None   # legacy B4 compatibility
    datetime_text: str | None = None  # Б4: дата или дата+время одним куском
    location_text: str | None = None  # legacy B1 compatibility
```

Единственная реализация — `WazzupMessenger`. Абстракция `BaseMessenger` не создаётся (YAGNI — один backend). При необходимости добавить другой канал — выделить интерфейс.

### 4. Alert service

При ошибке отправки (Wazzup24 недоступен, номер невалиден, Kommo не отвечает) → Telegram-сообщение ответственному.

---

## Схема данных

### SQLite: таблица `messages`

| Поле | Тип | Описание |
|------|-----|----------|
| id | INTEGER PK | — |
| kommo_lead_id | INTEGER | ID сделки (лида) в Kommo |
| kommo_contact_id | INTEGER | ID контакта в Kommo |
| phone | TEXT | Номер телефона |
| line | TEXT | "gosniki_consultation_done" / "berater_accepted" / "berater_day_minus_7" / "berater_day_minus_3" / "berater_day_minus_1" / "berater_day_0" (legacy S01 "first"/"second" удалены) |
| termin_date | TEXT | Дата термина (DD.MM.YYYY) |
| message_text | TEXT | Текст отправленного сообщения |
| status | TEXT | "sent" / "delivered" / "failed" / "pending" |
| attempts | INTEGER | Количество попыток (макс 3: первая + 2 повтора) |
| created_at | DATETIME | Время создания записи |
| sent_at | DATETIME | Время последней отправки |
| next_retry_at | DATETIME | Время следующей попытки |
| messenger_id | TEXT | ID сообщения в Wazzup24 WABA |
| messenger_backend | TEXT | "wazzup" (заложена поддержка других каналов) |

---

## Интеграции

### Kommo CRM (бывш. Kommo)

**Воронки и этапы:**

**Воронка "Бух Бератер" (pipeline_id: 12154099):**
- "Принято от первой линии" — status_id: 93860331 (S01: триггер first line; S02: триггер berater_accepted)
- "Принято от первой линии (повторные)" — status_id: 93860327
- "Новый лид" — status_id: 83873491
- "Взято в работу" — status_id: 90367079
- "Недозвон" — status_id: 90367083
- "Контакт установлен" — status_id: 90367087
- "Отложенный старт" — status_id: 95514987
- "Термин ДЦ" — status_id: 10093587 (триггер: second line)
- "Закрыто и не реализовано" — status_id: 10093590
- "Терминарий" — status_id: 142
- "Закрыто и не реализовано" — status_id: 143

**Воронка "Бух Гос" (pipeline_id: 10935879):** ← S02: актуальный pipeline_id (старый 10631243 = "Бух Комм", не наш scope)
- "Консультация проведена" — status_id: 95514983 (S02: триггер gosniki_consultation_done; верифицировано из Kommo API 03.03.2026)
- "Принято от первой линии" — status_id: 8152349
- "Не предварительного согласования" — status_id: 81523499 (редактируемый)
- "Редозвон" — status_id: 83364011
- "Новый лид" — status_id: 81523503
- "Взят в работу" — status_id: 81523507
- "НЕДОЗВОН" — status_id: 82883595
- "Контакт установлен" — status_id: 81523515
- "Нет предварительного согласия" — status_id: 88519479
- "ИНТЕРЕС ПОДТВЕРЖДЁН" — status_id: 82661915
- "Счет выставлен" — status_id: 82661919
- "Предоплата получена" — status_id: 82946495
- "Рассрочка" — status_id: 82946499
- "Closed - won" — status_id: 142
- "Closed - lost" — status_id: 143

**Custom Fields (Leads):**
- "Дата термина" — field_id: 885996 (type: date)
- "Дата термина ДЦ" — field_id: 887026 (type: date)
- "Дата термина АА" — field_id: 887028 (type: date)
- "LANGUAGE_LEVEL" — field_id: 869928 (type: text)
- "Lead Email" — field_id: 889539 (type: text)

**Custom Fields (Contacts):**
- "Phone" — field_id: 849496 (type: multitext, code: PHONE)
- "Email" — field_id: 849498 (type: multitext, code: EMAIL)
- "Position" — field_id: 849494 (type: text)

**Входящий webhook:**
- Настраивается в Kommo → Настройки → Webhooks
- Событие: смена этапа воронки (pipeline status changed)
- POST на `https://<server>/webhook/kommo`

**Исходящие запросы (Kommo API v4):**
- **Base URL:** `https://sternmeister.kommo.com/api/v4` (НЕ `api-c.kommo.com` — домен `api-c` возвращает 401 "Account not found", ходить только через субдомен аккаунта)
- `GET /api/v4/contacts/{id}` — получить контакт (телефон, имя)
- `GET /api/v4/leads/pipelines` — получить воронки и этапы (status_id)
- `POST /api/v4/contacts/{id}/notes` — записать примечание "сообщение отправлено"
- Авторизация: OAuth 2.0 (long-lived token), header `Authorization: Bearer {KOMMO_TOKEN}`

### Wazzup24 WABA

Номер **+49 3046690188** подключён через Wazzup24 (получено 23.02.2026).

**Доступы (получены 23.02):**
- **API-ключ:** сохранён в `.env`
- **channelId:** сохранён в `.env`
- **Тип транспорта:** `wapi` (WhatsApp Business API)
- **Статус:** `active`

**Активные S02 WABA шаблоны (снимок API от 13.03.2026):**

| Линия | line | templateGuid | Категория | Статус | Переменные | Примечание |
|------|------|--------------|-----------|--------|------------|------------|
| Г1 | `gosniki_consultation_done` | `95ddec60-bb6b-44a8-b5fb-a98abd76f974` | UTILITY | approved | 2 | `Здравствуйте. Вас беспокоит {{1}}. Есть обновления по вашему запросу: {{2}}.` |
| Б1 | `berater_accepted` | `47d2946c-f66a-4697-b702-eb5d138bb1f1` | UTILITY | approved | 1 | onboarding-поздравление после записи |
| Б2 | `berater_day_minus_7` | `b028964c-9c27-4bc9-9b97-02a5e283df16` | UTILITY | approved | 4 | без времени; `{{4}}` = checklist block |
| Б3 | `berater_day_minus_3` | `e1cb07aa-5236-4f8a-84dc-fef26b3cccf6` | UTILITY | approved | 3 | quick reply `Нужна помощь` |
| Б4 | `berater_day_minus_1` | `a9b04e05-6b6c-4a5f-9463-d8a0d96316f4` | UTILITY | approved | 2 | quick reply `Да, буду` / `Нет, не смогу` |
| Б5 | `berater_day_0` | `176a8b5b-8704-4d04-aee5-0fbd08641806` | UTILITY | approved | 1 | без изменений после T17 |

Другие approved WABA-шаблоны в аккаунте есть, но они не используются текущими S01/S02 code paths и поэтому здесь не перечисляются.

**API методы (v3):**
```bash
# Получить список шаблонов
GET https://api.wazzup24.com/v3/templates/whatsapp
Authorization: Bearer {API_KEY}

# Отправить WABA-шаблон
POST https://api.wazzup24.com/v3/message
Authorization: Bearer {API_KEY}
Content-Type: application/json

{
  "channelId": "uuid-канала",
  "chatId": "491234567890",
  "chatType": "whatsapp",
  "templateId": "a9b04e05-6b6c-4a5f-9463-d8a0d96316f4",
  "templateValues": ["Иван", "15.03.2026 в 09:30"]
}
# Ответ: 201 Created → {"messageId": "uuid", "chatId": "..."}
```

Подробная документация API: [wazzup24_api_reference.md](5.%20unsorted/wazzup24_api_reference.md)

---

## Инфраструктура

### Размещение

Hetzner VPS (65.108.154.202), Ubuntu 24.04, Docker 29.2.1.

HTTPS-доступ через **ngrok tunnel** (статический домен `shternmeister.ngrok.pro`):
- Порт 443 занят VPN (x-ui) → ngrok решает SSL без конфликта портов
- Webhook URL: `https://shternmeister.ngrok.pro/webhook/kommo?secret=<KOMMO_WEBHOOK_SECRET>`

### Деплой

```
/app/whatsapp/              # на сервере
├── server/
│   ├── app.py              # FastAPI, webhook handler (POST /webhook/kommo)
│   ├── utils.py            # is_in_send_window(), get_next_send_window_start(), parse_bracket_form()
│   ├── cron.py             # Retry + отложенные сообщения
│   ├── messenger/
│   │   ├── __init__.py     # Экспорт get_messenger, MessageData, MessengerError
│   │   └── wazzup.py       # WazzupMessenger (lazy singleton, retry 429/5xx)
│   ├── kommo.py            # Kommo API клиент
│   ├── alerts.py           # Telegram-алерты
│   ├── db.py               # SQLite
│   └── config.py           # Переменные окружения
├── requirements.txt
├── Dockerfile
├── .env                    # продакшн-секреты
└── data/
    └── messages.db         # SQLite (volume mount)
```

### Docker

```bash
# Build
docker build -t whatsapp-notifications .

# Run (порт 8000 только на localhost, внешний доступ через ngrok)
docker run -d --name whatsapp-notifications \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v /app/whatsapp/data:/app/data \
  --env-file /app/whatsapp/.env \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  whatsapp-notifications
```

### Systemd

- `ngrok-whatsapp.service` — ngrok tunnel (auto-restart)
- `whatsapp-cron.timer` — cron каждый час (process_retries + process_pending + process_temporal_triggers [S02])

### Переменные окружения

```
# Kommo CRM
KOMMO_DOMAIN=xxx.kommo.com
KOMMO_TOKEN=...

# Wazzup24 WABA
WAZZUP_API_KEY=your_wazzup_api_key_here
WAZZUP_API_URL=https://api.wazzup24.com/v3
WAZZUP_CHANNEL_ID=your_wazzup_channel_id_here
WAZZUP_TEMPLATE_ID=your_wazzup_template_id_here

# Kommo webhook validation
KOMMO_WEBHOOK_SECRET=...

# Telegram alerts
TELEGRAM_BOT_TOKEN=...
TELEGRAM_ALERT_CHAT_ID=...

# Settings
SEND_WINDOW_START=8
SEND_WINDOW_END=22
MAX_RETRY_ATTEMPTS=2
RETRY_INTERVAL_HOURS=24

# Database
DATABASE_PATH=./data/messages.db
```

---

## Ограничения и допущения

- **WABA требует использования одобренных шаблонов** — для первого сообщения только через template. После ответа клиента есть 24 часа на произвольный текст
- Для S02 используются отдельные approved line-specific шаблоны; новый Б4 больше не зависит от shared legacy-шаблона S01.
- SQLite достаточно для ~300 клиентов и ~60 сообщений/день. При росте → PostgreSQL
- Часовой пояс: клиенты в разных городах Германии, но вся страна в одном часовом поясе (CET/CEST). Упрощает логику окна 8–22
- "Нет ответа" определяем по отсутствию входящего сообщения от контакта, а не по статусу "прочитано"

---

## Acceptance Criteria / DoD

- [x] Webhook от Kommo принимается и обрабатывается
- [x] Сообщение отправляется в WhatsApp при смене этапа воронки
- [x] Персонализация: имя клиента, дата термина, customer-facing контекст и line-specific шаблонные переменные
- [x] Отправка только в окне 8:00–22:00, отложенные уходят утром
- [x] Повторная отправка через 24ч, макс 2 раза
- [x] Примечание в Kommo: "сообщение отправлено"
- [x] Алерт в Telegram при ошибке
- [x] Логирование всех событий в SQLite
- [x] Messenger layer позволяет добавить дополнительные каналы (выделение интерфейса при необходимости)
