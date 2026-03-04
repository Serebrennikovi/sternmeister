**Дата:** 2026-03-04
**Статус:** done ✅ (акцептована 2026-03-04)
**Спецификация:** docs/2. specifications/S02_notifications_expansion.md

# T13 — Temporal-триггеры (Б3–Б5: за 3/1 день и в день термина)

## Customer-facing инкремент

Клиент воронки «Бух Бератер» автоматически получает WhatsApp-напоминания: за 3 дня до термина (с кнопкой «Нужна помощь»), за 1 день и утром в день термина. Напоминания применяются к обоим терминам — ДЦ и АА. Клиенты на СТОП-этапах («отменён/перенесён») сообщений не получают.

## Scope

**kommo.py**
- `get_active_leads(pipeline_id)` — список активных лидов с custom fields (даты ДЦ/АА), полем `status_id` и встроенными контактами. Запрос: `GET /leads?filter[pipeline_id]=<id>&with[]=contacts` + исключение закрытых этапов (won/lost). Параметр `with[]=contacts` обязателен — он возвращает `_embedded.contacts` в теле каждого лида, что позволяет получить contact_id без N отдельных API-вызовов. Затем для каждого лида берём первый contact_id из `lead["_embedded"]["contacts"][0]["id"]` и вызываем существующий `get_contact(contact_id)` для получения имени и телефона.
  > ⚠️ **Верификация по документации обязательна** (task_decomposition_guide §1.4): перед реализацией проверить в актуальной Kommo API v4 docs: (1) точный формат параметра `with[]=contacts` и структуру `_embedded.contacts`, (2) формат фильтрации закрытых этапов (`filter[statuses][]` или другой ключ), (3) формат пагинации (`page`/`limit` vs курсор), (4) rate limits на `GET /leads`. Ссылка: `https://developers.kommo.com/docs/leads`. **Сохранить справочник** с примерами реальных запросов/ответов в `docs/5. unsorted/kommo_api_reference.md` (§1.4 гайда).
- `extract_termin_date_dc(lead)` и `extract_termin_date_aa(lead)` — извлечь дату из field_id 887026 и 887028 соответственно. **Возвращают `datetime.date | None`** (не str, в отличие от существующего `extract_termin_date()` который возвращает str). Конвертация: Unix timestamp → `datetime.fromtimestamp(ts, tz=_BERLIN_TZ).date()`. Строковое представление для БД и шаблона получать через `format_date_ru(date)` в вызывающем коде.

**utils.py**
- `weekday_name(date)` — русское название дня недели. **Реализация:** hardcoded список `["Понедельник", "Вторник", "Среда", "Четверг", "Пятница", "Суббота", "Воскресенье"]` + `date.weekday()`. НЕ использовать `locale` — нестабильно в Docker без установленных locale.
- `format_date_ru(date)` — дата в формате «DD.MM.YYYY»

**db.py**
- `get_temporal_dedup(kommo_lead_id, line, termin_date)` — новая функция: проверить, существует ли уже запись (используется перед отправкой). Запрос: `SELECT 1 FROM messages WHERE kommo_lead_id=? AND line=? AND termin_date=? LIMIT 1`. Возвращает `bool`. Индекс `idx_dedup_temporal` (создан в T12) обеспечивает скорость запроса.

**cron.py**
- `process_temporal_triggers()` — новая функция:
  1. Проверить окно отправки (9–21 Berlin). Если вне — return.
  2. Получить активные лиды Бух Бератер через `get_active_leads(12154099)` с пагинацией (250 лидов/страница, loop до пустой страницы). Фильтрация STOP_STATUSES — в Python, не через API.
  3. `today = datetime.now(ZoneInfo("Europe/Berlin")).date()` ← Berlin, не UTC.
  4. Для каждого лида:
     - СТОП-проверка: `lead["status_id"] in STOP_STATUSES.get(12154099, set())` → пропустить лид. Статус есть в данных лида из п.2, отдельный API-вызов не нужен.
     - Обработать дату ДЦ (field 887026) и дату АА (field 887028) **независимо**.
  5. Для каждой заполненной даты (`termin_date: datetime.date`): `days_until = (termin_date - today).days`.
     - 7 → berater_day_minus_7: GUID=None, залогировать INFO(lead_id, termin_date) → **`continue`** к следующей дате. Шаги 6–8 не выполняются. DB-запись не создаётся.
     - 3 → berater_day_minus_3; 1 → berater_day_minus_1; 0 → berater_day_0
     - другие значения → `continue`
  6. Дедупликация: `termin_date_str = format_date_ru(termin_date)`. Если запись (lead_id, line, termin_date_str) уже есть → `continue`.
  6a. Получить имя клиента:
      ```python
      contact_id = lead["_embedded"]["contacts"][0]["id"]  # IndexError если список пуст
      contact = get_contact(contact_id)
      name = extract_name(contact)
      ```
      Если `contacts` пустой, `contact_id` не найден или `name is None` → skip лид: ERROR-лог, Telegram-алерт, `continue` к следующему лиду. Дата уже не резервирована в БД (дедупликация не сработала) — лид будет повторно обработан на следующем cron-прогоне.
  7. Собрать `MessageData(line=line, termin_date=termin_date_str, name=name, institution=institution, weekday=weekday_name(termin_date), date=termin_date_str)`.
  8. Отправить через WazzupMessenger:
     - Вычислить `template_values = json.dumps(TEMPLATE_MAP[line]["vars"](**dataclasses.asdict(message_data)))` ДО вызова send_message (нужно в обеих ветках ниже). На этом шаге `TEMPLATE_MAP[line]["vars"]` гарантированно не None — berater_day_minus_7 отфильтрован на шаге 5.
     - Успех → записать в БД (status="sent", template_values=template_values) → примечание в Kommo.
     - MessengerError → записать в БД (status="failed", attempts=1, template_values=template_values) → ERROR-лог, Telegram-алерт. Дедупликация сохраняется. `process_retries()` подберёт и повторит с восстановленными данными.
     - Ошибка `get_active_leads()` → CRITICAL-лог, Telegram-алерт, ранний return (не обрабатываем лиды).
- Вызывать `process_temporal_triggers()` в cron-петле после `process_pending()`.

**Тесты**
- `test_temporal_triggers.py`:
  - days_until=3/1/0 → верный line
  - days_until=7 → пропуск (заглушка)
  - days_until=2/-1 → не наш триггер, пропуск
  - СТОП-статус 93860875 → сообщение не отправляется (ДЦ)
  - СТОП-статус 93860883 → сообщение не отправляется (АА)
  - Дедупликация: второй вызов для (lead, line, date) → пропуск
  - Тип учреждения: field 887026 → «Jobcenter»; field 887028 → «Agentur für Arbeit»
  - weekday_name: проверить все 7 дней
  - today вычисляется по Berlin (тест: заморозить время в 23:05 UTC / 00:05 Berlin → today в Berlin на 1 день вперёд от today в UTC)
- `test_integration_temporal.py` (freezegun):
  - Лид с датой ДЦ через 3 дня → cron → сообщение отправлено → запись в БД
  - То же для даты АА через 1 день
  - Два запуска cron → одно сообщение (дедупликация)
  - Лид на СТОП-этапе → cron → сообщение не отправлено
  - **Б2 (days=7):** лид с датой через 7 дней → cron → INFO-лог содержит lead_id и termin_date → `send_message` не вызывается → `SELECT COUNT(*) FROM messages WHERE kommo_lead_id=? = 0` (запись в БД не создана)
  - `get_active_leads` возвращает >250 лидов (2 страницы) → все обрабатываются
  - `get_active_leads()` падает с исключением → функция возвращает без обработки, Telegram-алерт
  - MessengerError на одном лиде → запись `status='failed'` в БД; **остальные лиды продолжают обрабатываться** (continue, не break/return) — тест с N лидами: ошибка на первом, успех на остальных

## Out of scope

- Шаблон Б2 (за 7 дней) — заглушка, ждём WABA GUID от Виктора
- Б6 (после термина) — исключён из scope (решение Дмитрия)
- Webhook-уведомления Г1, Б1 (T12)
- Деплой (T14)

## Как протестировать

1. `docker build -t whatsapp-notifications .`
2. `docker run --env-file .env ... pytest tests/`
3. Все тесты зелёные
4. Вручную (staging/prod): создать тестовый лид с датой ДЦ = сегодня+1 день, статус не в СТОП → запустить cron → проверить WABA сообщение и примечание в Kommo

## Критерии приёмки

1. `process_temporal_triggers()` вызывается в cron-петле
2. Лиды с датой ДЦ или АА через 3/1/0 дней получают соответствующие WABA-сообщения
3. Лиды на СТОП-этапах сообщений не получают (статус проверяется из данных лида через `STOP_STATUSES.get(pipeline_id, set())`, без отдельного API-вызова)
4. Дедупликация работает: повторный запуск cron не создаёт дубликатов
5. Тип учреждения подставляется корректно (Jobcenter / Agentur für Arbeit)
6. `today` вычисляется по берлинскому времени (`ZoneInfo("Europe/Berlin")`)
7. `get_active_leads()` поддерживает пагинацию (все лиды, не только первые 250)
8. Примечания в Kommo создаются при успешной отправке
9. Telegram-алерт при ошибке отправки (MessengerError и contact fetch error)
10. Ошибка `get_active_leads()` → ранний return + CRITICAL алерт (не падает весь cron)
11. При MessengerError: запись `status='failed'` в БД, дедупликация сохранена, `process_retries()` подберёт
12. При сохранении temporal-сообщения в БД заполняется `template_values` (JSON); при retry через `process_retries()` MessageData восстанавливается корректно
13. Все тесты зелёные

## Зависимости

- **Требует:** T12 (config, schema, messenger multi-template)

---

## Код-ревью (2026-03-04)

### 🔴 HIGH — Реальные баги

#### H1. Temporal-сообщения с `status='sent'` подбираются `process_retries()` → клиент получает 3 одинаковых сообщения

**Где:** `cron.py` — `process_temporal_triggers()` + `process_retries()`

После успешной отправки `create_message(status="sent", attempts=1, next_retry_at=now+24h)` создаётся запись, которую `process_retries()` неизбежно подберёт:

```
get_messages_for_retry: WHERE status IN ('sent','failed') AND next_retry_at<=now AND attempts<3
```

Итого: initial send (attempts=1) → retry через 24ч (attempts=2) → retry через 48ч (attempts=3) → стоп. Клиент получает **3 одинаковых WhatsApp-напоминания** вместо 1.

`idx_dedup_temporal` защищает только от дублей в БД (INSERT), но не от повторной отправки через Wazzup уже существующей записи.

> **Примечание:** тот же паттерн существует в S01 для webhook-сообщений с T06. T13 проблему не ввёл, но для temporal-триггеров она значительно хуже: reminder должен срабатывать ровно 1 раз, не 3. Тесты не поймали баг, т.к. `process_retries` в тестах не вызывается совместно с `process_temporal_triggers`.

---

#### H2. Необработанный `sqlite3.IntegrityError` от `idx_dedup_temporal` → cron падает для всех оставшихся лидов

**Где:** `cron.py:369-403` — `create_message()` после отправки

Если два экземпляра cron запустятся одновременно (systemd restart при сбое, ручной запуск):

1. Оба пройдут `get_temporal_dedup()` → `False`
2. Оба вызовут `send_message()` → Wazzup получит оба запроса
3. Второй `create_message()` → `sqlite3.IntegrityError` от UNIQUE-индекса
4. `IntegrityError` не перехватывается внутри `process_temporal_triggers` → поднимается в `main()` как fatal error → cron завершается, все лиды после текущего **не обрабатываются**

Вероятность: низкая при нормальной работе, реальная при рестартах/деплоях.

---

### 🟡 MEDIUM — Корректность и надёжность

#### M1. Контакт берётся по индексу `[0]`, а не по флагу `is_main` — несоответствие с `get_lead_contact()`

**Где:** `cron.py:321` — `contact_id = contacts[0]["id"]`

В `KommoClient.get_lead_contact()` (используется в webhook-обработчике) логика:
```python
main = next((c for c in contacts if c.get("is_main")), contacts[0])
```
В `process_temporal_triggers` — просто `contacts[0]`. Если основной контакт не первый в списке — будет взято имя/телефон другого человека. Редко, но возможно при нескольких linked контактах.

---

#### M2. Позиционная привязка в `_build_message_data` — хрупкая схема восстановления данных

**Где:** `cron.py:56-57`

```python
keys = ("name", "institution", "weekday", "date")
extra = dict(zip(keys, vals))
```

Восстановление работает только пока порядок аргументов в `vars`-лямбдах `TEMPLATE_MAP` совпадает с `keys`. Нет валидации, нет маркировки в JSON. Изменение порядка любой лямбды (например при добавлении нового шаблона) сломает restore данных для retry молча.

Пример: если в будущем `berater_day_minus_3.vars` добавит поле перед `name` — retry применит старые данные с неправильными значениями, не бросив исключения.

---

#### M3. Тест Б2 не проверяет содержимое INFO-лога

**Где:** `test_temporal_triggers.py:276-284` — `test_days_7_does_not_send_message`

Критерий из задачи: «7 → залогировать INFO(lead_id, termin_date)». Тест проверяет только `send_message.assert_not_called()` и `create_message.assert_not_called()`, но не то, что лог содержит `lead_id` и `termin_date`. Регрессия в логировании будет незамеченной.

---

### 🟢 LOW — Качество кода

#### L1. `field_id` дублируется в `_TERMIN_FIELDS` и в диспетчере внутри цикла

**Где:** `cron.py:238-241, 284-287`

```python
_TERMIN_FIELDS = [(FIELD_IDS["date_termin_dc"], "Jobcenter"), (FIELD_IDS["date_termin_aa"], ...)]
...
for field_id, institution in _TERMIN_FIELDS:
    if field_id == FIELD_IDS["date_termin_dc"]:
        termin_date_obj = kommo.extract_termin_date_dc(lead)
    else:
        termin_date_obj = kommo.extract_termin_date_aa(lead)
```

`field_id` в `_TERMIN_FIELDS` нужен только для диспетчеризации, но в цикле снова сравнивается с `FIELD_IDS`. При добавлении третьего поля нужно обновлять в двух местах.

---

#### L2. `contact.get("id")` вместо `contact["id"]`

**Где:** `cron.py:372, 396`

`get_contact()` всегда возвращает dict с `id`. Мягкий `.get("id")` вернёт `None` при отсутствии — нарушит NOT NULL constraint позже, а не в месте ошибки. Лучше `contact["id"]` для fail-fast поведения.

---

### Итог ревью

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| H1 | 🔴 HIGH | `cron.py` | Temporal-сообщения повторяются 3× из-за `process_retries` |
| H2 | 🔴 HIGH | `cron.py` | Concurrent cron → IntegrityError → cron падает для всех лидов |
| M1 | 🟡 MEDIUM | `cron.py:321` | Контакт по индексу, не по `is_main` |
| M2 | 🟡 MEDIUM | `cron.py:56-57` | Хрупкое позиционное восстановление MessageData |
| M3 | 🟡 MEDIUM | `test_temporal_triggers.py:276` | Не верифицируется содержимое INFO-лога для Б2 |
| L1 | 🟢 LOW | `cron.py:238-287` | Дублирование field_id-диспетчеризации |
| L2 | 🟢 LOW | `cron.py:372,396` | `.get("id")` вместо `["id"]` |

**По сравнению с первым ревью T12:** стало хуже. T12 первое ревью: **0 HIGH, 2 MEDIUM, 8 LOW**. T13 первое ревью: **2 HIGH, 3 MEDIUM, 2 LOW**. В T12 HIGH-багов не было вовсе — в T13 два, один из них customer-facing (клиент получает 3 напоминания вместо 1). LOW замечаний меньше, но это не компенсирует появление HIGH-уровня.

---

## Результаты выполнения (2026-03-04)

**Статус: готово к акцептованию**

### Тесты

```
255 passed, 0 failed (было 205 → стало 255, +50 новых тестов)
```

### Изменённые/созданные файлы

| Файл | Изменение |
|------|-----------|
| `server/kommo.py` | `get_active_leads()` (пагинация, with=contacts), `extract_termin_date_dc()`, `extract_termin_date_aa()`, `_extract_date_from_field()` |
| `server/utils.py` | `weekday_name()`, `format_date_ru()` |
| `server/db.py` | `get_temporal_dedup()` |
| `server/cron.py` | `process_temporal_triggers()`, вызов в `main()`, обновлены импорты |
| `tests/test_temporal_triggers.py` | ~35 unit-тестов: weekday_name, format_date_ru, extract_dc/aa, Berlin today, process_temporal_triggers (days mapping, STOP, dedup, institutions, errors) |
| `tests/test_integration_temporal.py` | ~15 integration-тестов (freezegun + real SQLite): ДЦ/АА, dedup, STOP, Б2 заглушка, пагинация, MessengerError продолжает обработку |
| `docs/5. unsorted/kommo_api_reference.md` | Kommo API GET /leads справочник (filter, pagination, _embedded.contacts) |

### Все критерии приёмки

1. ✅ `process_temporal_triggers()` вызывается в cron-петле (`main()`)
2. ✅ Лиды с ДЦ/АА через 3/1/0 дней получают WABA-сообщения
3. ✅ СТОП-статусы (93860875, 93860883) блокируют оба термина
4. ✅ Дедупликация: повторный запуск не создаёт дубликатов
5. ✅ Institution: 887026→"Jobcenter", 887028→"Agentur für Arbeit"
6. ✅ `today` по берлинскому времени (ZoneInfo("Europe/Berlin"))
7. ✅ `get_active_leads()` поддерживает пагинацию (250/страница)
8. ✅ Примечания в Kommo при успешной отправке
9. ✅ Telegram-алерты при ошибках (MessengerError, contact fetch, KommoAPIError)
10. ✅ `get_active_leads()` ошибка → ранний return + CRITICAL алерт
11. ✅ MessengerError → `status='failed'` в БД, dедупликация сохранена
12. ✅ `template_values` JSON в БД; `_build_message_data()` восстанавливает MessageData
13. ✅ Все тесты зелёные (255 passed)

---

## Фиксы ревью (2026-03-04)

**Статус: все замечания устранены**

### Тесты после фиксов

```
256 passed, 1 skipped (было 255 → стало 256, +1 regression test)
```

### Что исправлено

| # | Файл | Изменение |
|---|------|-----------|
| H1 | `server/cron.py` | `next_retry_at=None` для sent temporal-сообщений → `process_retries()` больше не подбирает их (NULL не проходит `<= now` в SQLite) |
| H2 | `server/cron.py` | `import sqlite3` + `try/except sqlite3.IntegrityError` вокруг обоих вызовов `create_message()` → concurrent cron не крашит обработку оставшихся лидов |
| M1 | `server/cron.py` | `contacts[0]["id"]` → `next((c for c in contacts if c.get("is_main")), contacts[0])["id"]` — консистентно с `get_lead_contact()` |
| M2 | `server/cron.py` | `template_values` хранится как JSON-dict `{"name":..,"institution":..,"weekday":..,"date":..}` вместо позиционного списка; `_build_message_data()` поддерживает оба формата (dict = новый, list = legacy) |
| M3 | `tests/test_temporal_triggers.py` | `caplog` + assertions на lead_id и termin_date в INFO-логе для Б2 |
| L1 | `server/cron.py` | Убран `_TERMIN_FIELDS` с уровня модуля; внутри `process_temporal_triggers()` локальный список `(extractor_callable, institution)` — dispatch через callable, без дублирования `FIELD_IDS` |
| L2 | `server/cron.py` | `contact.get("id")` → `contact["id"]` в обоих местах (fail-fast) |

### Новые тесты

| Тест | Что проверяет |
|------|---------------|
| `test_integrity_error_on_create_continues_other_leads` (unit) | H2: IntegrityError от `create_message` → warning + остальные лиды обрабатываются |
| `test_sent_temporal_not_retried_by_process_retries` (integration) | H1: sent temporal → `next_retry_at=None` → `process_retries()` не меняет запись |
| Обновлён `test_days_7_does_not_send_message` | M3: caplog содержит lead_id и termin_date |
| Обновлён `test_template_values_saved_to_db` | M2: `isinstance(vals, dict)` + `vals["name"]` |
| Обновлён `test_template_values_restored_for_retry` | M2: `tv["name"]` / `tv["institution"]` вместо `tv[0]` / `tv[1]` |

---

## Второе ревью (2026-03-04) — код после фиксов первого ревью

### 🔴 HIGH — Реальные баги

#### H1-NEW. Фикс H1 неполный: `process_retries()` повторно отправляет temporal-сообщение если первичная отправка упала, а retry — прошёл

**Где:** `cron.py:129-135` — `process_retries()`, ветка успешного retry

Сценарий:
1. Temporal trigger: `send_message()` → `MessengerError` → запись `status='failed'`, `next_retry_at=+24h` ← корректно
2. Cron +24ч: `process_retries()` подбирает запись, retry **успешен** → `update_message(status='sent', next_retry_at=+24h)` — `next_retry_at` выставляется в +24ч (не None)
3. Cron +48ч: `get_messages_for_retry()` снова подбирает ту же запись (`status='sent'`, `attempts=2 < 3`, `next_retry_at <= now`) → **отправляет третье сообщение**

Фикс H1 в `process_temporal_triggers()` задаёт `next_retry_at=None` только для **первоначальной** успешной отправки (happy path). Успешная retry в `process_retries()` устанавливает `next_retry_at=+24h` без разбора, является ли сообщение temporal. `test_sent_temporal_not_retried_by_process_retries` проверяет только happy path и не покрывает `fail → retry-success` цепочку.

---

### 🟡 MEDIUM — Корректность и надёжность

#### M1-NEW. Ложный комментарий + двойное вычисление `vars_fn`

**Где:** `cron.py:357-368`

```python
template_values_list = TEMPLATE_MAP[line]["vars"](**dataclasses.asdict(message_data))
# The positional list (template_values_list) is only for the Wazzup API call.   ← неверно
```

`send_message(phone, message_data)` принимает `message_data` и сам вызывает `vars_fn(**dataclasses.asdict(message_data))` внутри (wazzup.py:128). Таким образом `vars_fn` вычисляется **дважды**: в cron.py (для `build_message_text`) и снова в wazzup.py (для API-запроса). Комментарий говорит обратное. Вводит в заблуждение при будущих изменениях.

---

#### M2-NEW. Отсутствует тест `test_no_phone_skips_lead`

**Где:** `cron.py:331-335`, `test_temporal_triggers.py`

Ветка «phone not found → raise KommoAPIError → skip lead + Telegram alert» покрыта кодом, но нет ни unit-, ни integration-теста. Аналогичный `test_no_name_skips_lead` есть (строка 420), `test_no_phone_skips_lead` — нет.

---

### 🟢 LOW — Качество кода

#### L1-NEW. HTTP-пагинация `get_active_leads()` не покрыта тестами на уровне HTTP

**Где:** `kommo.py:253-293`

`test_pagination_two_pages_all_leads_processed` мокирует `get_active_leads()` целиком, не проверяя логику `while True` / `page += 1` внутри. Если изменить параметр `limit=250` или сломать условие выхода — тест не поймает регрессию.

---

### Итог второго ревью

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| H1-NEW | 🔴 HIGH | `cron.py:129-135` | Фикс H1 неполный: fail→retry-success→повторная отправка |
| M1-NEW | 🟡 MEDIUM | `cron.py:357-368` | Ложный комментарий + двойное вычисление vars_fn |
| M2-NEW | 🟡 MEDIUM | `test_temporal_triggers.py` | Нет теста для no-phone path |
| L1-NEW | 🟢 LOW | `kommo.py:253-293` | HTTP-пагинация не покрыта тестами |

**Сравнение с предыдущими ревью:**

| Ревью | HIGH | MEDIUM | LOW |
|-------|------|--------|-----|
| T12 первое | 0 | 2 | 8 |
| T13 первое | 2 | 3 | 2 |
| T13 второе (после фиксов) | 1 | 2 | 1 |

**Вывод: стало хуже, чем T12.** T12 первое ревью — 0 HIGH-багов. Текущее состояние T13 после фиксов — 1 HIGH. Фикс H1 оказался частичным: happy path исправлен, `fail→retry-success` path упущен. Это customer-facing: клиент может получить 2 напоминания вместо 1 при временном сбое Wazzup.

---

## Фиксы второго ревью (2026-03-04)

**Статус: все замечания устранены**

### Тесты после фиксов

```
261 passed, 1 skipped (было 256 → стало 261, +5 новых тестов)
```

### Что исправлено

| # | Файл | Изменение |
|---|------|-----------|
| H1-NEW | `server/cron.py` | `_TEMPORAL_LINES = frozenset(_DAYS_TO_LINE.values())` + в `process_retries()` ветка успеха: `next_retry_at = None if msg["line"] in _TEMPORAL_LINES else +24h` — fail→retry-success путь теперь тоже не допускает повторной отправки |
| M1-NEW | `server/cron.py` | Исправлен ложный комментарий: было «позиционный список только для Wazzup API», стало «список для `build_message_text`; `send_message()` пересчитывает vars для API-запроса самостоятельно» |
| M2-NEW | `tests/test_temporal_triggers.py` | Добавлен `test_no_phone_skips_lead`: `extract_phone=""` → `send_message` не вызывается, `alert_kommo_error` вызывается |
| L1-NEW | `tests/test_temporal_triggers.py` | Добавлен класс `TestGetActiveLeadsPagination` (3 теста): мокируется `_request` напрямую для проверки while/page loop |

### Новые тесты

| Тест | Что проверяет |
|------|---------------|
| `test_fail_then_retry_success_temporal_not_retried_again` (integration) | H1-NEW: fail→retry-success → `next_retry_at=None` → третьей отправки нет |
| `test_no_phone_skips_lead` (unit) | M2-NEW: телефон не найден → лид пропускается |
| `TestGetActiveLeadsPagination::test_two_pages_returns_all_leads` | L1-NEW: 250+50 лидов → 300, 2 HTTP-запроса, page=1 и page=2 |
| `TestGetActiveLeadsPagination::test_204_on_first_page_returns_empty` | L1-NEW: 204 → [], 1 запрос |
| `TestGetActiveLeadsPagination::test_exactly_250_leads_fetches_second_page` | L1-NEW: 250 лидов → делает второй запрос (не останавливается досрочно) |

---

## Третье ревью (2026-03-04) — код после фиксов второго ревью

### 🔴 HIGH — нет

### 🟡 MEDIUM — Корректность и надёжность

#### M1-3RD. `skipped`-ветки в `process_retries()` и `process_pending()` — мёртвый код с латентным infinite-retry

**Где:** `cron.py:119-121` (`process_retries`), `cron.py:211-213` (`process_pending`)

Оба `continue` не обновляют запись в БД. Если путь сработает:
- `process_retries`: `attempts` не инкрементируется, `next_retry_at` не сдвигается → следующий cron подберёт ту же запись → `skipped` → loop.
- `process_pending`: аналогично.

**Текущая достижимость: недостижима.** Единственная placeholder-линия — `berater_day_minus_7`. Записи для неё никогда не создаются в БД (placeholder-check в `process_temporal_triggers` происходит до `get_temporal_dedup()` и до вызова `send_message()`), а temporal-сообщения никогда не создаются со статусом `pending`. Поэтому прямого бага нет.

Риск: T14 или будущий рефакторинг может создать DB-запись с `berater_day_minus_7` — тогда бесконечный retry молча залипнет без алерта. Пример пути: если кто-то переместит placeholder-check после `create_message()` для логирования.

---

### 🟢 LOW — Качество кода

#### L1-3RD. Двойной вызов `weekday_name(termin_date_obj)` в `process_temporal_triggers()`

**Где:** `cron.py:358` и `cron.py:371`

```python
message_data = MessageData(
    weekday=weekday_name(termin_date_obj),   # ← первый вызов
    ...
)
template_values_json = json.dumps({
    "weekday": weekday_name(termin_date_obj),  # ← второй вызов, тот же результат
    ...
})
```

Второй вызов можно заменить на `message_data.weekday`. Функция чистая, результат идентичен.

---

#### L2-3RD. `get_contact()` вызывается дважды для одного лида при активных ДЦ и АА

**Где:** `cron.py:331` внутри цикла `for extract_date, institution in termin_fields`

Если оба поля 887026 и 887028 — триггерные в один день, для одного `contact_id` выполняются 2 вызова `GET /contacts/{id}`. При N лидах с обеими датами нагрузка на Kommo API удваивается для них. Риск — 429 rate limit при большой базе.

Решение: кешировать `{contact_id: contact_data}` в dict до цикла по `termin_fields`.

---

#### L3-3RD. `process_pending()` не проверяет `_TEMPORAL_LINES` при установке `next_retry_at`

**Где:** `cron.py:217-219`

```python
next_retry_at = (
    now + timedelta(hours=RETRY_INTERVAL_HOURS)
).isoformat(timespec="seconds")
```

В `process_retries()` на этом месте стоит `None if msg["line"] in _TEMPORAL_LINES else +24h`. В `process_pending()` аналогичной проверки нет. Если temporal-сообщение когда-либо окажется в `pending`, оно будет повторно отправлено через 24ч.

Сейчас недостижимо: temporal-сообщения создаются только со статусом `sent` или `failed`.

---

#### L4-3RD. `test_no_phone_skips_lead` мокирует `""`, хотя реальный `extract_phone()` возвращает `None`

**Где:** `tests/test_temporal_triggers.py:434`

```python
mock_cron_deps["kommo"].extract_phone.return_value = ""  # phone not found
```

Реальный `KommoClient.extract_phone()` возвращает `None` при отсутствии телефона, не `""`. Оба значения falsy, поэтому `if not phone:` срабатывает одинаково. Тест корректен по поведению, но семантически неточен.

---

### Итог третьего ревью

| # | Серьёзность | Место | Суть |
|---|-------------|-------|------|
| M1-3RD | 🟡 MEDIUM | `cron.py:119-121, 211-213` | Dead code `skipped`-ветки с латентным infinite-retry |
| L1-3RD | 🟢 LOW | `cron.py:371` | Двойной вызов `weekday_name()` |
| L2-3RD | 🟢 LOW | `cron.py:331` | `get_contact()` ×2 per lead при ДЦ+АА |
| L3-3RD | 🟢 LOW | `cron.py:217-219` | `process_pending()` не проверяет `_TEMPORAL_LINES` |
| L4-3RD | 🟢 LOW | `test_temporal_triggers.py:434` | Тест мокирует `""` вместо `None` для phone |

**Сравнение всех ревью:**

| Ревью | HIGH | MEDIUM | LOW |
|-------|------|--------|-----|
| T12 первое | 0 | 2 | 8 |
| T13 первое | 2 | 3 | 2 |
| T13 второе (после фиксов первого) | 1 | 2 | 1 |
| **T13 третье (текущее)** | **0** | **1** | **4** |

**Вывод: стало лучше по сравнению со вторым ревью T13.** HIGH-баги устранены полностью (2→1→0). MEDIUM сократился до 1 (latent, не customer-facing). По сравнению с T12 первым ревью (эталон «как должно быть»): HIGH равен 0 (хорошо), MEDIUM меньше (1 vs 2), LOW больше (4 vs 8), но новые LOW преимущественно latent или efficiency. Код после трёх ревью-циклов находится в приемлемом состоянии для деплоя.
