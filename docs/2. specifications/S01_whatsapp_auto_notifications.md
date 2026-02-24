# Функциональная спецификация: WhatsApp Auto-notifications

**ID:** S01
**Статус:** draft
**Версия:** 2.1
**Дата:** 2026-02-23
**Автор:** Иван Серебренников

---

## Цель

Реализовать автоматическую систему WhatsApp-уведомлений для клиентов Sternmeister при изменении этапа воронки в Kommo CRM. Система должна отправлять персонализированные напоминания о записи на термин (первая линия) и уведомления об ожидании термина (вторая линия) с автоматическими повторами при отсутствии ответа. Это позволит снизить ручную нагрузку на менеджеров и увеличить конверсию записей на консультации.

---

## Scope

### Входит:

- Webhook-интеграция с Kommo CRM для получения событий смены этапа воронки
- Python-сервис для обработки webhook и отправки сообщений
- Messenger layer с Wazzup24 WABA (архитектура заложена для будущих каналов)
- Персонализация сообщений: имя клиента, дата термина
- Ограничение времени отправки: 9:00–21:00 (CET/CEST)
- Автоматические повторы: через 24ч если нет ответа, максимум 2 повтора
- Логирование всех отправок в SQLite
- Обратная запись в Kommo CRM: примечание "сообщение отправлено"
- Telegram-алерты при ошибках отправки
- Cron-задача для обработки повторов и отложенных сообщений

### Не входит / Out of scope:

- Интеграция с другими CRM (только Kommo)
- SMS как fallback канал (возможно в будущем)
- A/B тестирование текстов сообщений
- Веб-интерфейс для управления рассылками
- Дашборд с аналитикой доставляемости
- Поддержка нескольких языков (только русский/немецкий)
- Интеграция с календарём для автоматического подбора времени

---

## Архитектура и структура

### Компоненты системы

```
┌──────────┐    webhook     ┌──────────────┐    REST API  ┌───────────┐
│  Kommo   │ ──────────────→│ Python-сервис │─────────────→│ Wazzup24  │
│ (воронка)│                │              │              │   WABA    │
└──────────┘                │  ┌────────┐  │              └───────────┘
                            │  │ SQLite │  │
                            │  │ (логи) │  │
                            │  └────────┘  │
                            │              │
                            │  ┌────────┐  │──→ Kommo API (примечание)
                            │  │  Cron  │  │──→ Telegram (алерты)
                            │  │(повтор)│  │
                            └──────────────┘
```

### Структура проекта

```
server/
├── app.py              # FastAPI, webhook handler
├── cron.py             # Retry + отложенные сообщения
├── messenger/
│   ├── __init__.py     # Экспорт get_messenger, MessageData, MessengerError
│   └── wazzup.py       # WazzupMessenger (Wazzup24 WABA)
├── kommo.py            # Kommo CRM API клиент
├── alerts.py           # Telegram-алерты
├── db.py               # SQLite operations
├── config.py           # Переменные окружения
├── requirements.txt
├── Dockerfile
└── .env.example
```

### Технологический стек

| Слой | Технология | Назначение |
|------|-----------|------------|
| CRM | Kommo CRM | Источник данных, воронки "Госники" и "Бератер" |
| Триггер | Kommo Webhooks | POST-запрос при смене этапа воронки |
| Сервис | Python (FastAPI) | Приём webhooks, бизнес-логика, cron |
| Мессенджер | Wazzup24 WABA | Отправка через официальный WhatsApp Business API (номер +49 3046690188, WABA-шаблоны) |
| Хранение | SQLite | Лог отправок, очередь повторов |
| Алерты | Telegram Bot API | Уведомления при ошибках |
| Инфраструктура | Hetzner VPS + Docker | 65.108.154.202, Ubuntu 24.04, Docker 29.2.1 |

---

## Модели данных / БД

### SQLite: таблица `messages`

**Назначение:** хранение истории отправленных сообщений и очереди повторов.

**Поля:**

| Поле | Тип | Описание |
|------|-----|----------|
| `id` | INTEGER PRIMARY KEY | Уникальный идентификатор записи |
| `kommo_lead_id` | INTEGER NOT NULL | ID сделки (лида) в Kommo CRM |
| `kommo_contact_id` | INTEGER NOT NULL | ID контакта в Kommo CRM |
| `phone` | TEXT NOT NULL | Номер телефона (формат: +491234567890) |
| `line` | TEXT NOT NULL | "first" (первая линия) / "second" (вторая линия) |
| `termin_date` | TEXT NOT NULL | Дата термина (DD.MM.YYYY) |
| `message_text` | TEXT NOT NULL | Текст отправленного сообщения |
| `status` | TEXT NOT NULL | "pending" / "sent" / "delivered" / "failed" |
| `attempts` | INTEGER DEFAULT 1 | Количество попыток отправки (макс 3: первая + 2 повтора) |
| `created_at` | DATETIME NOT NULL | Время создания записи |
| `sent_at` | DATETIME | Время последней отправки |
| `next_retry_at` | DATETIME | Время следующей попытки (для повторов) |
| `messenger_id` | TEXT | ID сообщения от Wazzup24 |
| `messenger_backend` | TEXT NOT NULL | "wazzup" (заложена поддержка других каналов) |

**Индексы:**
- `idx_status_next_retry` на `(status, next_retry_at)` — для cron-задачи повторов
- `idx_kommo_contact` на `kommo_contact_id` — для поиска истории по контакту
- `idx_dedup` на `(kommo_lead_id, line, created_at)` — для дедупликации webhook

---

## API Endpoints

### 1. POST /webhook/kommo

**Назначение:** Приём webhook от Kommo CRM при смене этапа воронки.

**Headers:**
```
Content-Type: application/json
X-Kommo-Signature: <HMAC подпись> (опционально, для валидации)
```

**Body (пример):**
```json
{
  "leads": {
    "status": [
      {
        "id": 12345,
        "status_id": 67890,
        "pipeline_id": 111,
        "old_status_id": 67889
      }
    ]
  },
  "account": {
    "id": "xxxxx",
    "subdomain": "sternmeister"
  }
}
```

**Логика обработки:**
1. Валидация payload (проверка структуры)
2. Определение воронки и этапа (первая/вторая линия)
3. Получение контакта через Kommo API: `GET /api/v4/leads/{id}?with=contacts`
4. Извлечение: имя, телефон, дата термина
5. Проверка времени (9:00–21:00):
   - Если в окне → отправка сразу
   - Если вне окна → `status=pending`, `next_retry_at=9:00 следующего дня`
6. Формирование сообщения через messenger layer
7. Отправка сообщения через Wazzup24
8. Запись в SQLite
9. Запись примечания в Kommo: `POST /api/v4/leads/{id}/notes`

**Response 200:**
```json
{
  "status": "ok",
  "message_id": "msg_123456"
}
```

**Response 400:** Invalid payload
**Response 500:** Internal error (+ Telegram alert)

---

### 2. Исходящие запросы к Kommo API

**Базовый URL:** `https://sternmeister.kommo.com/api/v4`

**Авторизация:** `Authorization: Bearer {KOMMO_TOKEN}` (OAuth 2.0 long-lived token)

#### GET /api/v4/leads/{id}

**Назначение:** Получить информацию о сделке и контакте.

**Query параметры:**
- `with=contacts` — включить связанные контакты

**Response 200:**
```json
{
  "_embedded": {
    "leads": [
      {
        "id": 12345,
        "name": "Иван Иванов",
        "pipeline_id": 111,
        "status_id": 67890,
        "custom_fields_values": [
          {
            "field_id": 123,
            "field_name": "Дата Термина",
            "values": [{"value": "2026-02-25 14:00"}]
          }
        ],
        "_embedded": {
          "contacts": [
            {
              "id": 98765,
              "custom_fields_values": [
                {
                  "field_code": "PHONE",
                  "values": [{"value": "+491234567890"}]
                }
              ]
            }
          ]
        }
      }
    ]
  }
}
```

**Примечание:** Значения `pipeline_id`, `status_id`, `field_id` — реальные ID из Kommo CRM (T01).

#### POST /api/v4/leads/{id}/notes

**Назначение:** Добавить примечание к сделке "Сообщение отправлено".

**Body:**
```json
{
  "note_type": "common",
  "params": {
    "text": "WhatsApp сообщение отправлено: {дата/время}"
  }
}
```

**Response 200:**
```json
{
  "_embedded": {
    "notes": [{"id": 456789}]
  }
}
```

---

### 3. Исходящие запросы к Wazzup24 API

**Базовый URL:** `https://api.wazzup24.com/v3`

**Авторизация:** `Authorization: Bearer {WAZZUP_API_KEY}`

#### POST /v3/message

**Назначение:** Отправка WhatsApp-сообщения через WABA шаблон.

**Body:**
```json
{
  "channelId": "1b689b43-f846-41a6-bedc-cae01209fb8b",
  "chatId": "491234567890",
  "chatType": "whatsapp",
  "text": "@template: 38194e93-e926-4826-babe-19032e0bd74c { [[SternMeister]]; [[термине]]; [[25.02 в 14:00]] }"
}
```

**Используемый шаблон:**
- **templateGuid:** `38194e93-e926-4826-babe-19032e0bd74c`
- **Название:** "Напоминание о записи или встрече"
- **Текст:** "Здравствуйте. Это {{1}}. Напоминаем о {{2}} в {{3}}. Скажите, все в силе?"
- **Кнопки:** "Да, буду вовремя" / "Нет, не могу прийти" (QUICK_REPLY)

**Response 200:**
```json
{
  "messageId": "waba_msg_123456"
}
```

---

## Авторизация и безопасность

### Kommo CRM

- **Метод авторизации:** OAuth 2.0 (long-lived access token)
- **Хранение токена:** переменная окружения `KOMMO_TOKEN`
- **Валидация webhook:** опциональная проверка `X-Kommo-Signature` (HMAC SHA256)
- **Rate limiting:** не требуется (входящие webhook)

### Wazzup24 WABA

- **Метод авторизации:** Bearer token в заголовке
- **Хранение:** `WAZZUP_API_KEY` в .env
- **Ограничения:** официальный WABA, без жёстких лимитов

### Telegram Alerts

- **Метод авторизации:** Bot token
- **Хранение:** `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALERT_CHAT_ID`

### Общие меры безопасности

- ✅ Все секреты в `.env`, не коммитятся в git (`.gitignore`)
- ✅ Валидация всех входящих данных от webhook
- ✅ Sanitization номеров телефонов перед отправкой
- ✅ Логирование всех ошибок без раскрытия токенов
- ✅ HTTPS для всех внешних запросов
- ⬜ Rate limiting для webhook endpoint (TODO T11, пока защита через secret-in-URL)

---

## Логика и алгоритмы

### 1. Обработка webhook от Kommo

```python
def handle_kommo_webhook(payload):
    # 1. Валидация
    if not validate_payload(payload):
        return 400, "Invalid payload"

    # 2. Извлечение данных
    lead_id = payload['leads']['status'][0]['id']
    new_status_id = payload['leads']['status'][0]['status_id']

    # 3. Определение линии
    line = determine_line(new_status_id)
    if line is None:
        return 200, "Status not relevant"  # Не триггерный этап

    # 4. Получение контакта из Kommo
    contact = kommo_api.get_lead_with_contacts(lead_id)
    name = contact['name']
    phone = extract_phone(contact)
    termin_date = extract_termin_date(contact)

    # 5. Формирование сообщения
    message_text = format_message(name, termin_date, line)

    # 6. Проверка времени
    if not is_in_send_window():
        # Отложить до 9:00 следующего дня
        next_retry_at = get_next_send_window_start()
        db.create_message(
            kommo_contact_id=contact['id'],
            phone=phone,
            line=line,
            message_text=message_text,
            status="pending",
            next_retry_at=next_retry_at
        )
        return 200, "Scheduled for tomorrow"

    # 7. Отправка
    messenger = get_messenger()  # wazzup
    result = messenger.send_message(phone, message_text)

    # 8. Логирование
    db.create_message(
        kommo_contact_id=contact['id'],
        phone=phone,
        line=line,
        message_text=message_text,
        status="sent",
        messenger_id=result['message_id'],
        messenger_backend="wazzup",
        sent_at=datetime.now(),
        next_retry_at=datetime.now() + timedelta(hours=24)
    )

    # 9. Примечание в Kommo
    kommo_api.add_note(lead_id, f"WhatsApp отправлено: {datetime.now()}")

    return 200, {"message_id": result['message_id']}
```

### 2. Cron-задача для повторов

**Частота:** каждый час

```python
def retry_cron():
    now = datetime.now()

    # 1. Найти сообщения для повтора
    messages = db.get_messages_for_retry(
        status="sent",
        next_retry_at__lte=now,
        attempts__lt=3
    )

    for msg in messages:
        # 2. Проверка окна времени
        if not is_in_send_window():
            continue

        # 3. Проверка: есть ли ответ от клиента?
        # (опционально: интеграция с Wazzup24 webhook для входящих)
        # Пока упрощение: повтор всегда

        # 4. Отправка
        messenger = get_messenger_by_type(msg.messenger_backend)
        try:
            result = messenger.send_message(msg.phone, msg.message_text)

            db.update_message(msg.id,
                status="sent",
                attempts=msg.attempts + 1,
                sent_at=now,
                next_retry_at=now + timedelta(hours=24),
                messenger_id=result['message_id']
            )
        except Exception as e:
            db.update_message(msg.id, status="failed")
            telegram_alert(f"Retry failed: {msg.id}, error: {e}")

    # 5. Обработка отложенных (pending)
    pending = db.get_messages(status="pending", next_retry_at__lte=now)
    for msg in pending:
        # Аналогичная логика отправки
        ...
```

### 3. Определение линии по status_id

```python
PIPELINE_CONFIG = {
    12154099: {  # Воронка "Берётар"
        9386032: "first",    # "Принято от первой линии"
        10093587: "second",  # "Термин ДЦ"
    },
    10631243: {  # Воронка "Госники"
        8152349: "first",    # "Принято от первой линии"
    },
}

def determine_line(pipeline_id: int, status_id: int) -> str | None:
    statuses = PIPELINE_CONFIG.get(pipeline_id)
    if statuses is None:
        return None
    return statuses.get(status_id)  # "first", "second" или None
```

---

## Acceptance Criteria / DoD

- [x] Webhook от Kommo принимается корректно (валидация payload)
- [x] Сообщение отправляется в WhatsApp при смене этапа воронки "Бератер" (первая/вторая линия)
- [x] Персонализация работает: дата термина (имя клиента не используется — ограничение WABA-шаблона)
- [x] Отправка происходит только в окне 9:00–21:00 (CET/CEST)
- [x] Сообщения вне окна откладываются до 9:00 следующего дня
- [x] Повторная отправка работает: через 24ч при отсутствии ответа, максимум 2 повтора
- [x] Примечание "WhatsApp сообщение отправлено" записывается в Kommo
- [x] Telegram-алерт отправляется при ошибке (Wazzup24 недоступен, невалидный номер и т.д.)
- [x] Все события логируются в SQLite (status, attempts, timestamps)
- [x] Messenger layer позволяет добавить другие каналы в будущем (выделить интерфейс при необходимости)
- [x] Wazzup24 использует WABA-шаблон "Напоминание о записи или встрече"
- [x] Cron-задача запускается каждый час и обрабатывает повторы + отложенные сообщения
- [x] Проект запускается в Docker на сервере Hetzner (65.108.154.202)
- [x] `.env.example` содержит все необходимые переменные окружения

---

## Тест-план

### Юнит-тесты

- [ ] `test_validate_payload()` — валидация webhook payload (valid/invalid)
- [ ] `test_determine_line()` — определение линии по status_id (first/second/None)
- [ ] `test_extract_phone()` — извлечение телефона из контакта Kommo
- [ ] `test_format_message()` — форматирование текста сообщения
- [ ] `test_is_in_send_window()` — проверка времени (9-21, вне окна)
- [ ] `test_get_next_send_window_start()` — расчёт следующего окна

### Интеграционные тесты

- [ ] Webhook от Kommo → запись в SQLite → отправка через Wazzup24 WABA
- [ ] Webhook вне окна 9-21 → `status=pending`, `next_retry_at` установлен корректно
- [ ] Cron-задача обрабатывает повторы: `attempts < 3`, `next_retry_at` обновляется
- [ ] Messenger layer позволяет добавить другие каналы (без BaseMessenger — YAGNI, один backend)
- [ ] Kommo API: создание примечания после отправки сообщения

### Тестирование в реальной среде

1. **Создать тестовый контакт в Kommo:**
   - Имя: "Тест Иван"
   - Телефон: +996501354144
   - Воронка: "Бератер"

2. **Переместить контакт по этапам:**
   - Этап "Принято от первой линии" → проверить получение сообщения в WhatsApp
   - Проверить примечание в Kommo: "WhatsApp сообщение отправлено"

3. **Тест повторной отправки:**
   - Не отвечать на сообщение
   - Изменить `RETRY_INTERVAL_HOURS=0.1` (6 минут для теста)
   - Подождать 6 минут → проверить повторную отправку
   - Проверить в SQLite: `attempts=2`

4. **Тест окна времени:**
   - Изменить системное время на 22:00
   - Переместить контакт → проверить `status=pending` в SQLite
   - Вернуть время на 9:00 → запустить cron → проверить отправку

5. **Проверка WABA-шаблона:**
   - Отправка через Wazzup24 с шаблоном успешна
   - Кнопки "Да, буду вовремя" / "Нет, не могу прийти" отображаются

### Негативные сценарии

- [ ] Невалидный payload от Kommo → 400 Bad Request
- [ ] Номер телефона отсутствует в контакте → Telegram alert, status=failed
- [ ] Wazzup24 недоступен (timeout) → Telegram alert, status=failed, повтор через 24ч
- [ ] Kommo API возвращает 500 → Telegram alert, логирование ошибки

---

## Зависимости и интеграции

### Требуется до начала разработки:

- [x] Доступ к Kommo CRM (логин, OAuth токен)
- [x] Wazzup24 API (ключ, channelId получены)
- [x] Сервер Hetzner (65.108.154.202, SSH-доступ настроен)
- [x] **Реальные ID из Kommo CRM (T01):**
  - [x] `pipeline_id` для воронок "Бератер" и "Госники"
  - [x] `status_id` для этапов "Принято от первой линии", "Термин ДЦ назначен"
  - [x] `field_id` для кастомного поля "Дата Термина"
  - [ ] Тестовый контакт для проверки (имя, телефон +996501354144)
- [ ] Telegram Bot для алертов (token, chat_id)
- [ ] Webhook URL для Kommo (публичный URL, настроить в Kommo UI)

### Декомпозиция на задачи:

Подробная декомпозиция с customer-facing инкрементами, критериями приёмки и планом тестирования:

**→ [S01_whatsapp_auto_notifications_TASKS_PROPOSAL.md](S01_whatsapp_auto_notifications_TASKS_PROPOSAL.md)**

**Краткий обзор задач:**
- **T01** ✅ — Сбор конфигурации из Kommo CRM (pipeline_id, status_id, field_id)
- **T02** ✅ — Scaffold проекта и базовая инфраструктура
- **T03** ✅ — SQLite модель и логирование
- **T04** ✅ — Kommo API клиент (get_lead, extract_phone, add_note)
- **T05** ✅ — Отправка WhatsApp через Wazzup24 WABA (WazzupMessenger)
- **T06** ✅ — Webhook handler для Kommo (POST /webhook/kommo)
- **T07** ✅ — Логика окна времени и отложенные сообщения (9:00-21:00 CET/CEST)
- **T08** ✅ — Cron-задача для повторов (через 24ч, максимум 2 раза)
- **T09** ✅ — Telegram алерты при ошибках (alerts.py, интеграция в app.py/cron.py, 31 тест)
- **T10** — Деплой на Hetzner VPS
- **T11** — Интеграционное тестирование и доработки

---

## Риски и ограничения

### Риски

1. **Задержка webhook от Kommo**
   - Риск: Kommo может отправлять webhook с задержкой
   - Митигация: логирование времени получения, повторы через 24ч нивелируют задержку

3. **Отсутствие ответа от клиента**
   - Риск: сложно определить "нет ответа" (клиент может прочитать, но не ответить)
   - Митигация: упрощение — повтор всегда через 24ч, макс 2 раза

4. **WABA-шаблон отклонён/удалён**
   - Риск: шаблон может быть отклонён модераторами WhatsApp
   - Митигация: использовать универсальный шаблон как fallback

### Ограничения

- **SQLite:** достаточно для ~300 клиентов и ~60 сообщений/день. При росте → PostgreSQL
- **Часовой пояс:** вся Германия в CET/CEST, упрощает логику окна 9-21
- **WABA-шаблоны:** первое сообщение только через template. После ответа клиента — 24ч на произвольный текст
- **"Нет ответа":** определяется по отсутствию входящего сообщения (требует webhook от Wazzup24). Пока упрощение: повтор всегда
- **Воронки:** реализация только для "Бератер" и "Госники", другие воронки не поддерживаются
- **Тестирование:** прямо на Wazzup24 WABA, используя тестовых пользователей в воронке Kommo

---

## Связанные документы

- **HANDOFF:** [docs/HANDOFF.md](../HANDOFF.md) — статус проекта, доступы, активные задачи
- **Архитектура:** [docs/architecture.md](../architecture.md) — техническая архитектура решения
- **Гайды:**
  - [docs/4. guides/security_checklist.md](../4.%20guides/security_checklist.md) — чеклист безопасности
  - [docs/4. guides/task_decomposition_guide.md](../4.%20guides/task_decomposition_guide.md) — декомпозиция на задачи

---

## История изменений

### v2.6 (2026-02-24)
- T09 акцептована: Telegram алерты (alerts.py) — TelegramAlerter с lazy singleton, PII masking, Markdown escaping, graceful degradation
- Интеграция в app.py (KommoAPIError, MessengerError, no phone/termin warnings, catch-all) и cron.py (retry/pending failures, fatal error)
- Ревью-фиксы T09: Markdown injection в catch-all (`alert_unexpected_error` вместо raw `send_alert`), `_escape_md` в `alert_info`, интеграционные тесты для cron retry/pending alert
- 31 тест (pytest + freezegun): unit + webhook/cron integration

### v2.5 (2026-02-24)
- T08 акцептована: Cron-задача (cron.py) — process_retries() (sent/failed, attempts < 3), process_pending() (отложенные), Kommo add_note для retry/pending, 24 теста
- Ревью-фиксы T08: `except Exception as exc` (был не привязан), добавлен Kommo add_note для pending/retry, обновлена документация (устаревший псевдокод → ссылка, green_api → wazzup, scope status=sent → sent/failed, ExecStart → python -m)

### v2.4 (2026-02-24)
- T06 акцептована: Webhook handler (app.py) — POST /webhook/kommo, form-data парсер, dedup, 61 тест
- T07 акцептована: Send window logic (utils.py) — is_in_send_window, get_next_send_window_start (DST-safe, zoneinfo)
- Ревью-фиксы T06: attempts=0 для pending, консистентный response format, TypeError в парсере, termin_date fallback тесты

### v2.3 (2026-02-24)
- T05 акцептована: WazzupMessenger (messenger/wazzup.py) — send_message, build_message_text, MessageData, MessengerError
- Убран base.py из file tree (YAGNI), Flask→FastAPI в стеке, обновлён DoD

### v2.2 (2026-02-24)
- T04 акцептована: Kommo API клиент (kommo.py) — KommoClient, get_lead_with_contacts, extract_phone, extract_termin_date, add_note

### v2.1 (2026-02-23)
- Убран Green API (используется только Wazzup24 WABA)
- Упрощена архитектура messenger layer (без BaseMessenger — YAGNI, единственная реализация WazzupMessenger)
- Обновлена декомпозиция задач: T02 сразу реализует Wazzup24, удалена старая T08 (переключение на Wazzup)
- Обновлены тесты и критерии приёмки
- Тестирование напрямую на Wazzup24 с тестовыми пользователями

### v2.0 (2026-02-23)
- Полная реструктуризация в соответствии с `specifications_guide.md`
- Добавлены секции: ID, статус, версия, Scope, API Endpoints, Security, DoD, Тест-план, Риски
- Удалены секции: Оплата, Сроки, Предложенные варианты (перенесены в контекст проекта)
- Детализированы модели данных (SQLite), API endpoints (Kommo, Wazzup24)
- Добавлен подробный тест-план (юнит, интеграционные, негативные сценарии)

### v1.0 (2026-02-20)
- Первая версия спецификации
- Описание задачи, варианты реализации, доступы
