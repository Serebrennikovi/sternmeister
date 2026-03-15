# HANDOFF — WhatsApp Auto-notifications (Sternmeister)

**Последнее обновление:** 15.03.2026 (legacy S01 линии удалены из кода)

---

## Текущий статус

**S01 и S02 завершены.** Обе спецификации закрыты, все задачи выполнены. Система работает в продакшене на Hetzner (65.108.154.202).

---

## Следующие шаги

Нет активных задач. При необходимости создать новую спецификацию S03.

---

## Спецификации

### S02: Расширение системы уведомлений ✅ done

Цепочка из 6 WABA-сообщений — 2 webhook (Г1, Б1) + 4 temporal (Б2-Б5 по дате термина ДЦ и АА).

Файл: [S02_notifications_expansion_done.md](2.%20specifications/S02_notifications_expansion_done.md)

**Задачи (все ✅):** T12 (config + schema + webhook) → T13 (temporal triggers) → T14 (deploy) → T15 (fail-safe backfill) → T16 (utility-only серия) → T17 (customer-facing text/template cleanup + send window `08:00-22:00`)

Файлы задач: [docs/3. tasks/Done/S02_notifications_expansion_done/](3.%20tasks/Done/S02_notifications_expansion_done/)

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
- [x] WABA шаблоны: approved templates доступны; финальная серия S02 подтверждена 13.03.2026
- [x] Доступ к редактированию и проверке Wazzup-шаблонов — получен

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

### 2026-03-15 — Legacy S01 линии удалены из кода
- Удалены `first`/`second` из `TEMPLATE_MAP`, `_VALID_LINES`, `.env.example` (убран `WAZZUP_TEMPLATE_ID`).
- Legacy шаблон `SternMeister_first_message` (GUID `08541939-...`) больше не может быть отправлен — код отвергнет линии `first`/`second` с `ValueError`.
- Тесты обновлены: 310 passed, 1 skipped.

### 2026-03-14 — T17 акцептована, S02 завершена
- T17: customer-facing text/template cleanup + send window `08:00-22:00` — выполнена. 313 тестов (0 failed).
- Два ревью-цикла: найден и исправлен HIGH (dict-path retry Б2/Б3 не форсировал `CUSTOMER_FACING_BERATER`), добавлен `_non_empty` для Б5, добавлены тесты keyed dict retry.
- Файл задачи T17 → `Done/S02_notifications_expansion_done/`. Папка `S02_notifications_expansion/` удалена (пуста).
- **Все 6 задач S02 (T12-T17) выполнены. Спецификация S02 закрыта.**

### 2026-03-13 — T17: Wazzup sync завершён, GUID-ы посажены в код
- Через `GET /templates/whatsapp` подтверждены финальные approved GUID: Г1 `95ddec60-bb6b-44a8-b5fb-a98abd76f974`, Б1 `47d2946c-f66a-4697-b702-eb5d138bb1f1`, Б2 `b028964c-9c27-4bc9-9b97-02a5e283df16`, Б3 `e1cb07aa-5236-4f8a-84dc-fef26b3cccf6`, Б4 `a9b04e05-6b6c-4a5f-9463-d8a0d96316f4`, Б5 `176a8b5b-8704-4d04-aee5-0fbd08641806`.
- `server/config.py`, `server/app.py`, `server/cron.py`, `server/template_helpers.py`, `server/messenger/wazzup.py` синхронизированы под реальные contracts:
  - Г1 — 2 vars (`SternMeister`, `news_text`)
  - Б1 — 1 var (`name`)
  - Б2 — 4 vars (`name`, `date`, `institution`, `checklist_text`)
  - Б3 — 3 vars (`name`, `institution`, `schedule_text`) + quick reply `Нужна помощь`
  - Б4 — 2 vars (`name`, `datetime_text`) + quick reply `Да, буду` / `Нет, не смогу`
- Targeted Docker suite по S02 sync: `198 passed`.
- До акцепта T17 остаётся только optional render-check на whitelist-номере.

### 2026-03-13 — T17: repo-side часть реализована, manual Wazzup sync остаётся
- `server/template_helpers.py`: введён единый customer-facing контракт S02 (`с Бератором`, `запросу`, без `назначенное время`), helper для AA `-7` gate и stale-state detection для Б1.
- `server/app.py`: webhook Б1 больше не отправляется при уже активном более позднем temporal-state; создаётся терминальный DB marker без retry.
- `server/cron.py`: retry/pending/backfill нормализуют legacy/template_values в новые customer-facing тексты, backfill Г1 переведён на keyed payload, stale Б1 больше не досылается, Б2 по АА уходит только на этапах `102183943/102183947`.
- `tests/`: обновлены unit/integration/e2e проверки; Docker-прогон `pytest tests -q` → `312 passed`.
- Полностью закрыть T17 нельзя без ручных правок в Wazzup UI и финального GUID sync.

### 2026-03-13 — T16 акцептована, создана T17 (customer-facing text/template cleanup)
- T16 переведена в `done` и перемещена в `docs/3. tasks/Done/S02_notifications_expansion_done/`.
- Создана T17: одна консолидированная задача на правки customer-facing текстов S02 и Wazzup-шаблонов.
- В T17 зафиксированы требования: вернуть поздравительный Б1, заменить customer-facing `Jobcenter`/`Agentur für Arbeit`/`AA` на `с Бератором`, убрать `назначенное время`, лишнее `в` перед датой, исправить `в это`/`Хотели`, убрать квадраты/точки от bullet-символов и сдвинуть send window на `08:00-22:00 Europe/Berlin`.

### 2026-03-11 — hotfix: full utility-only для S02 + pending-cron fix
- `server/config.py`: Г1 (`gosniki_consultation_done`) переключён с marketing-template на промежуточный utility-template как этап до final T17 sync.
- `server/cron.py`: в `process_pending()` удалён дополнительный `is_in_send_window()` gate; pending-сообщения отправляются по факту `next_retry_at <= now`.
- Тесты обновлены под новый GUID Г1 и новую pending-логику.
- Проверка: `pytest tests` → `300 passed`.

### 2026-03-10 — T16 реализована в кодовой базе (ожидает акцепта)
- `server/config.py`: Б1/Б2/Б4 переведены на utility-only шаблоны как промежуточный этап до final T17 sync.
- `server/kommo.py`: добавлен `extract_time_termin()` (`field_id=886670` → `HH:MM` Europe/Berlin).
- `server/app.py`: Б1 переведена на winner-алгоритм DC/AA + keyed `template_values` + fallback-safe composite поля.
- `server/cron.py`: Б2 включён как реальная temporal-отправка; Б4 использует line-specific composite поля; backfill Б1 обновлён на utility-only шаблон.
- `cron._build_message_data()`: добавлена legacy-реконструкция Б1 (`template_values=[name]`) без `None` в template-переменных.
- Тесты: `pytest tests` → `274 passed`.

### 2026-03-10 — создана T16 (utility-only серия S02)
- Зафиксирован customer-facing кейс: на одном и том же номере доходят UTILITY (Б3/Б5), но не всегда доходят MARKETING (Б1/Б4), при том что отправка API-уровня успешна (`sent` + `messenger_id` в БД).
- В Wazzup подтверждён статус approved для промежуточного utility-шаблона Б2, при этом в коде Б2 на тот момент ещё оставался заглушкой (`template_guid=None`).
- Создана задача T16: перевод S02-цепочки на utility-only шаблоны и включение Б2 в рабочую серию.

### 2026-03-10 — T16 уточнена после QA-фидбэка
- Добавлен реестр категорий/статусов шаблонов Г1 и Б1-Б5 по факту `GET /v3/templates/whatsapp` (снимок 10.03.2026).
- Для Б1/Б2/Б4 зафиксированы непустые fallback-значения всех template-переменных.
- Формализовано извлечение `time_termin` (`field_id=886670`): Unix timestamp → `HH:MM` (Europe/Berlin), с fallback при невалидном значении.
- В задаче явно описан маппинг `MessageData`/`template_values` для retry и восстановление в cron.
- Добавлены explicit diff-секции для `app.py` и `cron.py`, line-specific `template_values_json` (Б2/Б3/Б4), а также секция `Migration/Deployment` для legacy Б1 записей в retry-очереди.
- Исправлен fallback времени для Б2 на грамматически корректный (`назначенное время`) и добавлены тест-кейсы на retry для keyed и legacy Б1.

### 2026-03-10 — T16 дополнительно уточнена по второму ревью
- Добавлен explicit diff для `process_webhook_backfill()` по Б1 (4-переменный utility-шаблон, keyed `template_values`, запрет `None`).
- Устранено противоречие `time` vs `time_text`: зафиксировано, что в `MessageData.time`/БД хранится fallback-applied значение, а не сырой `None`.
- Детализирована legacy-реконструкция Б1 (`template_values=[name]`) фиксированными fallback-строками для composite customer-facing полей.
- Зафиксировано правило source-consistency для Б1: `date_for_template` берётся из того же winner-поля DC/AA, что и `institution`.
- Добавлены примечания по операционным рискам (shared legacy-template B4, рассинхрон `886670`, quick-reply в новом Б4) и тест-пункт на backfill Б1.

### 2026-03-06 — T15 акцептована, S02 stabilization завершена
- cron.py: process_webhook_backfill() — fail-safe backfill для Г1/Б1, record-before-send паттерн, IntegrityError dedup, MessengerError→failed, _WEBHOOK_BACKFILL_TARGETS
- db.py: get_webhook_line_exists(), idx_dedup_webhook_lines (partial unique index на kommo_lead_id, line для webhook-линий)
- app.py: lifetime dedup через get_webhook_line_exists() для webhook-линий (вместо time-based), IntegrityError catch на всех create_message()
- 4 новых тест-файла (unit + integration backfill), обновлены test_webhook_s02, test_alerts, test_webhook, test_cron, test_db_s02
- 272 теста (0 failed, 1 skipped), 3 ревью-цикла; закрыты H1 (IntegrityError в webhook handler) + H2 (send-before-record → record-before-send)

### 2026-03-04 — T13 акцептована
- kommo.py: get_active_leads() (пагинация 250/стр, with=contacts), extract_termin_date_dc(), extract_termin_date_aa(), _extract_date_from_field()
- utils.py: weekday_name(), format_date_ru()
- db.py: get_temporal_dedup()
- cron.py: process_temporal_triggers() (СТОП-проверка, деdup, ДЦ/АА независимо, contact fetch, Berlin today), _TEMPORAL_LINES, next_retry_at=None для sent temporal, try/except IntegrityError
- 2 новых тест-файла: 261 тест (0 failed, 1 skipped), 3 ревью-цикла; закрыты H1+H2+H1-NEW (customer-facing ретрай ×3, IntegrityError, fail→retry-success)
- docs/5. unsorted/kommo_api_reference.md: справочник GET /leads

### 2026-03-04 — T12 акцептована
- config.py: PIPELINE_CONFIG (10935879, 12154099, без 10631243), STOP_STATUSES, TEMPLATE_MAP (6 линий + заглушка Б2), FIELD_IDS["time_termin"]
- db.py: migrate_db() (атомарная, BEGIN IMMEDIATE, идемпотентная), create_message(template_values=...), get_failed_temporal_count(), idx_dedup_temporal
- messenger/wazzup.py: MessageData с новыми optional полями, send_message() через TEMPLATE_MAP, skipped для заглушки Б2
- kommo.py: extract_name()
- app.py: _TERMIN_OPTIONAL_LINES, извлечение имени, template_values в БД, failed_temporal в /health
- cron.py: восстановление extra-полей из template_values, обработка skipped
- 5 новых тест-файлов: 205 тестов (0 failed), 2 ревью-цикла

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
