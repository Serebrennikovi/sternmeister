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

### Fixed
- 2026-02-23 — Код-ревью T01-T03: убраны секреты из документов, .dockerignore исправлен (__pycache__), timestamps в db.py переведены на UTC
- 2026-02-24 — Код-ревью T04: 429 retry из JSON body (не header), extract_termin_date возвращает None при ошибке парсинга, JSONDecodeError → KommoAPIError, локальные номера 0→+49, удалён мёртвый код (_TRUNK_ZERO_RE, unused import)
- 2026-02-24 — Код-ревью T05: добавлен build_message_text() для DB logging, elif chain в error handling, WAZZUP_TEMPLATE_GUID→WAZZUP_TEMPLATE_ID, удалён python-dateutil, убран base.py/Flask из docs

---

## История

### 2026-02-23 — Инициализация проекта
- Создана структура документации (HANDOFF, architecture, спецификация S01)
- Получены доступы к Wazzup24 WABA (API-ключ, channelId, 9 одобренных WABA-шаблонов)
- Декомпозиция задачи S01 на T01-T11 (10 задач)
