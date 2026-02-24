# HANDOFF — WhatsApp Auto-notifications (Sternmeister)

**Последнее обновление:** 24.02.2026

---

## Текущий статус

Проект **задеплоен в продакшн**. T01-T10 завершены. Сервис работает на Hetzner (65.108.154.202) через ngrok (`https://shternmeister.ngrok.pro`). Полный цикл: webhook → Kommo API → проверка окна 9-21 → Wazzup24 → SQLite + cron retry/pending + Telegram алерты при ошибках. Webhook secret validation активна. Следующая задача: T11 (интеграционное тестирование + настройка webhook в Kommo UI).

---

## Активная спецификация

### S01: WhatsApp Auto-notifications (draft)

Автоматические WhatsApp-уведомления для клиентов Sternmeister при смене этапа воронки в Kommo CRM.

Файл: [S01_whatsapp_auto_notifications.md](2.%20specifications/S01_whatsapp_auto_notifications.md)

---

## Задачи S01

**Всего задач:** 10 (T01-T11, без пропусков)
**Текущая задача:** T11 — Интеграционное тестирование и доработки

### Фаза 1: Foundation (последовательно)

**T01** — Сбор конфигурации из Kommo CRM
- **Статус:** ✅ done
- **Файл:** [T01_s01_kommo_config_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T01_s01_kommo_config_done.md)
- **Результат:** Pipeline/Status/Field ID задокументированы в [kommo_config.md](kommo_config.md), API токен в `.env`

**T02** — Scaffold проекта и базовая инфраструктура
- **Статус:** ✅ done
- **Файл:** [T02_s01_scaffold_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T02_s01_scaffold_done.md)
- **Результат:** FastAPI app, config.py, Dockerfile, .env.example, .gitignore, .dockerignore

**T03** — SQLite модель и логирование
- **Статус:** ✅ done
- **Файл:** [T03_s01_sqlite_model_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T03_s01_sqlite_model_done.md)
- **Результат:** db.py с init_db(), create_message(), update_message(), get_messages_for_retry(), get_pending_messages()

### Фаза 2: Core (последовательно T04→T06, затем параллельные ветки)

**T04** — Kommo API клиент
- **Статус:** ✅ done
- **Файл:** [T04_s01_kommo_api_client_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T04_s01_kommo_api_client_done.md)
- **Результат:** kommo.py — KommoClient с get_lead_with_contacts(), get_contact(), extract_phone(), extract_termin_date(), add_note(); retry 429/5xx, lazy init, нормализация телефонов

**T05** — Отправка WhatsApp через Wazzup24 WABA
- **Статус:** ✅ done
- **Файл:** [T05_s01_wazzup_messenger_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T05_s01_wazzup_messenger_done.md)
- **Результат:** messenger/wazzup.py — WazzupMessenger с send_message(), build_message_text(), MessageData, MessengerError; retry 429/5xx, PII masking, lazy singleton

**T06** — Webhook handler для Kommo
- **Статус:** ✅ done
- **Файл:** [T06_s01_webhook_handler_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T06_s01_webhook_handler_done.md)
- **Результат:** app.py — POST /webhook/kommo: парсинг form-data (PHP bracket notation) и JSON, determine_line, dedup, Kommo API → extract phone/termin → build message → send → add note → SQLite. utils.py — parse_bracket_form(). 61 тест (pytest + freezegun)

**T07** — Логика окна времени и отложенные сообщения
- **Статус:** ✅ done
- **Файл:** [T07_s01_send_window_logic_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T07_s01_send_window_logic_done.md)
- **Результат:** utils.py — is_in_send_window(), get_next_send_window_start() (DST-safe, zoneinfo). Webhook вне 9-21 → status=pending. Валидация SEND_WINDOW_START/END. Тесты CET/CEST/DST transitions

### Фаза 3: Features (параллельные ветки после T06)

**T08** — Cron-задача для повторов
- **Статус:** ✅ done
- **Файл:** [T08_s01_cron_retries_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T08_s01_cron_retries_done.md)
- **Результат:** cron.py — process_retries() (sent/failed, attempts < 3, next_retry_at <= now), process_pending() (отложенные из webhook), Kommo add_note для retry/pending, 24 теста (pytest + freezegun)

**T09** — Telegram алерты
- **Статус:** ✅ done
- **Файл:** [T09_s01_telegram_alerts_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T09_s01_telegram_alerts_done.md)
- **Результат:** alerts.py — TelegramAlerter с lazy singleton, send_alert, alert_messenger_error (PII masking), alert_kommo_error, alert_cron_error, alert_unexpected_error, alert_info; Markdown escaping; graceful degradation. Интеграция в app.py и cron.py. 31 тест (pytest + freezegun)

### Фаза 4: Production

**T10** — Деплой на Hetzner и настройка webhook
- **Статус:** ✅ done
- **Файл:** [T10_s01_deploy_done.md](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/T10_s01_deploy_done.md)
- **Результат:** Docker на Hetzner (UID 999, HEALTHCHECK, log rotation), ngrok systemd service, webhook secret validation (hmac.compare_digest), --no-access-log (prevent secret leak), systemd cron timer, enhanced /health endpoint. Deploy doc: rsync, .env management, ngrok, systemd units, test commands

**T11** — Интеграционное тестирование и доработки
- **Статус:** draft
- **Файл:** [T11_s01_integration_testing.md](3.%20tasks/S01_whatsapp_auto_notifications/T11_s01_integration_testing.md)
- **Требует:** T10
- **Инкремент:** E2E тестирование на продакшне

---

## Правила разработки

- **Сборка и тестирование — ТОЛЬКО через Docker.** Не запускать локально (`python server/app.py` и т.д.). На системе Python 3.7, код требует 3.11+. Всегда: `docker build && docker run`.
- Webhook handler (`def`, не `async def`) — чтобы не блокировать event loop FastAPI с синхронным SQLite.

---

## Доступы и инфраструктура

### Kommo CRM
- [x] Логин к CRM — доступ получен
- [x] API long-lived token — сохранён в `.env`
- [x] Pipeline ID и Status ID — задокументированы в [kommo_config.md](kommo_config.md)
- [x] Custom Fields ID — получены field_id для всех необходимых полей

### Wazzup24 WABA (продакшн)
- [x] API-ключ — сохранён в `.env`
- [x] Channel ID — сохранён в `.env`
- [x] Номер: +49 3046690188
- [x] WABA шаблоны: 9 одобренных доступны

### Инфраструктура
- [x] Сервер: Hetzner 65.108.154.202
- [x] SSH-доступ настроен
- [x] Docker 29.2.1

### Telegram Alerts
- [ ] Bot token для алертов
- [ ] Chat ID для уведомлений

---

## Внешние зависимости

- [x] Доступ к Kommo CRM
- [x] Webhook URL для Kommo: `https://shternmeister.ngrok.pro/webhook/kommo?secret=...`
- [ ] Webhook настроен в Kommo UI (ожидает T11)
- [ ] Telegram бот для алертов (опционально)

---

## Связанные документы

- [architecture.md](architecture.md) — техническая архитектура
- [S01_whatsapp_auto_notifications.md](2.%20specifications/S01_whatsapp_auto_notifications.md) — спецификация задачи
- [Гайд: конвенции документации](4.%20guides/doc_conventions.md)
- [Гайд: декомпозиция на задачи](4.%20guides/task_decomposition_guide.md)
- [Security Checklist](4.%20guides/security_checklist.md)

---

## История изменений

### 2026-02-24 — T10 акцептована
- Деплой на Hetzner: Docker container, ngrok systemd service, systemd cron timer
- Webhook secret validation (hmac.compare_digest, secret-in-URL)
- Docker HEALTHCHECK (60s interval, urllib.request)
- Enhanced /health endpoint: server_time_utc, server_time_berlin, in_window
- --no-access-log в uvicorn (предотвращает утечку webhook secret в логи)
- Startup warning при отсутствии KOMMO_WEBHOOK_SECRET
- Ревью-фиксы: process_retries обновляет next_retry_at при ошибке (был баг — агрессивный 1h re-retry), исправлена формулировка HEALTHCHECK (мониторинг, не auto-restart), S01 spec: rate limiting→TODO, добавлен termin_date и idx_dedup в schema
- Webhook URL: `https://shternmeister.ngrok.pro/webhook/kommo?secret=...`

### 2026-02-24 — T09 акцептована
- Telegram алерты (alerts.py): TelegramAlerter с lazy singleton, PII masking, Markdown escaping, graceful degradation
- Интеграция в app.py: KommoAPIError, MessengerError, no phone/termin warnings, catch-all (alert_unexpected_error)
- Интеграция в cron.py: retry/pending individual failure alerts, fatal error alert
- Фиксы по ревью: Markdown injection в catch-all (send_alert → alert_unexpected_error), _escape_md в alert_info, доп. интеграционные тесты для cron
- 31 тест: unit (_mask_phone, _escape_md, send_alert, все helper-методы) + integration (webhook/cron alert calls)

### 2026-02-24 — T08 акцептована
- Cron-задача (cron.py): process_retries() для sent/failed сообщений, process_pending() для отложенных
- Kommo add_note при успешной retry/pending отправке (не было в исходной реализации — добавлено по ревью)
- Фиксы по ревью: `except Exception as exc` (exc не был привязан), обновлена документация (устаревший псевдокод, green_api→wazzup, scope, ExecStart)
- 24 теста (pytest + freezegun): retry lifecycle, pending lifecycle, Kommo note, error handling

### 2026-02-24 — Ревью и фиксы T06
- `attempts=0` для pending-сообщений (не отправленных — готовит для T08 cron)
- Консистентный формат ответа: всегда `{"status":"ok","results":[...]}` (убрана развилка single/multiple)
- TypeError добавлен в except-clause парсера form-data (защита от inconsistent nesting)
- `json.loads(body)` вместо `request.json()` (убрано двойное чтение body)
- Новые тесты: termin_date fallback по 3 полям (4 теста), batch mixed results (2 теста), malformed form/JSON body (2 теста)
- Документация: acceptance criteria отмечены [x], termin_date добавлен в architecture.md, 61 тест
- Обновлены все существующие тесты под новый формат ответа

### 2026-02-24 — T06, T07 акцептованы
- T06: Webhook handler (app.py) — полный цикл обработки Kommo webhook, form-data парсер, dedup, 61 тест
- T07: Send window logic (utils.py) — is_in_send_window(), get_next_send_window_start() с DST-safe вычислением (zoneinfo вместо pytz), тесты CET/CEST/DST
- Фиксы по ревью T07: обновлена документация задачи (зависимости, zoneinfo, убраны ненужные format_* функции), добавлены тесты midnight/early morning, валидация SEND_WINDOW_START/END в config.py, RETRY_INTERVAL_HOURS → float
- Убран дубликат separator в test_webhook.py

### 2026-02-24 — T05 акцептована
- WazzupMessenger (messenger/wazzup.py) реализован и прошёл код-ревью
- Фиксы по ревью: добавлен build_message_text() и message_text в return dict, elif chain в error handling, комментарий о non-retry network errors, переименован WAZZUP_TEMPLATE_GUID → WAZZUP_TEMPLATE_ID
- Docs sync: убран base.py из file trees, Flask→FastAPI, обновлён DoD в S01 spec (BaseMessenger → YAGNI)
- Удалён неиспользуемый python-dateutil из requirements.txt

### 2026-02-24 — T04 акцептована
- Kommo API клиент (kommo.py) реализован и прошёл код-ревью
- Фиксы по ревью: 429 retry из JSON body, extract_termin_date возвращает None при ошибке, JSONDecodeError handling, нормализация локальных немецких номеров (+0→+49), удалён мёртвый код

### 2026-02-23 — Синхронизация нумерации задач
- HANDOFF приведён к нумерации файлов задач (T01-T11, 10 задач)
- Убрана двойная нумерация и таблица маппинга
- T01, T02, T03 отмечены как done

### 2026-02-23 — Код-ревью и фиксы scaffold
- Убраны реальные секреты из `.env.example`, HANDOFF, architecture.md
- Исправлен Dockerfile (убрана копия .env.example)
- Исправлен .gitignore (не скрывает .env.example)
- Добавлен PIPELINE_CONFIG, FIELD_IDS, determine_line() в config.py
- Реализован db.py (init_db, CRUD, индексы)
- Зафиксированы версии зависимостей
- Создан .dockerignore
- Убран Green API из architecture.md

### 2026-02-23 — Создание проекта
- Создан HANDOFF, architecture, спецификация S01
- Получены доступы Wazzup24 (API-ключ, channelId, 9 WABA-шаблонов)
- Декомпозиция задачи S01 на T01-T11
- T01: конфигурация Kommo получена
