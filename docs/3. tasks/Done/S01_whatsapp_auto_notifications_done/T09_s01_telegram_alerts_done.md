**Дата:** 2026-02-23
**Статус:** done
**Акцептована:** 2026-02-24
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T09 — Telegram алерты

---

## Customer-facing инкремент

При ошибке отправки WhatsApp-сообщения (Wazzup24 недоступен, невалидный номер, Kommo API не отвечает) команда получает мгновенное уведомление в Telegram. Это позволяет быстро реагировать на проблемы.

---

## Scope

### Делаем:
- Реализация `alerts.py` для отправки Telegram-сообщений
- Создание Telegram бота через @BotFather (инструкция; бот создаётся вручную)
- Получение `chat_id` для получения алертов (инструкция)
- Интеграция алертов в webhook handler (T06) и cron (T08)
- Форматирование алертов: тип ошибки, контекст, timestamp

### НЕ делаем:
- Telegram бота для управления системой (только алерты)
- Интеграцию с другими каналами (Slack, Email)
- Dashboard для мониторинга (только push-уведомления)

---

## Результат реализации

### alerts.py

`server/alerts.py` — TelegramAlerter с lazy singleton (`get_alerter()`):

- `send_alert(message, level)` — базовая отправка (ERROR/WARNING/INFO), Markdown форматирование, UTC timestamp
- `alert_messenger_error(phone, error)` — ошибка WhatsApp с PII masking телефона (`_mask_phone`)
- `alert_kommo_error(lead_id, error)` — ошибка Kommo API
- `alert_cron_error(error)` — fatal ошибка cron
- `alert_unexpected_error(error)` — неожиданная ошибка в webhook (catch-all)
- `alert_info(message)` — информационный алерт

Вспомогательные функции:
- `_mask_phone(phone)` — PII masking: `+491234567890` → `+49***7890`
- `_escape_md(text)` — экранирование спецсимволов Markdown v1 (`*`, `_`, `` ` ``, `[`)

Graceful degradation: если `TELEGRAM_BOT_TOKEN` или `TELEGRAM_ALERT_CHAT_ID` не заданы — алерты логируются, но не отправляются, приложение не падает.

### Интеграция в app.py

- `KommoAPIError` → `alert_kommo_error(lead_id, error)`
- `MessengerError` → `alert_messenger_error(phone, error)`
- Телефон не найден → `send_alert(..., level="WARNING")`
- Дата термина не найдена → `send_alert(..., level="WARNING")`
- Unexpected exception (catch-all) → `alert_unexpected_error(error)`

### Интеграция в cron.py

- Retry failure → `alert_messenger_error(phone, error)`
- Pending failure → `alert_messenger_error(phone, error)`
- Fatal cron error → `alert_cron_error(error)`

### Тесты

31 тест в `tests/test_alerts.py` (pytest + freezegun):
- `_mask_phone`: normal, short, edge-case 8 chars
- `_escape_md`: no special chars, asterisks, underscores, backticks, brackets, mixed, empty
- `send_alert`: success, API error, network exception, disabled, WARNING/INFO levels
- `alert_messenger_error`: PII masking, Markdown escaping
- `alert_kommo_error`: lead_id + error text
- `alert_cron_error`: error text
- `alert_unexpected_error`: error text, Markdown escaping
- `alert_info`: INFO level, Markdown escaping
- Webhook integration: Kommo error, no phone (WARNING), no termin (WARNING), messenger error
- Cron integration: fatal error, retry failure, pending failure

---

## Создание Telegram бота

### 1. Создать бота через @BotFather

1. Открыть Telegram, найти @BotFather
2. Отправить `/newbot`
3. Указать имя: `Sternmeister WhatsApp Alerts`
4. Указать username: `sternmeister_whatsapp_bot`
5. Получить токен → записать в `.env` как `TELEGRAM_BOT_TOKEN`

### 2. Получить chat_id

1. Отправить любое сообщение боту
2. Открыть: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Найти `"chat":{"id": 123456789, ...}`
4. Записать `chat_id` → в `.env` как `TELEGRAM_ALERT_CHAT_ID`

---

## Критерии приёмки

- [ ] Telegram бот создан через @BotFather, токен получен (ручной шаг, делается при деплое T10)
- [ ] `chat_id` получен и записан в `.env` (ручной шаг, делается при деплое T10)
- [x] `alerts.py` отправляет сообщения в Telegram корректно
- [x] Форматирование работает: emoji, уровень, timestamp (UTC)
- [x] `alert_messenger_error()` отправляет алерт с замаскированным телефоном и ошибкой
- [x] `alert_kommo_error()` отправляет алерт с lead_id и ошибкой
- [x] `alert_cron_error()` отправляет алерт при ошибке cron
- [x] `alert_unexpected_error()` отправляет алерт с экранированным текстом ошибки
- [x] Интеграция с webhook handler: при ошибке Wazzup24/Kommo/unexpected → алерт в Telegram
- [x] Интеграция с cron: при ошибке retry/pending/fatal → алерт в Telegram
- [x] Если токен не настроен → логирование, но не падает (graceful degradation)
- [x] PII masking телефонов в алертах
- [x] Markdown escaping во всех методах с динамическим контентом

---

## Зависимости

**Требует:** T06 (webhook handler), T08 (cron)
**Блокирует:** —
**Можно параллельно с:** T07
