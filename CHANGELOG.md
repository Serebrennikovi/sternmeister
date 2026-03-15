# Changelog

Все значимые изменения проекта Sternmeister — AI Automation документируются в этом файле.

Формат основан на [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added
- 2026-02-23 — T01: Получены конфигурация Kommo CRM (pipeline_id, status_id, field_id), создан API токен
- 2026-02-23 — Создан файл конфигурации [kommo_config.md](docs/kommo_config.md) с полным маппингом воронок и полей
- 2026-02-23 — T02: Scaffold проекта — FastAPI app, config.py, Dockerfile, .env.example, .gitignore, .dockerignore
- 2026-02-23 — T03: SQLite модель (db.py) — init_db, create_message, update_message, get_messages_for_retry, get_pending_messages
- 2026-02-23 — Добавлена команда `/accept` для закрытия задач
- 2026-02-24 — T04: Kommo API клиент (kommo.py) — KommoClient с get_lead_with_contacts, extract_phone, extract_termin_date, add_note; retry для 429
- 2026-02-24 — T05: Wazzup messenger (messenger/wazzup.py) — WazzupMessenger с send_message, build_message_text, MessageData, MessengerError; Wazzup24 v3 API (templateId + templateValues), retry 429/5xx, PII masking
- 2026-02-24 — T06: Webhook handler (app.py) — POST /webhook/kommo, form-data парсер (PHP bracket notation), dedup, Kommo API → extract phone/termin → Wazzup24 → SQLite. utils.py — parse_bracket_form()
- 2026-02-24 — T07: Send window logic (utils.py) — is_in_send_window(), get_next_send_window_start() DST-safe (zoneinfo). Webhook вне 9-21 → status=pending
- 2026-02-24 — T08: Cron-задача для повторов (cron.py) — process_retries() (sent/failed, attempts < 3), process_pending() (отложенные), Kommo add_note для retry/pending, 24 теста (pytest + freezegun)
- 2026-02-24 — T09: Telegram алерты (alerts.py) — TelegramAlerter с send_alert, alert_messenger_error, alert_kommo_error, alert_cron_error, alert_unexpected_error, alert_info; интеграция в app.py (KommoAPIError, MessengerError, catch-all) и cron.py (retry/pending ошибки, fatal error); graceful degradation без токена; PII masking телефонов; Markdown escaping; 31 тест
- 2026-02-24 — T10: Production deploy — Docker HEALTHCHECK (auto-restart при deadlock), webhook secret validation (hmac.compare_digest, secret-in-URL), UID 999 в Dockerfile (совпадает с хостом), enhanced /health endpoint (server time, send window status), startup warning при отсутствии KOMMO_WEBHOOK_SECRET. Deploy docs: ngrok systemd service, whatsapp-cron timer, rsync deploy, .env management
- 2026-02-24 — T11: Интеграционное тестирование — 19 E2E-тестов (test_integration_e2e.py), полный код-ревью, все DoD S01 подтверждены. Итого: 142 теста (141 pass, 1 skip). **S01 WhatsApp Auto-notifications — done**
- 2026-03-04 — T12: S02 config+schema+webhook+messenger — PIPELINE_CONFIG (Бух Гос/Бератер), TEMPLATE_MAP (6 линий + заглушка Б2), migrate_db() с template_values, extract_name(), MessageData расширен, failed_temporal в /health, восстановление template_values в cron. 205 тестов (0 failed)
- 2026-03-04 — T13: S02 temporal-триггеры (Б3–Б5) — process_temporal_triggers() с СТОП-проверкой, дедупликацией, пагинацией, Berlin today; get_active_leads(), extract_termin_date_dc/aa(), weekday_name(), format_date_ru(), get_temporal_dedup(); _TEMPORAL_LINES (next_retry_at=None для sent temporal), IntegrityError race protection. 261 тест (0 failed)
- 2026-03-06 — T14: S02 деплой на Hetzner — Docker rebuild + redeploy (образ :s02), DB миграция (migrate_db() с template_values, новые line-значения, idx_dedup_temporal), backup БД, smoke-тесты в продакшене. Все 7 критериев приёмки пройдены. **S02 WhatsApp Notifications Expansion — done**
- 2026-03-06 — T15: S02 fail-safe backfill webhook-линий (Г1/Б1) — process_webhook_backfill() в cron, record-before-send паттерн, partial unique index idx_dedup_webhook_lines, lifetime dedup в webhook handler, IntegrityError protection. 272 теста. **S02 stabilization — done**
- 2026-03-10 — T16: S02 utility-only серия (Б1/Б2/Б4) — Б1/Б2/Б4 переведены на UTILITY GUID, Б2 включён в реальные temporal-отправки, добавлен `extract_time_termin()` (Kommo field 886670), введены keyed `template_values` для Б1 и fallback-safe сборка composite-полей для webhook/temporal/backfill/retry.
- 2026-03-13 — T17: создана задача S02 на customer-facing cleanup текстов/Wazzup-шаблонов и изменение окна отправки на `08:00-22:00 Europe/Berlin`.
- 2026-03-13 — T17 repo-side: customer-facing контракт унифицирован до `с Бератором` / `запросу`, stale Б1 перестал досылаться через webhook/retry/pending/backfill, для Б2 по АА добавлен stage-gate `102183943/102183947`, send window синхронизирован на `08:00-22:00`, full Docker-прогон `pytest tests -q` → `312 passed`.
- 2026-03-14 — T17 акцептована: customer-facing text/template cleanup + send window `08:00-22:00`. Исправлен dict-path retry Б2/Б3 (форсирование `CUSTOMER_FACING_BERATER`), добавлен `_non_empty` для Б5, тесты keyed dict retry. 313 тестов. **S02 Расширение системы уведомлений — done.**

### Fixed
- 2026-02-24 — Код-ревью T09: Markdown injection в catch-all алерте (send_alert → alert_unexpected_error с _escape_md), добавлен _escape_md в alert_info, интеграционные тесты для cron retry/pending alert_messenger_error
- 2026-02-24 — Код-ревью T08: `except Exception as exc` (exc не привязан), добавлен Kommo add_note для pending/retry отправок, обновлена документация задачи (устаревший псевдокод, green_api→wazzup, scope sent→sent/failed, ExecStart)
- 2026-02-23 — Код-ревью T01-T03: убраны секреты из документов, .dockerignore исправлен (__pycache__), timestamps в db.py переведены на UTC
- 2026-02-24 — Код-ревью T04: 429 retry из JSON body (не header), extract_termin_date возвращает None при ошибке парсинга, JSONDecodeError → KommoAPIError, локальные номера 0→+49, удалён мёртвый код (_TRUNK_ZERO_RE, unused import)
- 2026-02-24 — Код-ревью T05: добавлен build_message_text() для DB logging, elif chain в error handling, WAZZUP_TEMPLATE_GUID→WAZZUP_TEMPLATE_ID, удалён python-dateutil, убран base.py/Flask из docs
- 2026-02-24 — Код-ревью T06: attempts=0 для pending, консистентный response format (всегда results array), TypeError в form-парсере, json.loads вместо request.json(), тесты termin fallback/batch mixed/malformed body (61 тест)
- 2026-03-10 — FIX 016 (T16 раунд 2): добавлен retry-тест Б1 keyed-dict `template_values`, убрана двойная нормализация `time_raw` в webhook/backfill/temporal, расширены temporal-тесты для Б2 (`time` fallback) и Б4 (composite-поля), e2e-тест Б1 переведён на явный mocking `extract_termin_date_dc/aa/time_termin`.
- 2026-03-13 — T16: задача акцептована, файл перемещён в `Done/`, `HANDOFF` и S02-спека синхронизированы.

---

## История

### 2026-02-23 — Инициализация проекта
- Создана структура документации (HANDOFF, architecture, спецификация S01)
- Получены доступы к Wazzup24 WABA (API-ключ, channelId, 9 одобренных WABA-шаблонов)
- Декомпозиция задачи S01 на T01-T11 (10 задач)
