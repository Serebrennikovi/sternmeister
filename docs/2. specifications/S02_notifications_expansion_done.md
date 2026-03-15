# Функциональная спецификация: Расширение системы уведомлений

**ID:** S02
**Статус:** done
**Версия:** 3.6
**Дата:** 2026-03-14
**Автор:** Иван Серебренников

---

## Цель

Расширить систему WhatsApp-уведомлений (S01) для поддержки полного жизненного цикла клиента в воронках "Бух Бератер" и "Бух Гос". S01 отправляет 1 тип сообщения при смене этапа. S02 добавляет цепочку из 6 сообщений, привязанных к дате термина и этапу воронки, с защитой от ложных срабатываний при отмене/переносе.

Бизнес-цель: снизить 70% отвал клиентов между этапами подготовки и прохождения термина.

### Апдейт T17 final sync (13.03.2026)

- Send window сервиса переведён на `08:00-22:00 Europe/Berlin`.
- Customer-facing template contract унифицирован до `с Бератором`; legacy `Jobcenter` / `Agentur für Arbeit` / `AA` не должны попадать в новые `templateValues`, retry/backfill или логируемый `message_text`.
- Через Wazzup API подтверждены финальные approved GUID:
  - Г1 `gosniki_consultation_done` → `95ddec60-bb6b-44a8-b5fb-a98abd76f974`
  - Б1 `berater_accepted` → `47d2946c-f66a-4697-b702-eb5d138bb1f1`
  - Б2 `berater_day_minus_7` → `b028964c-9c27-4bc9-9b97-02a5e283df16`
  - Б3 `berater_day_minus_3` → `e1cb07aa-5236-4f8a-84dc-fef26b3cccf6`
  - Б4 `berater_day_minus_1` → `a9b04e05-6b6c-4a5f-9463-d8a0d96316f4`
  - Б5 `berater_day_0` остаётся `176a8b5b-8704-4d04-aee5-0fbd08641806`
- Финальные контракты переменных:
  - Г1: 2 vars (`SternMeister`, `news_text`)
  - Б1: 1 var (`name`)
  - Б2: 4 vars (`name`, `date`, `institution`, `checklist_text`)
  - Б3: 3 vars (`name`, `institution`, `schedule_text`) + quick reply `Нужна помощь`
  - Б4: 2 vars (`name`, `datetime_text`) + quick reply `Да, буду` / `Нет, не смогу`
- Retry/pending/backfill нормализуют legacy `template_values` в этот финальный контракт, а stale Б1 (`berater_accepted`) терминально гасится, если у сделки уже активен более поздний temporal-state (`-3/-1/0`).
- Для Б2 по АА действует stage-gate: `berater_day_minus_7` уходит только на этапах `102183943` / `102183947`; по одной дате без нужного этапа сообщение не отправляется.
- Manual Wazzup создание и approval шаблонов завершены; до финального акцепта T17 остаётся только optional render-check на whitelist-номере.

> Ниже остаются исторические разделы T12-T16. Если они противоречат этому блоку, актуальным считается именно этот блок T17 final sync.

---

## Scope

### Входит:

- 6 новых типов WABA-сообщений (1 Госники + 5 Бератер: Б1–Б5)
- Webhook-триггеры: отправка при смене этапа воронки (мгновенно)
- Temporal-триггеры: отправка по расписанию относительно даты термина (cron)
- Защита от ложных срабатываний: проверка этап + дата (СТОП-этапы "отменён/перенесён")
- Персонализация: имя клиента, дата термина, день недели, customer-facing контекст `с Бератором`
- Дедупликация temporal-сообщений: одно сообщение каждого типа на (lead_id, termin_date)
- Обновление PIPELINE_CONFIG для обеих воронок (новые status_id)
- Расширение MessageData и WazzupMessenger для работы с несколькими шаблонами

### Не входит:

- Email-канал (решение заказчика — убран из scope)
- SMS-канал
- Вложения (PDF-чеклисты, инструкции)
- Шаблон "Просьба о переносе термина" (Email-only, не прошёл модерацию WABA)
- A/B тестирование текстов
- Веб-интерфейс для редактирования шаблонов
- Воронки кроме "Бух Бератер" и "Бух Гос"

---

## Архитектура и структура

### Архитектура

```
┌──────────┐                          ┌──────────────┐
│  Kommo   │──webhook (status change)→│ Python-сервис │
│ (воронка)│                          │              │
└──────────┘                          │  ┌────────┐  │   WABA    ┌───────────┐
                                      │  │ SQLite │  │─────────→│ Wazzup24  │
    ┌──────────────────────────────── │  │ (логи) │  │           └───────────┘
    │ Cron (каждый час)               │  └────────┘  │
    │ • S01: retries + pending        │              │
    │ • S02: temporal triggers ← NEW  │              │──→ Kommo API (примечание + чтение лидов)
    └──────────────────────────────── │              │──→ Telegram (алерты)
                                      └──────────────┘
```

**Ключевое отличие от S01:** S01 реагирует только на webhook (смена этапа). S02 добавляет cron-задачу, которая сама ходит в Kommo API, читает даты термина активных лидов и отправляет сообщения по расписанию.

### Структура проекта (изменения)

```
server/
├── app.py              # [ИЗМЕНЕНИЯ] Расширенный PIPELINE_CONFIG, новые line-типы
├── cron.py             # [ИЗМЕНЕНИЯ] Новая функция process_temporal_triggers()
├── config.py           # [ИЗМЕНЕНИЯ] PIPELINE_CONFIG, STOP_STATUSES, TEMPLATE_MAP, новые field_id
├── db.py               # [ИЗМЕНЕНИЯ] Расширенный CHECK constraint, новые индексы, миграция
├── kommo.py            # [ИЗМЕНЕНИЯ] get_active_leads(), extract_name(), extract_termin_date_dc(), extract_termin_date_aa()
├── utils.py            # [ИЗМЕНЕНИЯ] weekday_name(), format_date_ru()
├── messenger/
│   ├── __init__.py     # [ИЗМЕНЕНИЯ] Расширенный MessageData
│   └── wazzup.py       # [ИЗМЕНЕНИЯ] Маппинг line → template_guid, многошаблонная отправка
└── alerts.py           # без изменений
```

---

## Модели данных / БД

### Изменения в таблице `messages`

**Расширение CHECK constraint для `line`:**

Текущие значения (S01): `'first'`, `'second'`

Новые значения (S02):
- `'gosniki_consultation_done'` — Госники: после консультации 1й линии
- `'berater_accepted'` — Бератер: принято от первой линии (новый шаблон)
- `'berater_day_minus_7'` — за 7 дней до термина
- `'berater_day_minus_3'` — за 3 дня до термина
- `'berater_day_minus_1'` — за 1 день до термина
- `'berater_day_0'` — в день термина (первый cron в send window)

> `'berater_post_termin'` исключён из scope (решение Дмитрия, 04.03.2026). В CHECK constraint и TEMPLATE_MAP не добавляется.

**Миграция:**

SQLite не поддерживает `ALTER TABLE DROP CONSTRAINT`. Реализация через `migrate_db()`:

```python
# Идемпотентность: первым делом проверить наличие колонки template_values
# cursor.execute("PRAGMA table_info(messages)")
# → if any(col[1] == "template_values" for col in cursor.fetchall()): return
```

```sql
-- Паттерн: CREATE new → INSERT INTO new SELECT * FROM old → DROP old → RENAME new
-- Всё в BEGIN/COMMIT для атомарности.
BEGIN TRANSACTION;
CREATE TABLE IF NOT EXISTS messages_new (
    -- полная схема таблицы с новым CHECK constraint
    line TEXT NOT NULL CHECK(line IN (
        'first', 'second',
        'gosniki_consultation_done', 'berater_accepted',
        'berater_day_minus_7', 'berater_day_minus_3', 'berater_day_minus_1',
        'berater_day_0'
    )),
    template_values TEXT,  -- NEW: JSON-массив строк для S02-шаблонов; NULL для S01
    -- остальные поля без изменений
);
INSERT INTO messages_new SELECT *, NULL AS template_values FROM messages;
DROP TABLE messages;
ALTER TABLE messages_new RENAME TO messages;
-- Воссоздать все индексы (CREATE INDEX IF NOT EXISTS)
COMMIT;
```

`migrate_db()` вызывается из `init_db()` после `CREATE TABLE IF NOT EXISTS`. Идемпотентна.

**Поле `template_values` для retry S02-сообщений:**

```sql
template_values TEXT  -- JSON, в актуальном коде keyed-dict с line-specific полями
                      -- legacy JSON-массивы поддерживаются только для обратной совместимости в retry/pending
                      -- NULL для S01-сообщений ('first', 'second')
```

Проблема: старые S02-записи в БД хранили positional/legacy `template_values`, из-за чего retry/pending могли восстанавливать устаревший customer-facing текст или пустые переменные.

Решение: сохранять line-specific keyed JSON в поле `template_values` при каждой новой S02-отправке. При retry/pending/backfill:
- keyed payload используется как источник данных;
- legacy list-format восстанавливается через line-specific normalizer;
- legacy customer-facing поля (`topic/location_text/subject_text/time`) при retry/pending/backfill приводятся к финальному T17-контракту (`news_text`, `institution`, `checklist_text`, `schedule_text`, `datetime_text`) перед повторной отправкой.

Это затрагивает:
- `db.py`: `template_values TEXT` в migrate_db()
- `app.py`: заполнять при сохранении Г1/Б1
- `cron.py` (T13): заполнять при сохранении temporal-сообщений
- `cron.py` (T12): обновить `process_retries()` и `process_pending()` — если `msg["template_values"]` не None, распаковать в MessageData

**Новый индекс для дедупликации temporal-триггеров:**

```sql
CREATE UNIQUE INDEX idx_dedup_temporal
ON messages(kommo_lead_id, line, termin_date)
WHERE line IN (
    'berater_day_minus_7', 'berater_day_minus_3', 'berater_day_minus_1',
    'berater_day_0'
);
```

**Правило: termin_date для webhook-типов (Г1, Б1)**

Шаблоны Г1 и Б1 не используют дату термина как переменную (только `{{1}}=имя`). Тем не менее, схема БД требует `termin_date NOT NULL`. Решение:

- В `app.py`: для line-типов `gosniki_consultation_done` и `berater_accepted` — попытаться извлечь `termin_date`, но **не блокировать отправку** если дата не найдена. Хранить `""` (пустую строку) как допустимое значение.
- Бизнес-правило: менеджеры Бератера заполняют дату термина до перевода на "Принято от 1й линии", но это не гарантировано на стороне CRM.
- `idx_dedup_temporal` через `WHERE line IN (...)` исключает Г1/Б1 из temporal-дедупликации — пустой `termin_date` не сломает индекс.
- Webhook-дедупликация Г1/Б1 работает через существующий `get_recent_message(within_minutes=DEDUP_WINDOW_MINUTES)` — как у S01.

**Изменение в app.py (новое):** выделить множество `_TERMIN_OPTIONAL_LINES = {"gosniki_consultation_done", "berater_accepted"}`. Если `line in _TERMIN_OPTIONAL_LINES` и дата не найдена — продолжить с `termin_date=""`, не делать ранний return.

---

## API Endpoints

### POST /webhook/kommo (изменения)

Расширенная логика `determine_line()` — новые маппинги status_id → line.

Без изменений в сигнатуре и формате ответа.

### GET /health (изменения)

Расширение ответа:

```json
{
  "status": "ok",
  "send_window": "8-22",
  "server_time_utc": "...",
  "server_time_berlin": "...",
  "in_window": true,
  "failed_temporal": 3
}
```

Новое поле `failed_temporal` — количество temporal-сообщений в статусе `failed`, ожидающих retry через `process_retries()`. Temporal-сообщения не записываются как `pending` (только как `sent` или `failed`), поэтому счётчик pending был бы всегда 0.

---

## Авторизация и безопасность

Без изменений относительно S01:
- Kommo webhook: secret-in-URL (hmac.compare_digest)
- Kommo API: Bearer token → `https://sternmeister.kommo.com/api/v4` (НЕ api-c.kommo.com)
- Wazzup24 API: Bearer token
- Telegram Bot API: Bot token

---

## Логика и алгоритмы

### 1. Таблица сообщений

| # | Сообщение | Воронка | line | Триггер | WABA GUID | Переменные | СТОП-этапы |
|---|-----------|---------|------|---------|-----------|------------|------------|
| Г1 | После консультации 1й линии (utility-only) | Бух Гос | `gosniki_consultation_done` | Webhook: status 95514983 "Консультация проведена" | `95ddec60-bb6b-44a8-b5fb-a98abd76f974` | {{1}}=SternMeister, {{2}}=news_text | — |
| Б1 | Уведомление после записи (utility-only) | Бух Бератер | `berater_accepted` | Webhook: status 93860331 "Принято от первой линии" | `47d2946c-f66a-4697-b702-eb5d138bb1f1` | {{1}}=имя | — |
| Б2 | За 7 дней до термина | Бух Бератер | `berater_day_minus_7` | Temporal: termin_date - 7 дней | `b028964c-9c27-4bc9-9b97-02a5e283df16` | {{1}}=имя, {{2}}=дата, {{3}}=с Бератором, {{4}}=checklist_text | `93860875` (ДЦ отменён), `93860883` (АА отменён) |
| Б3 | За 3 дня до термина | Бух Бератер | `berater_day_minus_3` | Temporal: termin_date - 3 дня | `e1cb07aa-5236-4f8a-84dc-fef26b3cccf6` | {{1}}=имя, {{2}}=с Бератором, {{3}}=schedule_text | `93860875`, `93860883` |
| Б4 | За 1 день до термина (utility-only) | Бух Бератер | `berater_day_minus_1` | Temporal: termin_date - 1 день | `a9b04e05-6b6c-4a5f-9463-d8a0d96316f4` | {{1}}=имя, {{2}}=datetime_text | `93860875`, `93860883` |
| Б5 | В день термина (утро) | Бух Бератер | `berater_day_0` | Temporal: termin_date, первый cron в окне (`08:00-22:00`) | `176a8b5b-8704-4d04-aee5-0fbd08641806` | {{1}}=имя | `93860875`, `93860883` |

### 2. PIPELINE_CONFIG (обновлённый)

```python
# Бух Бератер (12154099)
PIPELINE_CONFIG = {
    12154099: {
        93860331: "berater_accepted",           # Принято от первой линии
        # 102183931: Доведение — НЕ триггер, информационный этап
        # 102183935: Консультация перед термином ДЦ — НЕ триггер
        # 102183939: Консультация перед термином ДЦ проведена — НЕ триггер
        # 93886075: Термин ДЦ состоялся — НЕ триггер
        # 102183943: Консультация перед термином АА — НЕ триггер
        # 102183947: Консультация перед термином АА проведена — НЕ триггер
    },
    10935879: {  # Бух Гос (бывш. 10631243 → pipeline_id обновился!)
        95514983: "gosniki_consultation_done",   # Консультация проведена
    },
}

# СТОП-этапы: если лид на одном из этих этапов → temporal-триггеры НЕ отправляются
STOP_STATUSES = {
    12154099: {93860875, 93860883},  # ДЦ отменён/перенесён, АА отменён/перенесён
}
```

**Примечание:** pipeline_id "Бух Гос" изменился с 10631243 (старый "Госники") на 10935879. Старый pipeline 10631243 теперь называется "Бух Комм" (коммерческие продажи, не наш scope).

> ⚠️ **Breaking change (S01→S02/T16/T17):** Маппинг `93860331 → "first"` (S01) заменён на `93860331 → "berater_accepted"` (S02). В T17 линия Б1 финализирована как отдельный onboarding utility-шаблон с одной переменной `{{1}}=имя`.

### 3. Маппинг line → WABA template

```python
TEMPLATE_MAP = {
    # S01 — backward compat (GUID берётся из env WAZZUP_TEMPLATE_ID)
    "first": {
        "template_guid": os.getenv("WAZZUP_TEMPLATE_ID"),
        "vars": lambda name, termin_date, **_: ["SternMeister", "записи на термин", termin_date],
    },
    "second": {
        "template_guid": os.getenv("WAZZUP_TEMPLATE_ID"),
        "vars": lambda name, termin_date, **_: ["SternMeister", "термине", termin_date],
    },
    # S02 — новые шаблоны
    "gosniki_consultation_done": {
        "template_guid": "95ddec60-bb6b-44a8-b5fb-a98abd76f974",
        "vars": lambda news_text, **_: [
            "SternMeister",
            news_text,
        ],
    },
    "berater_accepted": {
        "template_guid": "47d2946c-f66a-4697-b702-eb5d138bb1f1",
        "vars": lambda name, **_: [name],
    },
    "berater_day_minus_7": {
        "template_guid": "b028964c-9c27-4bc9-9b97-02a5e283df16",
        "vars": lambda name, date, institution, checklist_text, **_: [
            name, date, institution, checklist_text
        ],
    },
    "berater_day_minus_3": {
        "template_guid": "e1cb07aa-5236-4f8a-84dc-fef26b3cccf6",
        "vars": lambda name, institution, schedule_text, **_: [
            name, institution, schedule_text
        ],
    },
    "berater_day_minus_1": {
        "template_guid": "a9b04e05-6b6c-4a5f-9463-d8a0d96316f4",
        "vars": lambda name, datetime_text, **_: [name, datetime_text],
    },
    "berater_day_0": {
        "template_guid": "176a8b5b-8704-4d04-aee5-0fbd08641806",
        "vars": lambda name, **_: [name],
    },
}
```

### 4. Расширение MessageData

Текущий `MessageData` (S01) хранит только `line` и `termin_date`. S02 добавляет поля для персонализации temporal-сообщений. `_VALID_LINES` расширяется.

```python
@dataclass
class MessageData:
    line: str               # все валидные значения из TEMPLATE_MAP
    termin_date: str        # "" допустимо для Г1/Б1 (шаблон не использует дату)
    name: str | None = None          # имя клиента
    news_text: str | None = None     # Г1: customer-facing новость
    institution: str | None = None   # customer-facing "с Бератором"
    weekday: str | None = None       # legacy/compat helper field
    date: str | None = None          # дата термина в формате "DD.MM.YYYY"
    time: str | None = None          # legacy/compat helper field
    checklist_text: str | None = None  # Б2: plain-text checklist
    schedule_text: str | None = None   # Б3: "Среда, 19.03.2026"
    topic: str | None = None           # legacy B1 compatibility
    subject_text: str | None = None    # legacy B4 compatibility
    datetime_text: str | None = None   # Б4: дата или дата+время
    location_text: str | None = None   # legacy B1 compatibility
```

`send_message()` передаёт поля в TEMPLATE_MAP lambda через `**dataclasses.asdict(message_data)` — все `**_` поглотят лишнее.

`build_message_text()` для новых типов: возвращать строковое представление переменных через `", ".join(vars)` для лога (точный текст шаблона недоступен без WABA API).

`_VALID_LINES` расширяется до всех ключей `TEMPLATE_MAP`.

### 5. Temporal triggers: алгоритм process_temporal_triggers()

Запускается каждый час в cron (после process_retries и process_pending из S01).

```
def process_temporal_triggers():
    1. Проверить окно отправки (8-22 Berlin). Если вне окна → return.
    2. Получить все активные лиды Бух Бератер (pipeline_id=12154099):
       - Kommo API: GET /leads?filter[pipeline_id]=12154099 с пагинацией (по 250, loop until page empty).
       - Kommo API НЕ поддерживает фильтр "статус НЕ в STOP_STATUSES". Фильтрация по STOP_STATUSES делается в Python после получения.
    3. today = datetime.now(ZoneInfo("Europe/Berlin")).date()  ← ОБЯЗАТЕЛЬНО Berlin, не UTC.
    4. Для каждого лида:
       a. СТОП-проверка: lead["status_id"] in STOP_STATUSES.get(12154099, set()) → пропустить лид.
          > **✅ Решение Дмитрия (04.03.2026):** СТОП-статус блокирует оба термина (ДЦ и АА). Лид находится в одном этапе воронки одновременно. Если статус "ДЦ отменён" (93860875) или "АА отменён" (93860883) — вся работа с лидом останавливается. Менеджер переводит лид в активный этап, если один из терминов продолжается.
       b. Обработать поле ДЦ (887026) и поле АА (887028) независимо.
       c. Для каждого заполненного поля:
          - Извлечь termin_date из поля.
          - Вычислить days_until = (termin_date - today).days.
          - Определить триггер по days_until:
            * 7 → berater_day_minus_7
            * 3 → berater_day_minus_3
            * 1 → berater_day_minus_1
            * 0 → berater_day_0
            * иное → пропустить.
       d. Для каждого триггера:
          - Проверить дедупликацию: уже есть запись в БД с (lead_id, line, termin_date)? → пропустить.
          - Если template_guid = None (заглушка) → пропустить, залогировать INFO с lead_id и termin_date.
          - Извлечь контакт → телефон → имя. При ошибке → skip лид, ERROR-лог, Telegram-алерт.
          - Определить тип учреждения по полю (887026 → "Jobcenter", 887028 → "Agentur für Arbeit").
          - Собрать MessageData (line, termin_date, name, institution, weekday, date).
          - Отправить через WazzupMessenger:
            * Успех → Записать в БД (status="sent") → Добавить примечание в Kommo.
            * MessengerError → Записать в БД (status="failed", attempts=1) → ERROR-лог, Telegram-алерт.
              Дедупликация сохраняется (запись в БД есть). `process_retries()` из S01 подберёт и повторит.
    5. Ошибка get_active_leads() → CRITICAL-лог, Telegram-алерт, ранний return.
```

### 6. Определение типа учреждения

Institution определяется однозначно по полю, из которого взята дата:

```
Итерируемое поле = 887026 (ДЦ) → "Jobcenter"
Итерируемое поле = 887028 (АА) → "Agentur für Arbeit"
```

Логика ДЦ/АА обрабатывается независимо в одном cron-проходе, поэтому institution всегда известен из контекста итерации — проверка по этапам лида не нужна. Fallback "Jobcenter/Agentur für Arbeit" недостижим при temporal-триггерах (если нет даты — нет триггера) и исключён.

### 7. Персонализация

| Переменная | Источник | Fallback |
|-----------|----------|----------|
| Имя клиента | Kommo contact → поле `name` (полное имя) | Ошибка: не отправлять, Telegram-алерт |
| Дата термина | Kommo lead → field 887026/887028/885996 | Ошибка: не отправлять |
| День недели | Вычисляется из даты термина | — |
| Тип учреждения | По полю итерации: 887026 → "Jobcenter", 887028 → "Agentur für Arbeit" | — (недостижимо) |
| Время термина | Kommo lead → field 886670 (date_time) | Не подставлять (убрать из текста) |

### 8. Kommo API: новые поля

| Поле | field_id | Тип | Назначение |
|------|----------|-----|-----------|
| Дата термина | 885996 | date | Общее поле (S01, fallback) |
| Время термина | 886670 | date_time | Время встречи |
| Дата термина ДЦ | 887026 | date | Дата в Jobcenter |
| Дата термина АА | 887028 | date | Дата в Agentur für Arbeit |
| Перевел в термин ДЦ | 889804 | text | Информационное поле |

---

## Acceptance Criteria / DoD

### Функциональность:

- [ ] Webhook от Kommo обрабатывает новые этапы:
  - [ ] Бух Гос: "Консультация проведена" (95514983) → gosniki_consultation_done
  - [ ] Бух Бератер: "Принято от первой линии" (93860331) → berater_accepted (новый шаблон)
- [ ] Cron-задача process_temporal_triggers() каждый час проверяет даты термина:
  - [ ] За 7 дней → berater_day_minus_7 (заглушка, логирование)
  - [ ] За 3 дня → berater_day_minus_3 (с кнопкой "Нужна помощь")
  - [ ] За 1 день → berater_day_minus_1
  - [ ] В день термина → berater_day_0
- [ ] СТОП-этапы работают: лиды на "отменён/перенесён" НЕ получают temporal-сообщения
- [ ] Дедупликация: одно сообщение каждого типа на (lead_id, termin_date)
- [ ] Персонализация: имя, дата, день недели, тип учреждения подставляются корректно
- [ ] Окно отправки 8-22 соблюдается для всех типов
- [ ] Примечания в Kommo создаются
- [ ] Telegram-алерты при ошибках

### Техническое качество:

- [ ] Миграция БД: расширен CHECK constraint, новые индексы
- [ ] Все новые функции покрыты юнит-тестами
- [ ] Интеграционные тесты для temporal-триггеров (freezegun)
- [ ] PIPELINE_CONFIG обновлён для обеих воронок
- [ ] Код следует архитектуре S01

---

## Тест-план

### Юнит-тесты

**test_temporal_triggers.py:**
- [ ] `test_determine_temporal_trigger_day_7/3/1/0()` — корректный маппинг days_until → line
- [ ] `test_stop_status_blocks_temporal()` — СТОП-этап → сообщение не отправляется
- [ ] `test_dedup_temporal()` — повторный вызов для того же (lead, line, date) → пропуск
- [ ] `test_institution_type_dc()` — поле ДЦ заполнено → "Jobcenter"
- [ ] `test_institution_type_aa()` — поле АА заполнено → "Agentur für Arbeit"
- [ ] `test_weekday_calculation()` — русские названия дней
- [ ] `test_template_guid_none_skips()` — заглушка (berater_day_minus_7) → пропуск с логом

**test_db_s02.py:**
- [ ] `test_migrate_db()` — на S01-схеме вызвать `migrate_db()` → новые line-значения проходят CHECK; `idx_dedup_temporal` существует; S01-данные сохранены; повторный вызов не падает (идемпотентность)

**test_webhook_s02.py:**
- [ ] `test_gosniki_consultation_done()` — webhook Бух Гос → gosniki_consultation_done
- [ ] `test_berater_accepted_new_template()` — webhook Бух Бератер → berater_accepted (новый шаблон)
- [ ] `test_unknown_status_ignored()` — неизвестный status_id → None

**test_messenger_s02.py:**
- [ ] `test_send_message_multiple_templates()` — разные line → разные template_guid
- [ ] `test_template_values_gosniki()` — 1 переменная (имя)
- [ ] `test_template_values_3_days()` — 4 переменные (имя, учреждение, день, дата)

### Интеграционные тесты

**test_integration_temporal.py:**
- [ ] Заглушка Б2: лид с датой через 7 дней → cron → INFO-лог, сообщение **НЕ** отправлено, запись в БД **НЕ** создана (GUID=None)
- [ ] СТОП-этап: лид на "отменён" → cron → сообщение НЕ отправлено
- [ ] Дедупликация: два запуска cron → одно сообщение
- [ ] Изменение даты: старая дата → сообщение, новая дата → новое сообщение (разные termin_date)

---

## Зависимости и интеграции

### Требуется до начала разработки:

- [x] S01 завершена и работает в продакшне
- [x] Status_id новых этапов получены (API, 03.03.2026)
- [x] field_id для "Время термина" получен (886670)
- [x] WABA-шаблоны: 6 из 7 одобрены, GUID-ы получены
- [ ] **WABA-шаблон "За 7 дней"** — нужно сократить до 550 символов и переподать (работа Виктора/Дмитрия)

### Порядок реализации:

1. Миграция БД (расширение schema)
2. Обновление config.py (PIPELINE_CONFIG, TEMPLATE_MAP, STOP_STATUSES, field_id)
3. Расширение MessageData и WazzupMessenger (многошаблонность)
4. Расширение webhook handler (новые line-типы)
5. Kommo API: get_active_leads(), extract_termin_date_dc(), extract_termin_date_aa()
6. process_temporal_triggers() в cron.py
7. Тесты
8. Деплой

---

## Открытые вопросы

### ❓ 1. Шаблон "За 7 дней" (Б2) — текст для сокращённой версии

Текущий текст не влезает в 550 символов WABA. Нужно решить, что оставить:
- **Вариант А:** Только напоминание + чек-лист документов (убрать предупреждение о санкциях)
- **Вариант Б:** Только напоминание + предупреждение о санкциях (убрать чек-лист)
- **Вариант В:** Короткое напоминание без чек-листа и санкций

**Кто решает:** Дмитрий → Виктор переподаёт в WABA.
**Блокирует ли:** Нет. Код пишем с заглушкой, GUID добавим когда одобрят.

### ❓ 2. "Принято от первой линии (повторные)" (status_id: 93860327) — нужно ли Б1?

В воронке "Бух Бератер" есть отдельный этап для повторных клиентов (93860327). В S02 маппинг только для 93860331 (первичные). Нужно ли отправлять Б1 для повторных?

**Кто решает:** Дмитрий.
**Блокирует ли:** Нет. По умолчанию повторные клиенты Б1 НЕ получают.

### ✅ 3. Шаблон "Предупреждение о санкциях"

**Ответ (04.03.2026):** Исключён из цепочки (решение Дмитрия). Не входит в S02.

### ✅ 4. Сообщение "После термина" (Б6) — когда отправлять?

**Ответ (04.03.2026):** Исключено из цепочки (решение Дмитрия). Не входит в S02.

### ✅ 5. Temporal-триггеры для АА (второй термин)

**Ответ (04.03.2026):** Да, цепочка Б2–Б6 применяется к обоим терминам (ДЦ и АА). Это явно следует из исходного документа: шаблоны Б4 (за 1 день) и Б5 (в день термина) прямо перечисляют этапы "консультация перед термином ДЦ ИЛИ АА проведена".

Реализация: обрабатываем оба поля (887026 = ДЦ, 887028 = АА) независимо в одном cron-проходе.
- Для ДЦ: СТОП-этап 93860875 ("Термин ДЦ отменён/перенесён")
- Для АА: СТОП-этап 93860883 ("Термин АА отменён/перенесён")
- Дедупликация по (lead_id, line, termin_date): если даты ДЦ и АА разные → клиент получит 2 сообщения каждого типа (одно к каждому термину). Это корректное поведение.
- **Если даты ДЦ и АА совпадают:** ДЦ-итерация (обрабатывается первой) успешно пишет запись в БД по ключу (lead_id, line, termin_date). АА-итерация: `get_temporal_dedup()` возвращает True → пропуск. Клиент получает одно сообщение с institution="Jobcenter". Это **намеренное поведение**: совпадение дат означает один визит — одно напоминание. Отдельного теста не требует сверх существующего dedup-теста.

### ✅ 6. Этап "Документы отправлены в ДЦ" (Бух Гос, 101935919) — нужно ли сообщение?

**Ответ (03.03.2026):** Нет. Никаких уведомлений кроме указанных в документе не нужно.

---

## Риски и ограничения

### Риски:

1. **Нагрузка на Kommo API**: cron каждый час запрашивает активные лиды → rate limiting
   - Митигация: фильтровать лиды по pipeline в запросе; исключать закрытые этапы через `filter[statuses][0]` на уровне API, чтобы не пагинировать по мёртвым записям

2. **Финальный render новых utility-шаблонов нужно spot-check’ить вручную**: approval в Wazzup не гарантирует, что preview и реальный WhatsApp-рендер полностью совпадут с ожиданиями
   - Митигация: whitelist render-check перед акцептом T17, если нужен отдельный финальный smoke

3. **Дублирование при изменении даты термина**: менеджер сменил дату → клиент получит 2 сообщения "за 3 дня" (для старой и новой даты)
   - Митигация: уникальный индекс на (lead_id, line, termin_date) предотвратит точный дубликат

4. **DST-переход**: CET ↔ CEST может сдвинуть отправку
   - Митигация: ZoneInfo("Europe/Berlin") автоматически учитывает DST (как в S01)

### Ограничения:

- SQLite: достаточно для ~60 сообщений/день × 7 типов = ~420 записей/день
- Cron granularity = 1 час: если cron в 9:05 → "утреннее" сообщение уйдёт в 9:05
- WABA-кнопка "Нужна помощь" (QUICK_REPLY): при нажатии клиент отправляет текст "Нужна помощь" → менеджер видит в чате Wazzup, автоответ не реализован
- Лог-шум berater_day_minus_7: пока шаблон не одобрен (GUID=None), каждый cron-прогон логирует INFO для каждого лида с days_until=7. При N активных лидах = N×24 INFO-записей в сутки. Допустимо при текущих объёмах (~60 лидов).

---

## Связанные документы

- **S01:** [S01_whatsapp_auto_notifications_done.md](S01_whatsapp_auto_notifications_done.md) — базовая система
- **Шаблоны сообщений:** [26_02_2026_new_info.md](../5.%20unsorted/26_02_2026_new_info.md) — от Дмитрия
- **Архитектура:** [architecture.md](../architecture.md)
- **HANDOFF:** [HANDOFF.md](../HANDOFF.md)

---

## Задачи

- [x] **T12** — config + schema + webhook/messenger (Г1, Б1): [T12_s02_config_schema_webhook_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T12_s02_config_schema_webhook_done.md) ✅
- [x] **T13** — temporal-триггеры (Б3–Б5): [T13_s02_temporal_triggers_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T13_s02_temporal_triggers_done.md) ✅ 261 тест
- [x] **T14** — деплой S02: [T14_s02_deploy_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T14_s02_deploy_done.md) ✅
- [x] **T15** — fail-safe backfill для пропущенных webhook-триггеров (Г1/Б1): [T15_s02_webhook_backfill_failsafe_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T15_s02_webhook_backfill_failsafe_done.md) ✅ 272 теста
- [x] **T16** — utility-only серия S02 (Г1/Б1/Б2/Б4): [T16_s02_utility_only_template_series_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T16_s02_utility_only_template_series_done.md) ✅
- [x] **T17** — customer-facing text/template cleanup + send window `08:00-22:00`: [T17_s02_customer_facing_text_cleanup_done.md](../3.%20tasks/Done/S02_notifications_expansion_done/T17_s02_customer_facing_text_cleanup_done.md) ✅

---

## История изменений

### v3.6 (2026-03-14) — T17 акцептована, S02 завершена
- T17: customer-facing text/template cleanup + send window `08:00-22:00` — выполнена. 313 тестов (0 failed). Два ревью-цикла: найден и исправлен HIGH (dict-path retry Б2/Б3 не форсировал `CUSTOMER_FACING_BERATER`), добавлен `_non_empty` для Б5. Все 11 критериев приёмки закрыты.
- Файл задачи T17 → Done/. Папка `S02_notifications_expansion/` удалена (пуста).
- **Все 6 задач S02 (T12-T17) выполнены. Спецификация S02 закрыта.**

### v3.5 (2026-03-13) — T17 final sync: approved Wazzup серия + repo/docs sync
- Через Wazzup API подтверждены финальные approved GUID для Г1/Б1/Б2/Б3/Б4; они синхронизированы в `config.py`, retry/backfill normalizers, тестах и документации.
- Финальные переменные зафиксированы как: Г1 `2`, Б1 `1`, Б2 `4`, Б3 `3`, Б4 `2`, Б5 `1`.
- Для Б3 подтверждён quick reply `Нужна помощь`; для Б4 — quick replies `Да, буду` / `Нет, не смогу`.
- Targeted Docker suite для S02 sync: `198 passed`.
- До полного акцепта T17 вне репозитория остаётся только optional whitelist render-check.

### v3.4 (2026-03-13) — T17 repo-side реализована
- `template_helpers.py`: зафиксирован customer-facing контракт T17 (`с Бератором`, `запросу`, без `назначенное время`), добавлены helper’ы для AA `-7` gate и stale-state detection Б1.
- `app.py`: webhook Б1 больше не уходит, если у сделки уже активен более поздний temporal-state; вместо этого сохраняется терминальный no-retry marker в БД.
- `cron.py`: retry/pending/backfill нормализуют legacy/keyed `template_values` в новые customer-facing строки; stale Б1 не досылается; Г1 backfill переведён на keyed payload; Б2 по АА ограничен этапами `102183943/102183947`.
- Тесты: repo-side sync покрыт таргетированным Docker-прогоном S02-набора.

### v3.3 (2026-03-13) — T16 акцептована, создана T17
- Статус спеки обновлён `done` → `active`: после акцепта T16 открыт новый инкремент T17 на customer-facing тексты и шаблоны.
- T16 перемещена в `Done/` и отмечена завершённой.
- Добавлена T17: правки customer-facing copy для Б1/Б2/Б3/Б4, Wazzup template cleanup, безопасный формат списков без квадратов/битых bullet-символов и изменение send window на `08:00-22:00 Europe/Berlin`.

### v3.2 (2026-03-10/2026-03-11) — реализация T16 + hotfix в кодовой базе (исторический этап до final T17 sync)
- `config.py`: Г1/Б1/Б2/Б4 переведены на utility-only шаблоны как промежуточный этап перед финальной T17-синхронизацией.
- `kommo.py`: добавлен `extract_time_termin()` (`field_id=886670` → `HH:MM`, Europe/Berlin).
- `app.py`: для Б1 внедрён winner-алгоритм DC/AA, line-specific composite поля и keyed `template_values`.
- `cron.py`: Б2 включён как реальная отправка; Б4 использует line-specific composite поля; backfill Б1 переведён на keyed `template_values`; retry поддерживает legacy Б1 list-format через fallback-реконструкцию; `process_pending()` больше не блокируется текущим send window.
- Тесты: обновлены unit/integration проверки для utility-only серии и legacy retry совместимости.

### v3.1 (2026-03-10) — создана T16 (utility-only серия)
- Зафиксирован production-кейс частичной недоставки MARKETING-шаблонов (Б1/Б4) при успешной API-отправке.
- Подтверждён approved status utility-шаблона Б2 в Wazzup, но код на тот момент ещё оставался в режиме placeholder.
- Спека S02 переоткрыта под T16: подключение Б2 и переход цепочки Б1/Б2/Б4 на utility-only шаблоны для полной серии сообщений.

### v3.0 (2026-03-06) — T15 акцептована, S02 stabilization завершена
- T15: fail-safe backfill webhook-линий (Г1/Б1) — выполнена. 272 теста (0 failed, 1 skipped). Три ревью-цикла: 2 HIGH (IntegrityError в webhook handler, send-before-record в backfill) — все исправлены. record-before-send паттерн, lifetime dedup, partial unique index `idx_dedup_webhook_lines`. Файл задачи → Done/.
- Все 4 задачи спеки (T12-T15) завершены. S02 stabilization закрыта.

### v2.9 (2026-03-06) — добавлена T15 (stabilization)
- После production-проверки выявлен сценарий потери webhook-события для автосозданной дочерней сделки (Бератер), из-за чего сообщение Б1 не создаётся в `messages` и не отправляется.
- Спека S02 переоткрыта для стабилизационной задачи T15: fail-safe backfill webhook-линий (Г1/Б1) через cron с идемпотентной защитой от дублей.

### v2.8 (2026-03-06) — T14 акцептована, S02 завершена
- T14: деплой S02 на Hetzner — выполнен. Миграция БД завершена, все критерии приёмки пройдены. S02 работает в продакшене: Г1 + Б1 webhooks + B3-B5 temporal triggers активны. Все 3 задачи спеки (T12-T14) завершены → спецификация S02 закрыта.

### v2.7 (2026-03-04) — T13 акцептована
- T13: temporal-триггеры (Б3–Б5) — выполнена. 261 тест (0 failed), 1 skipped. Три ревью-цикла: 2 HIGH (ретрай temporal ×3, IntegrityError) + 1 HIGH (fail→retry-success) — все исправлены. Файл задачи → Done/.

### v2.6 (2026-03-04) — T12 акцептована
- T12: config + schema + webhook/messenger (Г1, Б1) — выполнена. 205 тестов (0 failed). Два ревью-цикла: все MEDIUM/LOW исправлены. Файл задачи → Done/.

### v2.5 (2026-03-04) — фиксы ревью #2
- Версия и дата заголовка обновлены (2.0/2026-03-03 → 2.5/2026-03-04)
- "Порядок реализации" п.5: убраны несуществующие функции `get_lead_status()` и `extract_termin_time()`; добавлены реальные `extract_termin_date_dc()`, `extract_termin_date_aa()` (из T13)
- Таблица персонализации "Тип учреждения": убран fallback `"Jobcenter/Agentur für Arbeit"` (противоречил тексту ниже, где написано что fallback недостижим); исправлен источник → по полю итерации
- PIPELINE_CONFIG: добавлено явное предупреждение о breaking change `93860331: "first"→"berater_accepted"` при S01→S02
- utils.py в структуре проекта: `format_date_with_weekday()` → `format_date_ru()` (согласовано с T13)
- Интеграционный тест: "7 дней → сообщение отправлено" → "Заглушка Б2: INFO-лог, сообщение НЕ отправлено, запись в БД НЕ создана"
- Алгоритм `process_temporal_triggers()`: "5b." → "5."
- Открытые вопросы: ❓ переупорядочены (1→7 дней, 2→повторные лиды); ✅ перенумерованы 3-6 без дублей

### v2.4 (2026-03-04) — ревью-фиксы
- SQL-миграция: `SELECT *` → `SELECT *, NULL AS template_values` (без явного NULL INSERT падает на несоответствии числа колонок)
- `migrate_db()`: добавлена проверка идемпотентности через `PRAGMA table_info` перед BEGIN TRANSACTION
- Персонализация: `first_name` → `поле name (полное имя)` — согласовано с T12 и Kommo API (нет отдельного first_name поля)
- Риск 1 (Kommo rate limiting): убрана несуществующая митигация "кэшировать на 1 час" (не реализуется); добавлена реальная митигация — фильтр закрытых этапов на уровне API
- СТОП-статус: добавлено `✅ Решение Дмитрия (04.03.2026)` — блокирует оба термина

### v2.3 (2026-03-04) — фиксы критов
- Добавлено поле `template_values TEXT` в схему БД: хранит JSON-массив переменных шаблона для корректного retry S02-сообщений через `process_retries()` / `process_pending()`
- Описана retry-логика для S02: десериализация template_values → заполнение MessageData
- Исправлено противоречие architecture.md: status 95514983 "Консультация проведена" принадлежит Бух Гос (10935879), а не Бух Бератер; architecture.md обновлён

### v2.2 (2026-03-04) — ревью и фиксы
- Статус `draft` → `active` (спека закрыта, в работе)
- Счёт: "7 сообщений" → "6 сообщений" (артефакт удалённого berater_post_termin)
- Scope: "7 типов (1+6)" → "6 типов (1 Госники + 5 Бератер)"
- Нумерация разделов: исправлен дубль "5." → 5/6/7/8
- СТОП-статус: добавлено явное примечание (блокирует оба термина, это решение)
- Error handling в `process_temporal_triggers()`: при MessengerError → `status='failed'`, `process_retries()` подберёт; при падении `get_active_leads()` → ранний return
- Определение institution: упрощено (по полю, без проверки этапов); fallback убран как недостижимый
- `migrate_db()`: добавлен детальный SQL-алгоритм (пересоздание таблицы, BEGIN/COMMIT)
- `/health`: `pending_temporal` → `failed_temporal` (pending всегда 0 для temporal-сообщений)
- Ограничения: добавлен лог-шум berater_day_minus_7
- Тест-план: добавлен `test_migrate_db()`; убран `test_institution_type_fallback()` (fallback удалён)
- Открытые вопросы: добавлен ❓2 про "повторные" лиды (93860327)

### v2.1 (2026-03-04)
- Убран `berater_post_termin` из списка новых line-значений (исключён из scope, был внутренним противоречием)
- Добавлено правило: termin_date для Г1/Б1 — необязательна для шаблона, сохраняется `""` если не заполнена; app.py изменяется через `_TERMIN_OPTIONAL_LINES`
- Добавлена детальная структура нового MessageData (optional поля: name, institution, weekday, date)
- Алгоритм process_temporal_triggers(): уточнён timezone today (Berlin), исправлена СТОП-проверка, описана пагинация Kommo API, убрана вводящая в заблуждение фраза про фильтр "НЕ в STOP_STATUSES" на стороне API
- Добавлен раздел "Задачи" со ссылками на T12/T13/T14

### v2.0 (2026-03-03)
- Полная переработка по шаблону функциональной спецификации
- Email убран из scope (решение заказчика)
- Добавлены реальные status_id (получены из Kommo API 03.03.2026)
- Добавлены реальные WABA template GUID-ы (получены из Wazzup API)
- Добавлен field_id для "Время термина" (886670)
- Pipeline_id Бух Гос уточнён: 10935879 (не 10631243)
- Закрыты блокеры: время термина, тип учреждения
- Открытые вопросы переформулированы предметно

### v1.0 (2026-02-26)
- Первый черновик с незакрытыми вопросами
