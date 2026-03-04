**Дата:** 2026-03-04
**Статус:** done
**Спецификация:** docs/2. specifications/S02_notifications_expansion.md

# T12 — Конфиг, схема БД, webhook + messenger для Г1 и Б1

## Customer-facing инкремент

Клиент воронки «Бух Гос» получает WhatsApp при переводе на «Консультация проведена». Клиент воронки «Бух Бератер» получает WhatsApp при переводе на «Принято от первой линии». Оба сообщения — новые WABA-шаблоны (Г1 и Б1).

## Scope

**config.py**
- PIPELINE_CONFIG: исправить на актуальные pipeline_id и status_id
  - Бух Бератер (12154099): 93860331 → `berater_accepted`
  - Бух Гос (10935879): 95514983 → `gosniki_consultation_done`
  - Удалить старый pipeline 10631243 (Бух Комм — не наш scope)
- Добавить `STOP_STATUSES = {12154099: {93860875, 93860883}}`
- Добавить `TEMPLATE_MAP` — маппинг line → template_guid + lambda vars (данные из спеки раздел «Маппинг line → WABA template»)
- Добавить `FIELD_IDS["time_termin"] = 886670`
- `WAZZUP_TEMPLATE_ID`: **оставить как `_require`** — он используется в TEMPLATE_MAP для 'first' и 'second' (S01-шаблоны). Удаление сломает S01. Просто перестаёт быть единственным шаблоном системы.

**db.py**
- Добавить функцию `migrate_db()`: SQLite не поддерживает ALTER TABLE DROP CONSTRAINT — пересоздать таблицу с новым CHECK через BEGIN/COMMIT:
  0. **Идемпотентность (обязательно первым шагом):**
     ```python
     cursor.execute("PRAGMA table_info(messages)")
     cols = [row[1] for row in cursor.fetchall()]
     if "template_values" in cols:
         return  # миграция уже выполнена
     ```
  1. `CREATE TABLE messages_new (... CHECK(line IN ('first','second','gosniki_consultation_done','berater_accepted','berater_day_minus_7','berater_day_minus_3','berater_day_minus_1','berater_day_0')), template_values TEXT, ...)`
     — `template_values TEXT` nullable: JSON-массив строк для S02-шаблонов, NULL для S01
  2. `INSERT INTO messages_new SELECT *, NULL AS template_values FROM messages` (старые записи без template_values)
  3. `DROP TABLE messages` → `ALTER TABLE messages_new RENAME TO messages`
  4. Воссоздать все существующие индексы (`idx_dedup`, `idx_status`, и новый `idx_dedup_temporal`)
  - Вся операция в одном BEGIN/COMMIT
- Вызывать `migrate_db()` из `init_db()` после `CREATE TABLE IF NOT EXISTS`
- **Обновить `create_message()`**: добавить параметр `template_values: str | None = None` и включить его в INSERT-запрос. Это обязательно — иначе Г1/Б1 (webhook) и temporal-сообщения (T13) не смогут сохранить template_values при создании записи.
- **Добавить `"template_values"` в `_ALLOWED_COLUMNS`**: иначе `update_message(template_values=...)` будет падать с ValueError при любых будущих обновлениях поля.

**messenger/wazzup.py**
- Расширить `MessageData`: добавить optional поля `name`, `institution`, `weekday`, `date`; расширить `_VALID_LINES` до всех ключей `TEMPLATE_MAP`; `termin_date` разрешить как `""` (не raise при пустой строке)
- Метод `send_message()`: получать `template_guid` и `template_values` из `TEMPLATE_MAP` по `line`; передавать поля через `**dataclasses.asdict(message_data)` в lambda `vars`
- Если `template_guid is None` (заглушка для berater_day_minus_7) → логировать INFO, возвращать `{"status": "skipped"}`
- `build_message_text()` для новых типов: `"[template] " + ", ".join(template_values)` для лога

**kommo.py**
- Добавить `KommoClient.extract_name(contact_data)` — читает `contact_data.get("name")` (поле верхнего уровня в Kommo contact object; это полное имя, напр. "Иван Иванов"). Возвращает `str | None`. Логировать WARNING если имя не найдено. **Не разделять на first/last** — шаблон принимает полное имя как есть.

**app.py**
- `determine_line()` уже работает через PIPELINE_CONFIG — изменений не требует после обновления конфига
- Добавить `_TERMIN_OPTIONAL_LINES = {"gosniki_consultation_done", "berater_accepted"}`: если `line in _TERMIN_OPTIONAL_LINES` и `termin_date` не найден — продолжить с `termin_date=""` вместо раннего return с ошибкой
- Для `line in _TERMIN_OPTIONAL_LINES`: после получения контакта извлечь имя через `kommo.extract_name(contact)`. Если имя не найдено — ранний return с ошибкой + WARNING-лог (шаблон Г1/Б1 имеет `{{1}}=имя`, без него отправка бессмысленна). Передать в `MessageData(name=name, termin_date=termin_date, ...)`.
- При сохранении Г1/Б1 в БД: заполнять `template_values = json.dumps([name])` — чтобы `process_retries()` мог восстановить `name` при повторной отправке
- Добавить поле `failed_temporal` в ответ `/health`: `SELECT COUNT(*) FROM messages WHERE status='failed' AND line IN ('berater_day_minus_3', 'berater_day_minus_1', 'berater_day_0', 'berater_day_minus_7')`
  (temporal-сообщения не записываются как `pending`, только как `sent` или `failed`)

**cron.py**
- Обновить `process_retries()` и `process_pending()`: при построении MessageData проверять `msg["template_values"]`:
  ```python
  extra = {}
  if msg.get("template_values"):
      vals = json.loads(msg["template_values"])
      keys = ("name", "institution", "weekday", "date")
      extra = dict(zip(keys, vals))
  message_data = MessageData(line=msg["line"], termin_date=msg["termin_date"], **extra)
  ```
  Это единственное изменение в cron.py в T12. Остальная логика без изменений.
- Также обновить существующие S01-тесты для status 93860331: маппинг теперь `berater_accepted`, а не `first`

**Тесты**
- `test_config_s02.py`: TEMPLATE_MAP содержит все line-ключи; STOP_STATUSES корректны; PIPELINE_CONFIG возвращает правильные line для новых status_id
- `test_db_s02.py`: `test_migrate_db()` — на S01-схеме вызвать `migrate_db()` → новые line-значения проходят INSERT без CHECK ошибок; `idx_dedup_temporal` создан; S01-данные сохранены; повторный вызов не падает
- `test_webhook_s02.py`: webhook с status 95514983 → gosniki_consultation_done; webhook с status 93860331 → berater_accepted; неизвестный status → None; Г1/Б1 webhook с именем в контакте → MessageData.name заполнен; Г1/Б1 без имени → ранний return с ошибкой
- `test_messenger_s02.py`: send() с line=gosniki_consultation_done → верный GUID; send() с line=berater_day_minus_7 (заглушка) → skipped; send() с line=berater_day_minus_3 → 4 переменных
- `test_cron_retry_template_values.py`:
  - `test_process_retries_restores_4_vars()` — mock DB-запись (line=berater_day_minus_3, template_values='["Анна","Jobcenter","Среда","25.03.2026"]') → в MessageData: name="Анна", institution="Jobcenter", weekday="Среда", date="25.03.2026" → send_message вызывается с корректными 4 переменными
  - `test_process_retries_restores_1_var()` — mock DB-запись (line=berater_accepted, template_values='["Анна"]') → MessageData.name="Анна", остальные поля None → send_message вызывается с [name]
  - `test_process_retries_no_template_values()` — mock DB-запись S01 (line=first, template_values=None) → MessageData без extra-полей, S01-шаблон работает без изменений (backward compat)
  - `test_process_pending_restores_template_values()` — аналогично для process_pending() (pending → sent после успешной отправки)

## Out of scope

- Temporal-триггеры (T13)
- Kommo API: get_active_leads() (T13)
- Деплой (T14)
- Шаблон Б2 (7 дней) — остаётся заглушкой до получения WABA GUID

## Как протестировать

1. `docker build -t whatsapp-notifications .`
2. `docker run --env-file .env ... pytest tests/`
3. Все тесты проходят
4. Вручную: отправить тестовый webhook с pipeline_id=10935879, status_id=95514983 → проверить что ушло WABA сообщение по шаблону Г1 (d253993f-...)
5. То же для pipeline_id=12154099, status_id=93860331 → шаблон Б1 (18b763f8-...)

## Критерии приёмки

1. PIPELINE_CONFIG содержит корректные pipeline_id и status_id (10935879, 12154099; без 10631243)
2. TEMPLATE_MAP содержит все 6 активных line-типов S02 + заглушку для berater_day_minus_7
3. Миграция БД: новые line-значения проходят CHECK constraint без ошибок
4. Индекс idx_dedup_temporal создан
5. Webhook status 95514983 → сообщение отправлено по шаблону Г1
6. Webhook status 93860331 → сообщение отправлено по шаблону Б1
7. berater_day_minus_7: send() не падает, логирует INFO `skipped` с line и termin_date (lead_id в слое messenger недоступен — логируется в cron-контексте T13), запись в БД **не создаётся** (создаём только при реальной отправке)
8. MessageData принимает новые optional поля; S01-шаблоны ('first', 'second') работают без изменений
9. Webhook Г1/Б1 отправляет сообщение даже если termin_date не заполнена в CRM (хранит `""`)
10. Webhook Г1/Б1: имя клиента извлекается из контакта и передаётся в MessageData; если имя не найдено — ранний return с ошибкой (шаблон требует `{{1}}=имя`)
11. `KommoClient.extract_name()` реализован в kommo.py
12. `/health` содержит поле `failed_temporal` (количество temporal-сообщений в статусе `failed`)
13. Поле `template_values TEXT` добавлено в migrate_db(); Г1/Б1 сохраняют `json.dumps([name])`
14. `process_retries()` и `process_pending()` восстанавливают extra-поля MessageData из `template_values`
15. `create_message()` принимает параметр `template_values: str | None = None` и сохраняет его в БД; `_ALLOWED_COLUMNS` включает `"template_values"`
16. Все тесты зелёные

---

## Код-ревью (2026-03-04) → Фиксы выполнены (2026-03-04)

**Результат: 205 passed, 0 failed** (+13 новых тестов: 5 L7 + 2 L8 + 6 исправлены в test_alerts.py)

### MEDIUM — 2 бага ✅ исправлены

**M1. `migrate_db()` не атомарна** ✅ FIXED `server/db.py`

Создано отдельное соединение с `isolation_level=None` (autocommit mode), заменено `BEGIN` → `BEGIN IMMEDIATE`, добавлен `DROP TABLE IF EXISTS messages_new` перед `CREATE TABLE messages_new` — защита от прерванной миграции при перезапуске.

---

**M2. `process_retries()` и `process_pending()` крашались с `KeyError` при `status="skipped"`** ✅ FIXED `server/cron.py`

Добавлены проверки в обе функции после `send_message()`:
```python
if result.get("status") == "skipped":
    logger.info("Skipped msg %d (line=%s, placeholder template)", ...)
    continue
```

---

### LOW — 8 замечаний

**L1. BEGIN → BEGIN IMMEDIATE** ✅ FIXED — покрыто фиксом M1.

---

**L2. Мёртвый `KeyError` в `_build_message_data()`** ✅ FIXED `server/cron.py:45`

Убран `KeyError` из `except (KeyError, IndexError)` → `except IndexError`. Тесты в `test_alerts.py` которые передавали plain dicts без `template_values` — обновлены (добавлен `"template_values": None`).

---

**L3. `vars_fn` вызывался дважды** ✅ FIXED `server/messenger/wazzup.py`

`build_message_text()` получает опциональный параметр `template_values: list | None = None`. В `send_message()` передаётся уже вычисленный список. Обратно совместимо: вызовы `build_message_text(md)` без параметра вычисляют значения сами.

---

**L4. Нет валидации required-переменных перед отправкой** — отложено на T13 (актуально только для temporal-lines).

---

**L5. `_make_lead()` хардкодил Gosniki-данные в Berater-тестах** ✅ FIXED `tests/test_webhook_s02.py`

Добавлены параметры `pipeline_id` и `status_id`. В `TestBeraterAcceptedWebhook` передаётся `_make_lead(pipeline_id=12154099, status_id=93860331)`.

---

**L6. Слабые assertions в `TestNewStatusMappings`** ✅ FIXED `tests/test_webhook_s02.py`

Два слабых теста (`!= "Status not relevant"`) заменены на прямые вызовы `determine_line()` — точные assertions без необходимости мокать Kommo.

---

**L7. Нет unit-теста для `get_failed_temporal_count()`** ✅ FIXED `tests/test_db_s02.py`

Добавлен класс `TestGetFailedTemporalCount` (5 тестов): considers only failed temporal lines, игнорирует sent temporal, игнорирует failed non-temporal, смешанные записи, пустая БД.

---

**L8. M2-баг не покрыт тестами** ✅ FIXED `tests/test_cron_retry_template_values.py`

Добавлен класс `TestProcessRetriesSkipped` (2 теста): `process_retries()` и `process_pending()` при skipped-результате не крашатся и не вызывают `update_message`.

---

## Результаты выполнения (2026-03-04)

**Статус: готово к акцептованию**

### Тесты

```
205 passed, 0 failed (было 192 → стало 205, +13 новых тестов)
```

Docker: `python:3.11.15`, pytest 8.3.5.

### Изменённые файлы

| Файл | Изменение |
|------|-----------|
| `server/db.py` | M1: `isolation_level=None`, `BEGIN IMMEDIATE`, `DROP TABLE IF EXISTS messages_new` |
| `server/cron.py` | M2: `skipped`-проверка в `process_retries` и `process_pending`; L2: убран мёртвый `KeyError` |
| `server/messenger/wazzup.py` | L3: `build_message_text(md, template_values=...)` — vars_fn вызывается один раз |
| `tests/test_webhook_s02.py` | L5: `_make_lead()` параметризован; L6: слабые assert заменены на `determine_line()` |
| `tests/test_db_s02.py` | L7: `TestGetFailedTemporalCount` — 5 unit-тестов |
| `tests/test_cron_retry_template_values.py` | L8: `TestProcessRetriesSkipped` — 2 теста |
| `tests/test_alerts.py` | L2 side-effect: добавлен `"template_values": None` в 2 mock-дикта |

### Что отложено

- **L4** (валидация required template vars) — актуально только для temporal-lines (T13).

---

## Код-ревью 2 (2026-03-04)

**Результат: 205 passed, 0 failed** — состояние после всех фиксов первого ревью.

Все M1/M2 и L1–L8 из первого ревью исправлены. Новых MEDIUM-багов нет.

### LOW — 6 замечаний (по убыванию серьёзности)

**L-NEW-1. `app.py` не обрабатывает `{"status": "skipped"}` от `send_message`** — `server/app.py:324`

Если `send_message` вернёт `{"status": "skipped"}`, строка `result["message_id"]` бросит `KeyError`. Поймается outer-except в `_process_lead_status`, залогируется как unexpected error, в БД запись не создастся.

Сейчас **недостижимо**: ни один temporal-line не добавлен в `PIPELINE_CONFIG`. Но если в T13/будущем temporal-line попадёт туда — тихий баг без явного теста. Рекомендуется добавить проверку аналогично cron.py (или явный assert что temporal-lines не в PIPELINE_CONFIG).

---

**L-NEW-2. `vars_fn` вызывается дважды в webhook-пути** — `server/app.py:256` + `server/messenger/wazzup.py:128`

`app.py` вызывает `build_message_text(message_data)` → внутри считает `vars_fn`. Затем `send_message` → снова считает `vars_fn`. Фикс L3 устранил двойной вызов только для cron-пути (`send_message` передаёт pre-computed значения в `build_message_text`). Webhook-путь не задет.

Влияние: только производительность (два вызова тривиальных лямбд). Не блокирует.

---

**L-NEW-3. `process_retries/pending` не обновляют `next_retry_at` для skipped-сообщений** — `server/cron.py:106-108, 195-197`

После `{"status": "skipped"}` — `continue` без обновления DB. Если `berater_day_minus_7`-запись окажется в БД (после T13), cron будет забирать её каждый час бесконечно (next_retry_at не сдвигается).

Per T12-spec: "запись в БД не создаётся" для berater_day_minus_7 — если T13 это гарантирует, проблемы нет. Нужно явно проговорить в T13-scope.

---

**L-NEW-4. `STOP_STATUSES` определён, но не используется в production-коде** — `server/config.py:80`

Импортируется только в `test_config_s02.py`. Ожидаемо (T13 scope), но стоит задокументировать явно в T13 — иначе при рефакторинге могут удалить как "dead code".

---

**L-NEW-5. Нет unit-тестов для `vars`-лямбд `berater_day_minus_1` и `berater_day_0`** — `tests/test_config_s02.py`

Тесты проверяют GUID, но не вызов `vars(name=..., **_)`. Обе лямбды идентичны `berater_accepted` (`lambda name, **_: [name]`). Если кто-то случайно сломает лямбду — тест не поймает.

---

**L-NEW-6. Гонка в `migrate_db()`: idempotency-check вне транзакции** — `server/db.py:91-94`

`PRAGMA table_info` выполняется до `BEGIN IMMEDIATE`. Два одновременных процесса могут оба пройти check и оба запустить миграцию; второй перезапишет `template_values` значениями NULL (INSERT явно подставляет NULL).

В production: один Docker-контейнер, одновременный старт невозможен. Чисто теоретическое замечание.

---

### Сравнение с предыдущими ревью

| Ревью | MEDIUM | LOW |
|-------|--------|-----|
| T12 первое (2026-03-04) | 2 → ✅ все исправлены | 8 → ✅ все исправлены (L4 отложен в T13) |
| T12 второе (2026-03-04) | **0** | **6** (3 из них — T13 scope) |

**Не ухудшилось.** Устойчивый паттерн: MEDIUM-баги закрываются в одном ревью-цикле. Новые LOW — только незначительные технические долги и T13-scope заметки.
