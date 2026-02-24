# Архитектура: WhatsApp авто-нотификации

**Версия:** 1.0
**Дата:** 2026-02-20
**Статус:** draft

---

## Что делает система

Автоматически отправляет WhatsApp-сообщения клиентам Sternmeister при смене этапа воронки в Kommo:

1. **Первая линия** — клиент должен записаться на термин → напоминание
2. **Вторая линия** — клиент ожидает термин → уведомление о статусе

Повторная отправка через 24ч если нет ответа, максимум 2 раза. Отправка только с 9:00 до 21:00.

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
3. Проверяем время (9:00–21:00) → если вне окна, откладываем в очередь
4. Формируем сообщение (шаблон + персонализация)
5. Отправляем через Wazzup24 WABA
6. Пишем примечание в Kommo: "Сообщение отправлено"
7. Логируем в SQLite

### 2. Retry cron

Запускается раз в час. Проверяет SQLite:
- Сообщения без ответа старше 24ч → повторная отправка (макс 2 раза)
- Отложенные сообщения (вне окна 9–21) → отправка при наступлении окна

### 3. Messenger layer

```python
# messenger/wazzup.py
class WazzupMessenger:
    def send_message(self, phone: str, message_data: MessageData) -> dict: ...

@dataclass
class MessageData:
    line: str           # "first" или "second"
    termin_date: str    # "25.02.2026" (DD.MM.YYYY)
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
| line | TEXT | "first" / "second" |
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

**Воронка "Берётар" (pipeline_id: 12154099):**
- "Принято от первой линии" — status_id: 9386032 (триггер: first line)
- "Принято от первой линии (повторные)" — status_id: 93860327
- "Новый лид" — status_id: 83873491
- "Взято в работу" — status_id: 90367079
- "Недозвон" — status_id: 90367083
- "Контакт установлен" — status_id: 90367087
- "Консультация проведена" — status_id: 95514983
- "Отложенный старт" — status_id: 95514987
- "Термин ДЦ" — status_id: 10093587 (триггер: second line)
- "Закрыто и не реализовано" — status_id: 10093590
- "Терминарий" — status_id: 142
- "Закрыто и не реализовано" — status_id: 143

**Воронка "Госники" (pipeline_id: 10631243):**
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
- `GET /api/v4/contacts/{id}` — получить контакт (телефон, имя)
- `POST /api/v4/contacts/{id}/notes` — записать примечание "сообщение отправлено"
- Авторизация: OAuth 2.0 (long-lived token)

### Wazzup24 WABA

Номер **+49 3046690188** подключён через Wazzup24 (получено 23.02.2026).

**Доступы (получены 23.02):**
- **API-ключ:** сохранён в `.env`
- **channelId:** сохранён в `.env`
- **Тип транспорта:** `wapi` (WhatsApp Business API)
- **Статус:** `active`

**Доступные WABA шаблоны:**
1. **"Напоминание о записи или встрече"** (templateGuid: `38194e93-e926-4826-babe-19032e0bd74c`) — ⭐ рекомендуется
2. **"Уведомление о записи"** (templateGuid: `3b7211aa-6fbd-4b60-bb96-02d7cc837c73`)
3. **"Универсальный шаблон 4"** (templateGuid: `4e049e0c-c404-45ba-b516-5ae932260b19`) — для произвольного текста
4. + ещё 6 одобренных шаблонов (см. README.md)

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
  "templateId": "38194e93-e926-4826-babe-19032e0bd74c",
  "templateValues": ["SternMeister", "термине", "25.02.2026"]
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
- `whatsapp-cron.timer` — cron каждый час (process_retries + process_pending)

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
SEND_WINDOW_START=9
SEND_WINDOW_END=21
MAX_RETRY_ATTEMPTS=2
RETRY_INTERVAL_HOURS=24

# Database
DATABASE_PATH=./data/messages.db
```

---

## Ограничения и допущения

- **WABA требует использования одобренных шаблонов** — для первого сообщения только через template. После ответа клиента есть 24 часа на произвольный текст
- Используем шаблон **"Напоминание о записи или встрече"** (templateGuid: `38194e93-e926-4826-babe-19032e0bd74c`) для обеих линий
- SQLite достаточно для ~300 клиентов и ~60 сообщений/день. При росте → PostgreSQL
- Часовой пояс: клиенты в разных городах Германии, но вся страна в одном часовом поясе (CET/CEST). Упрощает логику окна 9–21
- "Нет ответа" определяем по отсутствию входящего сообщения от контакта, а не по статусу "прочитано"

---

## Acceptance Criteria / DoD

- [x] Webhook от Kommo принимается и обрабатывается
- [x] Сообщение отправляется в WhatsApp при смене этапа воронки
- [x] Персонализация: дата термина (имя клиента не используется — ограничение WABA-шаблона)
- [x] Отправка только в окне 9:00–21:00, отложенные уходят утром
- [x] Повторная отправка через 24ч, макс 2 раза
- [x] Примечание в Kommo: "сообщение отправлено"
- [x] Алерт в Telegram при ошибке
- [x] Логирование всех событий в SQLite
- [x] Messenger layer позволяет добавить дополнительные каналы (выделение интерфейса при необходимости)
