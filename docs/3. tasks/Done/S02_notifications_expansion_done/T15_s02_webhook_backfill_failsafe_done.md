**Дата:** 2026-03-06
**Статус:** done
**Спецификация:** docs/2. specifications/S02_notifications_expansion_done.md

# T15 — Fail-safe backfill для пропущенных webhook-триггеров (Г1/Б1)

## Customer-facing инкремент

Клиент не теряет WhatsApp-уведомление Г1/Б1, даже если Kommo не прислал (или сервис не принял) webhook `status_changed` для лида в целевом этапе.

## Scope

- Добавить в `cron.py` fail-safe процесс для webhook-линий:
  - `gosniki_consultation_done` (`pipeline_id=10935879`, `status_id=95514983`)
  - `berater_accepted` (`pipeline_id=12154099`, `status_id=93860331`)
- Источник данных: `get_active_leads()` из Kommo API (с `with=contacts`) с двумя отдельными вызовами:
  - `get_active_leads(10935879)` + фильтр `lead["status_id"] == 95514983`
  - `get_active_leads(12154099)` + фильтр `lead["status_id"] == 93860331`
- Для каждого лида, прошедшего фильтр целевого статуса:
  - определить `line` по `pipeline_id/status_id`;
  - проверить, есть ли уже сообщение по `(kommo_lead_id, line)` в БД (новая функция `get_webhook_line_exists(lead_id, line)` без time-window);
  - если нет:
    - собрать данные контакта/шаблона (имя, телефон, optional `termin_date`);
    - для `template_values` использовать webhook-совместимый формат `json.dumps([name])` для Г1/Б1;
    - если сейчас вне окна отправки — создать `pending` с `next_retry_at=get_next_send_window_start()`;
    - если в окне — отправить и сохранить `sent`;
    - добавить note в Kommo как в обычном webhook-пути.
- Добавить DB-level guard от гонок webhook ↔ cron:
  - миграция: partial unique index
    `UNIQUE(kommo_lead_id, line) WHERE line IN ('gosniki_consultation_done', 'berater_accepted')`;
  - обработка `sqlite3.IntegrityError` как штатного дедуп-сценария (лог + continue).
- Сделать процесс идемпотентным на уровне логики и БД:
  - не создавать дубли при повторных прогонах cron;
  - корректно переживать race webhook ↔ cron.
- Встроить вызов в `main()` cron после `process_pending()` и до `process_temporal_triggers()`.
- Добавить алерты и логирование для ошибок Kommo/Wazzup по аналогии с текущими cron-процессами.
- Добавить примечание в коде: в рамках T15 правило для Г1/Б1 — не более 1 сообщения на `(lead_id, line)` за lifecycle сделки.

## Out of scope

- Переписывание webhook-обработчика `/webhook/kommo`.
- Изменение бизнес-окон отправки (09:00–21:00 Berlin).
- Бэкфилл temporal-линий (Б2–Б5).
- Массовая историческая миграция старых лидов до даты деплоя T15.
- Повторная отправка Г1/Б1 при возврате лида в тот же статус (re-entry сценарий требует отдельного бизнес-решения).
- Кейс, когда лид уже ушёл из целевого статуса до cron-прогона (такой пропуск фиксируется отдельно, в T15 не закрывается).

## Как протестировать

1. Подготовить лид в одном из целевых статусов (Г1 или Б1), не отправляя webhook в сервис.
2. Убедиться, что в `messages` нет записи по `(lead_id, line)`.
3. Запустить cron вручную: `docker exec whatsapp-notifications python -m server.cron`.
4. Проверить:
   - в окне отправки: создана запись `sent`, есть `messenger_id`, добавлена note в Kommo;
   - вне окна: создана запись `pending` с корректным `next_retry_at` (`09:00 Berlin` = `08:00 UTC` зимой / `07:00 UTC` летом).
5. Запустить cron второй раз и убедиться, что дубликат не создаётся.
6. Смоделировать race webhook+cron:
   - перед `create_message` в backfill вручную вставить такую же запись (или вызвать webhook-путь);
   - убедиться, что второй путь падает в `IntegrityError` и в БД остаётся ровно одна запись.
7. Проверить фильтрацию по статусу:
   - лиды в тех же pipeline, но с другими `status_id`, не должны создавать записи.

## Критерии приёмки

1. Для Г1/Б1-лида в целевом статусе без webhook-обработки сообщение всё равно создаётся/отправляется cron-ом.
2. Обрабатываются оба источника:
   - Бух Гос: `10935879/95514983` → `gosniki_consultation_done`;
   - Бух Бератер: `12154099/93860331` → `berater_accepted`.
3. При повторном cron-прогоне дубликаты по `(lead_id, line)` не появляются (подтверждено DB unique index + логикой).
4. При внеокне отправки создаётся `pending` с `next_retry_at`, соответствующим ближайшим `09:00 Berlin`.
5. При внутриокне отправки создаётся `sent` с `messenger_id`, `sent_at`, Kommo-note.
6. Ошибки Kommo/Wazzup логируются и отправляют алерт, cron не падает целиком.
7. Добавлены автотесты:
   - unit: фильтрация по `status_id` + dedup (`get_webhook_line_exists`/`IntegrityError`);
   - integration: end-to-end backfill для Г1 или Б1.
8. Неактуальные лиды (не в целевом `status_id`) не попадают в backfill.

## Зависимости

- Требует: T12, T13, T14 (выполнены).
- Требует миграцию БД для нового partial unique index по webhook-линиям.
- Блокирует: финальную стабилизацию S02 в production.

---

## Обновление выполнения (2026-03-06, Codex)

### Что реализовано

- `server/cron.py`
  - Добавлен `process_webhook_backfill()` для fail-safe обработки пропущенных webhook-линий.
  - Реализованы 2 источника:
    - `10935879/95514983` -> `gosniki_consultation_done`
    - `12154099/93860331` -> `berater_accepted`
  - Добавлен логический дедуп через `get_webhook_line_exists(lead_id, line)`.
  - Вне send window создаётся `pending` с `next_retry_at=get_next_send_window_start()`.
  - Внутри send window отправка + сохранение `sent` + Kommo note.
  - Добавлены обработка `KommoAPIError`/`MessengerError`, алерты и обработка `sqlite3.IntegrityError` как dedup race.
  - В `main()` вызов встроен после `process_pending()` и до `process_temporal_triggers()`.

- `server/db.py`
  - Добавлен partial unique index:
    - `idx_dedup_webhook_lines` на `(kommo_lead_id, line)` для `gosniki_consultation_done`/`berater_accepted`.
  - Добавлена функция `get_webhook_line_exists(kommo_lead_id, line)`.
  - Для уже мигрированных БД `migrate_db()` теперь дополнительно гарантирует создание индексов.

### Добавленные тесты

- Unit:
  - `tests/test_cron_webhook_backfill.py`
    - фильтрация по `status_id`
    - дедуп через `get_webhook_line_exists`
    - `IntegrityError`-ветка (race webhook/cron)

- Integration:
  - `tests/test_integration_webhook_backfill.py`
    - end-to-end backfill (внутри окна) + идемпотентность (повторный прогон без дублей)
    - вне окна -> `pending` с корректным `next_retry_at` (09:00 Berlin)

- Сопутствующее:
  - `tests/test_db_s02.py`
    - проверки для `idx_dedup_webhook_lines`
    - тесты `get_webhook_line_exists()`
  - `tests/test_cron.py`
    - обновлён тест `main()` с учётом вызова `process_webhook_backfill()`

### Прогон тестов

- Целевой набор: `61 passed`
- Полный `pytest`: `271 passed`

### Критерии приёмки (фактическое покрытие)

- [x] 1. Backfill создаёт/отправляет Г1/Б1 при пропущенном webhook
- [x] 2. Поддержаны оба источника (Гос/Бератер)
- [x] 3. Дедуп подтверждён логикой + DB unique index
- [x] 4. Вне окна создаётся `pending` с корректным `next_retry_at`
- [x] 5. В окне создаётся `sent` с `messenger_id`, `sent_at`, Kommo-note
- [x] 6. Ошибки Kommo/Wazzup логируются и алертятся без падения cron целиком
- [x] 7. Добавлены unit + integration тесты для backfill
- [x] 8. Лиды вне целевого `status_id` отфильтровываются

---

## Код-ревью (2026-03-06)

### 🔴 HIGH — Реальные баги

#### H1. Webhook handler (app.py) не обрабатывает IntegrityError от нового unique index → ложные алерты + duplicate send

**Где:** `app.py:261, 294, 314` — три вызова `create_message()` в `_process_lead_status_inner()`

T15 добавил partial unique index `idx_dedup_webhook_lines` на `(kommo_lead_id, line) WHERE line IN ('gosniki_consultation_done', 'berater_accepted')`. Но webhook handler в `app.py` не обновлён для работы с ним.

**Сценарий 1 — Backfill обработал лид до webhook:**
1. Cron backfill создал запись `(lead_id=100, line='berater_accepted')`
2. Через 15 мин Kommo присылает webhook для того же лида
3. `get_recent_message(lead_id=100, 'berater_accepted', within_minutes=10)` → `None` (запись >10 мин назад — вне dedup window)
4. Webhook вызывает Kommo API (`get_lead_contact`), Wazzup API (`send_message`) — **клиент получает ВТОРОЕ WhatsApp-сообщение**
5. `create_message()` → `sqlite3.IntegrityError`
6. Исключение перехватывается в `_process_lead_status()` как `Exception` → `alert_unexpected_error()` → ложный Telegram-алерт «Unexpected error»

**Сценарий 2 — Kommo повторяет webhook после dedup window:**
1. Первый webhook штатно обработан, запись создана
2. Kommo повторяет тот же webhook через 11 мин (dedup window = 10 мин)
3. Та же цепочка: `get_recent_message()` пропускает → Wazzup отправляет → IntegrityError → ложный алерт

**Последствия:**
- Customer-facing: клиент получает 2 одинаковых WhatsApp-сообщения
- Ops: ложные «Unexpected error» алерты в Telegram забивают канал

**Корневая причина:** Webhook handler использует time-based dedup (`get_recent_message`, 10 мин) для webhook-линий, тогда как T15 ввёл lifetime-based правило (один `(lead_id, line)` навсегда). Для `gosniki_consultation_done` и `berater_accepted` нужно использовать `get_webhook_line_exists()` вместо `get_recent_message()` — так же, как делает backfill.

**Примечание:** Out of scope T15 говорит «переписывание webhook-обработчика» — но это не переписывание, а минимальное дополнение: добавить IntegrityError catch + заменить dedup-check для webhook-линий. Без этого T15 не выполняет собственный критерий «корректно переживать race webhook ↔ cron».

---

#### H2. Backfill отправляет сообщение ДО создания DB-записи → race window для duplicate send

**Где:** `cron.py:355-416` — «в окне отправки» ветка `process_webhook_backfill()`

Порядок операций:
```
1. get_webhook_line_exists(lead_id, line) → False
2. ← race window ← webhook может обработать тот же лид
3. messenger.send_message() → WhatsApp отправлен клиенту
4. create_message() → IntegrityError если webhook успел вставить запись
```

Между шагами 1 и 4 есть окно, в котором webhook может обработать тот же лид. Оба пути (webhook + backfill) отправят WhatsApp, клиент получит дубль.

**Безопасный паттерн (уже реализован для «вне окна»):**
- Вне окна (строки 328-353): `create_message(status="pending")` → IntegrityError ДО отправки → клиент не получает дубль ✅
- В окне (строки 355-416): `send_message()` → `create_message()` → IntegrityError ПОСЛЕ отправки → клиент уже получил дубль ❌

**Исправление:** создавать запись `status="pending"` ДО отправки, затем отправлять, затем `update_message(status="sent")`. Если IntegrityError при создании — отправки не было.

---

### 🟡 MEDIUM — Корректность и надёжность

#### M1. Нет теста на MessengerError-путь backfill

**Где:** `cron.py:357-383`, `test_cron_webhook_backfill.py`, `test_integration_webhook_backfill.py`

Ветка `except MessengerError` (создание `status="failed"` записи с `next_retry_at`) не покрыта ни unit-, ни integration-тестом. Все тесты используют успешный messenger mock. Если будет баг в формировании `failed`-записи или расчёте `next_retry_at`, тесты его не поймают.

---

#### M2. Нет integration-теста для berater_accepted (Б1)

**Где:** `test_integration_webhook_backfill.py`

Оба integration-теста (`test_backfill_gosniki_sent_and_idempotent`, `test_backfill_gosniki_outside_window_creates_pending`) тестируют только `gosniki_consultation_done` (Г1, pipeline 10935879). Линия `berater_accepted` (Б1, pipeline 12154099) покрыта только unit-тестом через mock.

Если для Б1 есть расхождение в реальном маппинге pipeline→status→line (например, ошибка в `_WEBHOOK_BACKFILL_TARGETS`), integration-тест это не поймает.

---

#### M3. Тест IntegrityError невольно подтверждает duplicate send

**Где:** `test_cron_webhook_backfill.py:149-153`

```python
mock_create.side_effect = [sqlite3.IntegrityError("dup"), 22]
created, failed = process_webhook_backfill()

assert messenger.send_message.call_count == 2  # ← оба лида получили WhatsApp
```

Тест проверяет, что IntegrityError не останавливает обработку (корректно). Но `send_message.call_count == 2` означает, что первый лид получил WhatsApp, несмотря на IntegrityError при записи в БД. Тест не проверяет, что первый `created` === 0 в этом случае (т.е. что счётчик корректен). Фактически `(created, failed) == (1, 0)` означает: один «создан», но это второй лид — а первый отправлен и потерян.

---

### 🟢 LOW — Качество кода

#### L1. Счётчик `created` не учитывает отправку при IntegrityError

**Где:** `cron.py:405-410`

При успешном `send_message()` + IntegrityError в `create_message()` → `continue` без инкремента `created` или `failed`. Сообщение ОТПРАВЛЕНО клиенту, но в логе cron: `backfill 0 created / 0 failed` — метрика скрывает реальную отправку.

---

#### L2. `process_webhook_backfill()` не проверяет `result["status"] == "skipped"`

**Где:** `cron.py:355-416`

`process_retries()` (строка 132) и `process_pending()` (строка 224) проверяют `result.get("status") == "skipped"` для placeholder-шаблонов. `process_webhook_backfill()` — нет.

**Текущая достижимость:** недостижима. Оба шаблона Г1/Б1 имеют реальные template_guid (не None). Но несогласованность с другими процессами — потенциальный latent bug при добавлении нового webhook-шаблона.

---

#### L3. Нет комментария о `termin_date=""` для webhook-линий backfill

**Где:** `cron.py:323, 335, 369, 396`

Backfill всегда передаёт `termin_date=""` в `MessageData` и `create_message()`. Это корректно (Г1/Б1 не используют дату в шаблоне), но причина не объяснена в коде. Читатель может решить, что это забытый TODO.

---

### Итог ревью

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| H1 | 🔴 HIGH | `app.py:261,294,314` | Webhook handler не ловит IntegrityError + использует time-based dedup вместо lifetime → duplicate send + ложные алерты |
| H2 | 🔴 HIGH | `cron.py:355-416` | Backfill: send before record → race window для duplicate send клиенту |
| M1 | 🟡 MEDIUM | `cron.py:357-383` | Нет теста на MessengerError-путь (failed record) |
| M2 | 🟡 MEDIUM | `test_integration_*.py` | Нет integration-теста для berater_accepted (Б1) |
| M3 | 🟡 MEDIUM | `test_cron_webhook_backfill.py:149` | Тест IntegrityError подтверждает duplicate send без проверки |
| L1 | 🟢 LOW | `cron.py:405-410` | Метрика created/failed не учитывает отправку при IntegrityError |
| L2 | 🟢 LOW | `cron.py:355-416` | Нет проверки `result["status"]=="skipped"` |
| L3 | 🟢 LOW | `cron.py:323` | Нет комментария о `termin_date=""` |

---

### Сравнение с предыдущими ревью

| Ревью | HIGH | MEDIUM | LOW |
|-------|------|--------|-----|
| T12 первое | 0 | 2 | 8 |
| T13 первое | 2 | 3 | 2 |
| T13 второе (после фиксов) | 1 | 2 | 1 |
| T13 третье (финальное) | 0 | 1 | 4 |
| **T15 первое (текущее)** | **2** | **3** | **3** |

**Вывод: на уровне предыдущих первых ревью, не хуже.**

Первое ревью T15 (2H/3M/3L) примерно соответствует первому ревью T13 (2H/3M/2L) и лучше первого ревью T12 по MEDIUM (3 vs 2, но 0 HIGH у T12). Качество первичной разработки стабильно — HIGH-баги типичны для первого ревью и устраняются в цикле фиксов.

Главная проблема — H1: T15 добавил partial unique index, но не обновил webhook handler для корректной работы с ним. Это **регрессия** — до T15 IntegrityError в webhook handler был невозможен для webhook-линий, теперь возможен и приводит к duplicate send + ложным алертам. H2 (send before record в backfill) — классический TOCTOU, решаемый перестановкой операций (как уже сделано для pending-ветки).

Оба HIGH-бага — customer-facing (клиент может получить 2 одинаковых WhatsApp). Без фикса H1/H2 деплой T15 добавит новый класс production-инцидентов.

---

## Фиксы по ревью (2026-03-06)

**Все 8 багов (2H/3M/3L) закрыты.**

### H1 ✅ — Webhook handler: lifetime dedup + IntegrityError catch (`app.py`)
- Разделена dedup-логика: webhook-линии (`gosniki_consultation_done`, `berater_accepted`) используют `get_webhook_line_exists()` (lifetime dedup), остальные — `get_recent_message()` (time-based).
- Все 3 вызова `create_message()` обёрнуты в `try/except sqlite3.IntegrityError` — логируется как dedup race, не вызывает `alert_unexpected_error()`.
- Добавлен `import sqlite3` и `get_webhook_line_exists` в импорты.

### H2 ✅ — Backfill: record-before-send (`cron.py`)
- «In window» ветка переписана на паттерн reserve slot: `create_message(pending, attempts=0)` → `send_message()` → `update_message(sent)`.
- IntegrityError при create → continue ДО отправки = клиент не получает дубль.
- MessengerError → `update_message(failed, attempts=1, next_retry_at=...)`.
- **L1 закрыта автоматически:** метрика created/failed теперь корректна.

### L2 ✅ — Проверка `result["status"] == "skipped"` в backfill (`cron.py`)
- Добавлена после `send_message()` для консистентности с `process_retries()`/`process_pending()`.
- Сейчас недостижима (оба шаблона реальные), защита на будущее.

### L3 ✅ — Комментарий `termin_date=""` (`cron.py`)
- Добавлен: `# Г1/Б1 templates use only {{1}}=name, no date variable`.

### M3 ✅ — Обновлён тест IntegrityError (`test_cron_webhook_backfill.py`)
- `send_message.call_count == 1` (не 2) — первый лид не получает WhatsApp.
- Добавлен mock `update_message`, проверка `mock_update.call_count == 1`.

### M1 ✅ — Тест MessengerError-пути (`test_cron_webhook_backfill.py`)
- Новый тест `test_backfill_messenger_error_creates_failed_record`: 2 лида (fail + success), проверяет `update_message` с `status="failed"` и `status="sent"`.

### M2 ✅ — Integration-тест для berater_accepted (`test_integration_webhook_backfill.py`)
- Новый тест `test_backfill_berater_accepted_sent_and_idempotent`: pipeline 12154099, idempotent (второй прогон = 0,0).

### Обновлённые моки в тестах
- `test_webhook_s02.py`: 5 патчей `get_recent_message` → `get_webhook_line_exists`
- `test_alerts.py`: 4 патча `get_recent_message` → `get_webhook_line_exists`
- `test_webhook.py`: 1 тест dedup обновлён для lifetime dedup

### Результат тестов
- **272 passed, 1 skipped, 0 failed** (Docker, Python 3.11)

---

## Код-ревью #2 — после фиксов (2026-03-06)

Независимый придирчивый ревью всего кода T15 (включая фиксы первого ревью).

**Результат: 0 HIGH, 2 MEDIUM, 2 LOW.**

Все 8 багов первого ревью закрыты корректно. Новые находки — остаточные пробелы в тестовом покрытии и один known-risk TOCTOU.

---

### 🟡 MEDIUM

#### M1. Нет тестов для IntegrityError-путей в webhook handler (app.py)

**Где:** `app.py:289-297` (pending), `app.py:319-335` (failed), `app.py:345-370` (sent)

H1 фикс добавил три `try/except sqlite3.IntegrityError` в webhook handler. Ни один из них не покрыт тестом. Особенно критичен "sent" путь (строка 359): сообщение уже отправлено клиенту, IntegrityError при записи → лог warning, возврат "ok". Без теста этот путь может регрессировать при рефакторинге.

**Почему важно:** это новый код, добавленный T15. Если IntegrityError catch случайно уберут — webhook handler начнёт отправлять `alert_unexpected_error()` на каждый race, забивая Telegram.

**Предлагаемый тест (для "sent" пути):**
- Mock `create_message` → `side_effect=sqlite3.IntegrityError`
- Verify: response `"status": "ok"`, `alert_unexpected_error` NOT called.

---

#### M2. Остаточный TOCTOU в webhook handler — send before record (app.py:308-370)

**Где:** `app.py:308-370` — «in window + successful send» ветка

Webhook handler для webhook-линий (Г1/Б1): отправляет WhatsApp (строка 310) ДО создания DB-записи (строка 346). Backfill (после H2 фикса) использует record-before-send, но webhook handler — нет.

**Сценарий race:**
1. Webhook: `get_webhook_line_exists(X, line)` → False (t=0.000s)
2. Backfill: `get_webhook_line_exists(X, line)` → False (t=0.100s)
3. Backfill: `create_message(pending)` → OK — слот зарезервирован (t=0.200s)
4. Backfill: `send_message()` → WhatsApp #1 отправлен (t=0.500s)
5. Webhook: Kommo API + Wazzup API... (t=0.000-2.000s)
6. Webhook: `send_message()` → WhatsApp #2 отправлен (t=2.000s)
7. Webhook: `create_message()` → IntegrityError (t=2.100s)

**Результат:** клиент получает 2 WhatsApp. Окно: ~1-5 секунд (2 сетевых вызова в webhook handler между check и record).

**Статус:** Known risk, задокументирован в коде (`"narrow TOCTOU window, acceptable"`), out of T15 scope ("переписывание webhook-обработчика"). Фиксируется паттерном reserve-before-send в webhook handler — аналогично H2 фиксу для backfill. Рекомендуется для T16 или следующего стабилизационного цикла.

---

### 🟢 LOW

#### L1. Stale `get_recent_message` mocks в test_webhook.py

**Где:** ~15 тестов в `test_webhook.py` (TestWebhookHappyPath, TestWebhookErrors, TestWebhookTerminFallback, TestWebhookGosnikAndBerater, TestWebhookMultipleLeads, TestWebhookFormEncoded, TestWebhookAddNoteFailure, TestWebhookCatchAll)

Эти тесты используют дефолтный payload `berater_accepted` (pipeline=12154099, status=93860331), но патчат `@patch("server.app.get_recent_message", return_value=None)`. После H1 фикса для webhook-линий вызывается `get_webhook_line_exists()`, а не `get_recent_message()` — мок мёртвый.

Тесты проходят, потому что реальный `get_webhook_line_exists` обращается к пустой тестовой БД и возвращает False. Но:
- Если кто-то уберёт `if line in _TERMIN_OPTIONAL_LINES` guard, тесты всё равно пройдут (fallback на `get_recent_message` + mock None) — регрессия не будет поймана.
- Мёртвые моки вводят в заблуждение при чтении тестов.

**Fix:** заменить `get_recent_message` → `get_webhook_line_exists` в этих тестах (аналогично тому, как уже сделано в `test_webhook_s02.py` и `test_alerts.py`).

---

#### L2. Zombie pending record при "skipped" в backfill (cron.py:397-402)

**Где:** `cron.py:356-402`

Record-before-send паттерн: `create_message(pending, attempts=0, next_retry_at=None)` → `send_message()` → если `result["status"] == "skipped"` → `continue` без обновления записи.

Запись остаётся: `status=pending, attempts=0, next_retry_at=NULL`. Её никто не подберёт:
- `process_pending()`: `WHERE next_retry_at <= ?` — NULL не проходит
- `process_retries()`: `WHERE status IN ('sent', 'failed')` — pending не проходит

При следующем backfill-прогоне `get_webhook_line_exists` вернёт True — лид больше не будет обработан. Фактически — silent delivery failure.

**Достижимость:** сейчас нет (оба Г1/Б1 шаблона реальные). Станет реальной при добавлении webhook-линии с placeholder template.

**Fix:** проверять `TEMPLATE_MAP[line]["template_guid"] is None` ДО reserve-шага (как в `process_temporal_triggers():497`), или обновлять zombie-запись при "skipped".

---

### Итог ревью #2

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| M1 | 🟡 MEDIUM | `app.py:289,319,345` | Нет тестов для IntegrityError-путей в webhook handler |
| M2 | 🟡 MEDIUM | `app.py:308-370` | Остаточный TOCTOU: send before record (known risk, out of scope) |
| L1 | 🟢 LOW | `test_webhook.py` (~15 тестов) | Stale `get_recent_message` mocks для `berater_accepted` |
| L2 | 🟢 LOW | `cron.py:397-402` | Zombie pending record при "skipped" (unreachable) |

---

### Сравнение с предыдущими ревью (обновлённое)

| Ревью | HIGH | MEDIUM | LOW |
|-------|------|--------|-----|
| T12 первое | 0 | 2 | 8 |
| T13 первое | 2 | 3 | 2 |
| T13 второе (после фиксов) | 1 | 2 | 1 |
| T13 третье (финальное) | 0 | 1 | 4 |
| T15 первое | 2 | 3 | 3 |
| **T15 второе (после фиксов)** | **0** | **2** | **2** |

**Вывод: лучше, чем T13 после первого цикла фиксов.**

T15 после фиксов (0H/2M/2L) лучше T13 после фиксов (1H/2M/1L) — нет HIGH-багов. Оба MEDIUM: один — пробел в тестах (легко закрыть), второй — known risk TOCTOU (acknowledged, out of scope). Оба LOW — нефункциональные (мёртвые моки + unreachable path).

Качество фиксов высокое: H1 (lifetime dedup) и H2 (record-before-send) закрыты грамотно, тесты обновлены. Единственный residual customer-facing risk — M2 (TOCTOU в webhook handler), но он требует precise timing overlap webhook+cron и задокументирован в коде.

**Не стало хуже.** T15 привнёс сложность (новый partial unique index + двойная dedup-стратегия), но после фикс-цикла основные риски закрыты. Для деплоя: рекомендуется закрыть M1 (3 теста) перед production; M2 и оба LOW можно перенести на следующий цикл.

---

## Код-ревью #3 — независимый придирчивый ревью (2026-03-06, Claude Opus)

Полный independent review всего кода T15, включая фиксы обоих предыдущих ревью.

**Результат: 0 HIGH, 1 MEDIUM (новая), 3 LOW (новые).**

Оба HIGH из первого ревью закрыты корректно. 4 находки из ревью #2 **НЕ ЗАКРЫТЫ** (M1-R2, M2-R2, L1-R2, L2-R2).

---

### Незакрытые находки из ревью #2

| # | Серьёзность | Статус | Суть |
|---|-------------|--------|------|
| M1-R2 | 🟡 MEDIUM | OPEN | Нет тестов для IntegrityError-путей в webhook handler (`app.py:289,319,345`) |
| M2-R2 | 🟡 MEDIUM | OPEN (known risk) | TOCTOU send-before-record в webhook handler (`app.py:308-370`) |
| L1-R2 | 🟢 LOW | OPEN | Stale `get_recent_message` mocks в ~15 тестах `test_webhook.py` |
| L2-R2 | 🟢 LOW | OPEN | Zombie pending record на "skipped" (`cron.py:397-402`, unreachable) |

---

### Новые находки

#### M1-NEW. Миграция: `CREATE UNIQUE INDEX` упадёт при наличии дубликатов в production DB

**Где:** `db.py:44-46` (index definition), `db.py:162-163` (`init_db`), `db.py:96-99` (`migrate_db` idempotent path)

T12-T14 код использовал time-based dedup (`get_recent_message`, 10 мин) для webhook-линий. Если один и тот же лид попал в целевой статус дважды с интервалом >10 мин (re-entry или повторный webhook при даунтайме сервиса), в БД есть два ряда с одинаковым `(kommo_lead_id, line)`.

T15 добавляет `CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_webhook_lines ON messages(kommo_lead_id, line) WHERE line IN (...)`. `IF NOT EXISTS` предотвращает повторное создание **только если индекс уже существует** по имени. Но если индекс НЕ существует (первый деплой T15), а в данных есть дубликаты, SQLite откажет: `IntegrityError: UNIQUE constraint failed`.

**Путь выполнения при деплое T15:**
1. `init_db()` → `CREATE TABLE IF NOT EXISTS` — no-op (таблица от T12 уже есть)
2. `init_db()` → `CREATE UNIQUE INDEX IF NOT EXISTS idx_dedup_webhook_lines...` → **FAIL** если дубликаты → исключение → сервер не стартует
3. До `migrate_db()` даже не доходит

**Сценарий дубликатов:**
1. Лид X перешёл в статус Б1 → webhook → `berater_accepted` запись создана (t=0)
2. Лид X ушёл из статуса → вернулся (t=15мин) → `get_recent_message(X, berater_accepted, 10)` = None → вторая запись
3. Деплой T15 → `init_db()` → `CREATE UNIQUE INDEX` → **IntegrityError**

**Вероятность:** Низкая (2 дня production, re-entry маловероятен). Но если дубликат есть — это deployment blocker, сервер не поднимется.

**Fix:** Добавить dedup-шаг в `init_db()` (или `migrate_db()`) перед созданием UNIQUE index:
```sql
-- Удалить более старые дубликаты, оставив первую запись
DELETE FROM messages WHERE rowid NOT IN (
    SELECT MIN(rowid) FROM messages
    WHERE line IN ('gosniki_consultation_done', 'berater_accepted')
    GROUP BY kommo_lead_id, line
) AND line IN ('gosniki_consultation_done', 'berater_accepted');
```

Альтернатива: проверить `SELECT COUNT(*) FROM (SELECT kommo_lead_id, line FROM messages WHERE line IN (...) GROUP BY kommo_lead_id, line HAVING COUNT(*) > 1)` и залогировать warning перед dedup.

---

#### L1-NEW. Zombie pending при crash/OOM между reserve и update (расширение L2-R2)

**Где:** `cron.py:358-422` — in-window backfill path

L2-R2 описал zombie при `result["status"]=="skipped"` (unreachable). Но тот же эффект возникает при любом crash/OOM/unhandled exception между `create_message(pending)` (строка 359) и `update_message(sent)` (строка 409):

```
create_message(pending, attempts=0, next_retry_at=None)  ← DB record created
    ← crash window ←
send_message() / update_message()                        ← never reached
```

Запись `pending, attempts=0, next_retry_at=NULL` → `get_webhook_line_exists()` = True → лид **навсегда** заблокирован от повторной обработки. WhatsApp НЕ отправлен.

`process_pending()` не подберёт: `WHERE next_retry_at <= ?` — NULL не проходит.
`process_retries()` не подберёт: `WHERE status IN ('sent', 'failed')`.

**Mitigation (для будущего, не blocker):** Добавить в cron cleanup query:
```sql
DELETE FROM messages
WHERE status = 'pending' AND attempts = 0 AND next_retry_at IS NULL
  AND line IN ('gosniki_consultation_done', 'berater_accepted')
  AND created_at < datetime('now', '-2 hours');
```

**Severity:** LOW — требует crash в узком окне (~1-5 сек).

---

#### L2-NEW. Temporal trigger менее защищён при ошибках контакта, чем backfill

**Где:** `cron.py:516-536` (temporal) vs `cron.py:297-317` (backfill)

Backfill:
```python
except (KeyError, TypeError, ValueError, KommoAPIError) as exc:  # ← 4 типа
```

Temporal trigger:
```python
except KommoAPIError as exc:  # ← только 1 тип
```

Если `main_contact["id"]` выбросит `KeyError` (нестандартный ответ Kommo API), в backfill это перехватится и лид пропустится с алертом. В temporal trigger — нет: ошибка уйдёт наверх через `process_temporal_triggers()` в `main()`, сработает `except Exception` → `alert_cron_error` → return 1. **Все оставшиеся лиды не будут обработаны** в этом cron-прогоне.

**Severity:** LOW — pre-existing T13 issue, Kommo API стабильно возвращает `id`. Но несогласованность создаёт latent risk.

---

#### L3-NEW. Unit-тест фильтрации по status_id покрывает только pipeline Г1

**Где:** `test_cron_webhook_backfill.py:50-56`

```python
def get_active(pipeline_id):
    if pipeline_id == 10935879:          # ← только Г1
        return [_make_lead(1, 10935879, 95514983, ...), ...]
    return []                            # ← Б1 всегда []
```

Тест `test_backfill_filters_by_target_status_id` проверяет фильтрацию `status_id` только для pipeline 10935879 (Г1). Для pipeline 12154099 (Б1) mock возвращает `[]` — фильтрация не тестируется.

**Severity:** LOW — integration-тест `test_backfill_berater_accepted_sent_and_idempotent` покрывает Б1 e2e. Но unit-тест не ловит баг типа «для Б1 фильтр по `status_id` пропущен».

---

### Итог ревью #3

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| M1-NEW | 🟡 MEDIUM | `db.py:44-46, 162-163` | Migration UNIQUE index fails if production has duplicate webhook-line records |
| L1-NEW | 🟢 LOW | `cron.py:358-422` | Zombie pending при crash между reserve и update — лид навсегда заблокирован |
| L2-NEW | 🟢 LOW | `cron.py:516-536` | Temporal trigger catches only KommoAPIError, не KeyError/TypeError/ValueError |
| L3-NEW | 🟢 LOW | `test_cron_webhook_backfill.py:50` | Unit-тест фильтрации только для Г1, не для Б1 |

+ 4 незакрытых из ревью #2 (M1-R2, M2-R2, L1-R2, L2-R2)

---

### Сравнение с предыдущими ревью (обновлённое)

| Ревью | HIGH | MEDIUM | LOW | Контекст |
|-------|------|--------|-----|----------|
| T12 первое | 0 | 2 | 8 | Первичная разработка |
| T13 первое | 2 | 3 | 2 | Первичная разработка |
| T13 второе (после фиксов) | 1 | 2 | 1 | |
| T13 третье (финальное) | 0 | 1 | 4 | |
| T15 первое | 2 | 3 | 3 | Первичная разработка |
| T15 второе (после фиксов) | 0 | 2 | 2 | 8/8 фиксов корректны |
| **T15 третье (текущее, independent)** | **0** | **1** | **3** | +4 open из R2 |

---

### Вывод: не стало хуже

Третий independent review нашёл **1 новую MEDIUM** (deployment risk) и **3 LOW**. Все 8 фиксов по первому ревью подтверждены корректными:

- **H1 (lifetime dedup):** Webhook handler правильно использует `get_webhook_line_exists()` для `_TERMIN_OPTIONAL_LINES`, `get_recent_message()` для остальных. Три `IntegrityError` catch на месте. ✅
- **H2 (record-before-send):** Backfill корректно реализует reserve→send→update. IntegrityError до отправки = нет дубля клиенту. ✅
- **Все M/L фиксы** (M1-M3, L1-L3 из R1): подтверждены.

**Единственный blocker перед деплоем:** M1-NEW — если в production DB есть дубликаты `(kommo_lead_id, line)` для webhook-линий, `init_db()` упадёт. Fix: добавить dedup-шаг перед `CREATE UNIQUE INDEX` или проверить вручную перед деплоем:

```sql
SELECT kommo_lead_id, line, COUNT(*) as cnt
FROM messages
WHERE line IN ('gosniki_consultation_done', 'berater_accepted')
GROUP BY kommo_lead_id, line
HAVING cnt > 1;
```

Если результат пуст — деплой безопасен. Если нет — нужен dedup.

4 находки из ревью #2 (M1-R2, M2-R2, L1-R2, L2-R2) по-прежнему open. Рекомендация: M1-R2 (тесты IntegrityError) закрыть перед/сразу после деплоя, остальное — в следующий стабилизационный цикл.
