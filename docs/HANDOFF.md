# HANDOFF — WhatsApp Auto-notifications (Sternmeister)

**Последнее обновление:** 24.02.2026

---

## Текущий статус

Проект в стадии **активной разработки**. Foundation завершён (T01-T03), Kommo API клиент (T04) и Wazzup messenger (T05) реализованы, переходим к webhook handler (T06).

---

## Активная спецификация

### S01: WhatsApp Auto-notifications (draft)

Автоматические WhatsApp-уведомления для клиентов Sternmeister при смене этапа воронки в Kommo CRM.

Файл: [S01_whatsapp_auto_notifications.md](2.%20specifications/S01_whatsapp_auto_notifications.md)

---

## Задачи S01

**Всего задач:** 10 (T01-T11, без пропусков)
**Текущая задача:** T06 — Webhook handler для Kommo

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
- **Статус:** draft (следующая)
- **Файл:** [T06_s01_webhook_handler.md](3.%20tasks/S01_whatsapp_auto_notifications/T06_s01_webhook_handler.md)
- **Требует:** T03, T04, T05
- **Инкремент:** POST /webhook/kommo — полный цикл: webhook → Kommo API → Wazzup24 → SQLite

### Фаза 3: Features (параллельные ветки после T06)

**T07** — Логика окна времени и отложенные сообщения
- **Статус:** draft
- **Файл:** [T07_s01_send_window_logic.md](3.%20tasks/S01_whatsapp_auto_notifications/T07_s01_send_window_logic.md)
- **Требует:** T06
- **Инкремент:** is_in_send_window(), отложенные сообщения до 9:00

**T08** — Cron-задача для повторов
- **Статус:** draft
- **Файл:** [T08_s01_cron_retries.md](3.%20tasks/S01_whatsapp_auto_notifications/T08_s01_cron_retries.md)
- **Требует:** T06, T07
- **Инкремент:** Автоматические повторы через 24ч, макс 2 раза

**T09** — Telegram алерты
- **Статус:** draft
- **Файл:** [T09_s01_telegram_alerts.md](3.%20tasks/S01_whatsapp_auto_notifications/T09_s01_telegram_alerts.md)
- **Требует:** T06
- **Инкремент:** Telegram-уведомления при ошибках отправки

### Фаза 4: Production

**T10** — Деплой на Hetzner и настройка webhook
- **Статус:** draft
- **Файл:** [T10_s01_deploy.md](3.%20tasks/S01_whatsapp_auto_notifications/T10_s01_deploy.md)
- **Требует:** T06-T09
- **Инкремент:** Docker на Hetzner, Nginx + SSL, webhook URL в Kommo

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
- [ ] Webhook URL для Kommo (нужен публичный URL)
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
