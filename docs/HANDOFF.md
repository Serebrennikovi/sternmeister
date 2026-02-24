# HANDOFF — WhatsApp Auto-notifications (Sternmeister)

**Последнее обновление:** 24.02.2026

---

## Текущий статус

**Спецификация S01 завершена.** Все задачи T01-T11 выполнены и акцептованы. Сервис работает в продакшне на Hetzner (65.108.154.202) через ngrok (`https://shternmeister.ngrok.pro`). Полный цикл: webhook → Kommo API → проверка окна 9-21 → Wazzup24 → SQLite + cron retry/pending + Telegram алерты. 142 теста (141 pass, 1 skip).

---

## Завершённая спецификация

### S01: WhatsApp Auto-notifications ✅ done

Автоматические WhatsApp-уведомления для клиентов Sternmeister при смене этапа воронки в Kommo CRM.

Файл: [S01_whatsapp_auto_notifications_done.md](2.%20specifications/S01_whatsapp_auto_notifications_done.md)

**Задачи (все ✅):** T01 (Kommo config) → T02 (scaffold) → T03 (SQLite) → T04 (Kommo API) → T05 (Wazzup24) → T06 (webhook) → T07 (send window) → T08 (cron retries) → T09 (Telegram alerts) → T10 (deploy) → T11 (integration testing)

Файлы задач: [docs/3. tasks/Done/S01_whatsapp_auto_notifications_done/](3.%20tasks/Done/S01_whatsapp_auto_notifications_done/)

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
- [x] Webhook URL: `https://shternmeister.ngrok.pro/webhook/kommo?secret=...`

### Telegram Alerts
- [ ] Bot token для алертов
- [ ] Chat ID для уведомлений

---

## Внешние зависимости

- [x] Доступ к Kommo CRM
- [x] Webhook URL для Kommo: `https://shternmeister.ngrok.pro/webhook/kommo?secret=...`
- [ ] Webhook настроен в Kommo UI
- [ ] Telegram бот для алертов (опционально)

---

## Связанные документы

- [architecture.md](architecture.md) — техническая архитектура
- [S01_whatsapp_auto_notifications_done.md](2.%20specifications/S01_whatsapp_auto_notifications_done.md) — спецификация S01 (завершена)
- [Гайд: конвенции документации](4.%20guides/doc_conventions.md)
- [Гайд: декомпозиция на задачи](4.%20guides/task_decomposition_guide.md)
- [Security Checklist](4.%20guides/security_checklist.md)

---

## История изменений

### 2026-02-24 — T11 акцептована, S01 завершена
- Интеграционное тестирование: 19 новых E2E-тестов (test_integration_e2e.py)
- Полный код-ревью всех исходных файлов: безопасность, корректность, error handling, thread safety
- Итого: 142 теста (141 pass, 1 skip), все критерии DoD S01 подтверждены
- Фиксы: убран устаревший TODO(T11), исправлен тест-план (удалён сценарий Green API — YAGNI)
- **Спецификация S01 закрыта: все 11 задач (T01-T11) выполнены**

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
