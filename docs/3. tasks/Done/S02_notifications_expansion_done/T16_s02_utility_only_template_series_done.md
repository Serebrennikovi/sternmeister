**Дата:** 2026-03-10
**Статус:** done
**Спецификация:** docs/2. specifications/S02_notifications_expansion_done.md

# T16 — Utility-only серия S02 без потери сообщений

## Customer-facing инкремент

Клиент получает полную рабочую серию S02 в WhatsApp (Б1-Б5) на whitelist-номерах без выпадений из-за MARKETING-категории и без пустых шаблонных переменных.

## Фактический baseline (Wazzup snapshot на 2026-03-10 + hotfix 2026-03-11)

| Линия | line | GUID | Status | Category | Переменные |
|---|---|---|---|---|---|
| Г1 | `gosniki_consultation_done` | `89155c52-758e-473a-9e44-dcdc086d206a` | approved | UTILITY | 3 |
| Б1 | `berater_accepted` | `18b763f8-1841-43fb-af65-669ab4c8dcea` | approved | MARKETING | 1 |
| Б2 | `berater_day_minus_7` | `bc64a599-9e88-49c0-be09-4e582778ca47` | approved | UTILITY | 4 |
| Б3 | `berater_day_minus_3` | `140a1ed5-7047-4de1-aa0d-d3fe5e0d912a` | approved | UTILITY | 4 |
| Б4 | `berater_day_minus_1` | `7732e8ac-1bcc-42d6-a723-bbb80b635c79` | approved | MARKETING | 1 |
| Б5 | `berater_day_0` | `176a8b5b-8704-4d04-aee5-0fbd08641806` | approved | UTILITY | 1 |

Б2 (`bc64a599`) применим и для ДЦ, и для АА: учреждение передаётся через `{{4}}` (в тексте нет хардкода `Jobcenter`).

## Scope

- Перевести Б1/Б2/Б4 на UTILITY-шаблоны в `server/config.py`:
  - Б1 (`berater_accepted`) → `3b7211aa-6fbd-4b60-bb96-02d7cc837c73` (`uvedomlenie_o_zapisi_1`, UTILITY).
  - Б2 (`berater_day_minus_7`) → `bc64a599-9e88-49c0-be09-4e582778ca47` (вместо placeholder `None`).
  - Б4 (`berater_day_minus_1`) → `38194e93-e926-4826-babe-19032e0bd74c` (`napominanie_o_zapisi_ili_vstreche_1`, UTILITY).
- Добавить извлечение времени термина из Kommo и единый формат `HH:MM` (Europe/Berlin).
- Убрать возможность пустых `templateValues` для Б1/Б2/Б4 (`""`, `None`, пробелы запрещены).
- Включить Б2 в `process_temporal_triggers()` как реальную отправку (без `skipped`).
- Обновить `process_webhook_backfill()` для Б1 под новый 4-переменный шаблон (не только webhook и temporal).
- Для Б1 перевести сохранение `template_values` с list-формата (`[name]`) на keyed dict.
- Обновить тесты и документацию (`architecture.md`, S02-спека, HANDOFF).

## Обязательное семантическое решение по Б1

Б1 в исходном бизнес-смысле был поздравлением. В T16 фиксируем эксплуатационный компромисс: Б1 отправляется через UTILITY-шаблон уведомления о записи (`3b7211aa`).

## Чёткий приоритет institution для Б1 (webhook)

Для `berater_accepted` в webhook-контексте institution определяется строго так:

1. Если заполнены и распарсены обе даты (`887026` и `887028`) — выбрать ближайшую к текущей дате (Europe/Berlin).
2. Если обе даты равны — приоритет ДЦ: `Jobcenter`.
3. Если заполнена только `887026` — `Jobcenter`.
4. Если заполнена только `887028` — `Agentur für Arbeit`.
5. Если ни одну дату не удалось распарсить — fallback `Jobcenter или Agentur für Arbeit`.

Это правило убирает неоднозначность для лида с двумя терминами.

Для Б1 `date_for_template` берётся из того же поля, которое выбрано для `institution_text` (DC/AA winner).  
Для Б1 нельзя смешивать источник даты с generic-полем `885996`: если winner не определён, `date_for_template` считается отсутствующей.

## Архитектурное решение: где собираются composite-переменные

Композиция `topic` / `subject_text` / `datetime_text` / `location_text` делается ДО `send_message()`, в коде-оркестрации (`app.py`, `cron.py`) через маленькие helper-функции.

`TEMPLATE_MAP[...]["vars"]` остаются тривиальными и не содержат условной бизнес-логики (только раскладка уже готовых строк по позициям `{{N}}`).

## Спецификация `extract_time_termin()`

- Файл: `server/kommo.py`.
- Сигнатура: `def extract_time_termin(lead_data: dict, field_id: int) -> str | None`.
- Вызов:
  - webhook Б1: `extract_time_termin(lead, FIELD_IDS["time_termin"])`.
  - temporal Б2/Б4: тот же вызов.
- Алгоритм:
  1. Найти `custom_fields_values[field_id=886670].values[0].value`.
  2. `ts = int(raw)`.
  3. `dt = datetime.fromtimestamp(ts, tz=ZoneInfo("Europe/Berlin"))`.
  4. Вернуть `dt.strftime("%H:%M")`.
- Ошибки (`ValueError`, `TypeError`, `OSError`) → вернуть `None` (без падения).

## Спецификация переменных и fallback

| line | Шаблон | `{{1}}` | `{{2}}` | `{{3}}` | `{{4}}` |
|---|---|---|---|---|---|
| Б1 `berater_accepted` | `3b7211aa` | `"SternMeister"` | `topic` | `datetime_text` | `location_text` |
| Б2 `berater_day_minus_7` | `bc64a599` | `name` | `date` | `time_text` | `institution_text` |
| Б4 `berater_day_minus_1` | `38194e93` | `"SternMeister"` | `subject_text` | `datetime_text` | — |

Правила:

- `time_text`:
  - `"HH:MM"`, если время распарсено;
  - `"назначенное время"` иначе (важно для Б2: в шаблоне уже есть предлог `в {{3}}`).
- Хранение `time`:
  - в `MessageData.time` и `template_values_json["time"]` хранится уже fallback-applied значение (`time_text`);
  - сырой `None` из `extract_time_termin()` не должен попадать в `MessageData`, БД и `templateValues`.
- `datetime_text` (line-specific):
  - для Б1: `"<date> в <HH:MM>"`, либо `"<date>, назначенное время"`, либо `"дату и время сообщим дополнительно"` (если даты нет);
  - для Б4: `"<date>, <HH:MM>"`, либо `"<date>, назначенное время"` (формат без лишнего предлога `в`, т.к. в шаблоне уже есть `в {{3}}`).
- `topic` (Б1): `"термин в <institution_text>"`.
- `location_text` (Б1): `"в <institution_text>"`.
- `subject_text` (Б4): `"вашем термине в <institution_text>"`.

## Явный diff для `app.py` (Б1 webhook)

В `_process_lead_status_inner()` для `line == "berater_accepted"` сделать:

1. Вычислить пару `institution_text + date_for_template` по приоритету выше (на основе `extract_termin_date_dc/aa`, единый winner-источник).
2. Извлечь `time_text` через новую `extract_time_termin()`.
3. Сформировать `datetime_text`, `topic`, `location_text`.
4. Создать `MessageData` с предсобранными полями.
5. Записать `template_values` в БД как keyed dict (не list):
   - `{"name": name, "institution": institution_text, "date": date_or_none, "time": time_text, "topic": topic, "datetime_text": datetime_text, "location_text": location_text}`.

## Явный diff для `cron.py` (`process_temporal_triggers()`, Б2/Б4)

Для temporal-линий изменения делаются явно по `line`:

1. После извлечения `termin_date_obj` вызвать:
   - `time_raw = kommo.extract_time_termin(lead, FIELD_IDS["time_termin"])`
   - `time_text = time_raw or "назначенное время"`.
2. Для Б2 (`berater_day_minus_7`):
   - собрать `MessageData(name, institution, date, time=time_text)`;
   - отправлять `{{1}}=name, {{2}}=date, {{3}}=time_text, {{4}}=institution`;
   - `template_values_json = {"name","institution","date","time"}`, где `"time" = time_text` (не `None`).
3. Для Б3 (`berater_day_minus_3`) оставить текущую логику:
   - `{{1}}=name, {{2}}=institution, {{3}}=weekday, {{4}}=date`;
   - `template_values_json = {"name","institution","weekday","date"}`.
4. Для Б4 (`berater_day_minus_1`):
   - вычислить `subject_text` и line-specific `datetime_text` (`<date>, <HH:MM>` или `<date>, назначенное время`);
   - собрать `MessageData(..., subject_text, datetime_text, time=time_text)`;
   - отправлять `{{1}}=SternMeister, {{2}}=subject_text, {{3}}=datetime_text`;
   - `template_values_json = {"name","institution","date","time","subject_text","datetime_text"}`, где `"time" = time_text`.
5. Для Б5 (`berater_day_0`) без изменений.

## Явный diff для `cron.py` (`process_webhook_backfill()`, Б1)

Для webhook-backfill Б1 (`line == "berater_accepted"`) добавить ту же логику сборки composite-переменных, что и в webhook:

1. Вычислить пару `institution_text + date_for_template` через тот же winner-алгоритм DC/AA (как в `app.py` для Б1).
2. Извлечь `time_raw = extract_time_termin(...)`, затем `time_text = time_raw or "назначенное время"`.
3. Сформировать `topic`, `datetime_text`, `location_text`.
4. Создать `MessageData` с заполненными Б1-полями (`topic/datetime_text/location_text/time`) без `None`.
5. Для Б1 сохранять `template_values_json` только в keyed-dict формате:
   - `{"name","institution","date","time","topic","datetime_text","location_text"}`.
6. Для Г1 (`gosniki_consultation_done`) поведение не менять.
7. Запрещено использовать legacy list `template_values=[name]` для новых backfill-записей Б1.

## MessageData и `template_values` для retry

Расширить `MessageData` полями:

- `time: str | None`
- `topic: str | None`
- `subject_text: str | None`
- `datetime_text: str | None`
- `location_text: str | None`

Маппинг `MessageData -> {{N}}`:

- Б1 (`3b7211aa`): `{{1}}=SternMeister`, `{{2}}=topic`, `{{3}}=datetime_text`, `{{4}}=location_text`.
- Б2 (`bc64a599`): `{{1}}=name`, `{{2}}=date`, `{{3}}=time`, `{{4}}=institution`, где `time` уже fallback-applied (`HH:MM` или `"назначенное время"`).
- Б4 (`38194e93`): `{{1}}=SternMeister`, `{{2}}=subject_text`, `{{3}}=datetime_text`.

Формат `messages.template_values`:

- Б1: `{"name","institution","date","time","topic","datetime_text","location_text"}`.
- Б2: `{"name","institution","date","time"}`, где `"time"` хранит fallback-applied `time_text` (не `None`).
- Б3: `{"name","institution","weekday","date"}` (без изменений, legacy-совместимо).
- Б4: `{"name","institution","date","time","subject_text","datetime_text"}`.

Для Б4 хранение 6 ключей при 3 шаблонных переменных намеренное: extra-поля нужны для воспроизводимого retry-восстановления composite-переменных.

`cron._build_message_data()` обязан восстанавливать оба формата: legacy list и keyed dict.

## Migration / Deployment (legacy Б1 retry queue)

Проблема: до деплоя T16 в БД есть Б1-записи с legacy `template_values=[name]` под старый 1-переменный шаблон. После переключения Б1 на 4 переменные такие записи нельзя ретраить "как есть".

Обязательные шаги деплоя:

1. Pre-deploy check: посчитать retriable legacy Б1 (`line='berater_accepted'`, `template_values` в list-формате, `next_retry_at IS NOT NULL`).
2. Кодовая обратная совместимость в `cron._build_message_data()`:
   - если `line='berater_accepted'` и `template_values` legacy list (`[name]`), реконструировать недостающие поля через фиксированные значения:
   - `institution_text = "Jobcenter или Agentur für Arbeit"`;
   - `topic = "термин в Jobcenter или Agentur für Arbeit"`;
   - `datetime_text = "дату и время сообщим дополнительно"`;
   - `location_text = "в Jobcenter или Agentur für Arbeit"`;
   - `time_text = "назначенное время"` (для консистентности `MessageData.time` и БД).
3. Post-deploy safety step:
   - если остались legacy Б1, прогнать dry-run retry в тестовом окружении;
   - только после этого включать cron в проде.
4. Запрещено отправлять retry Б1 с `None` в любой из 4 template-переменных.

## Операционные примечания

- GUID `38194e93` будет использоваться и в S01 (`first/second`), и в S02 Б4: в `TEMPLATE_MAP` нужен явный комментарий, что различается line и набор переменных.
- Поле `time_termin` (`886670`) может быть рассинхронизировано с датой из `887026/887028` на стороне Kommo; это риск качества данных CRM, не транспортного слоя.
- Новый Б4 (`38194e93`) содержит quick-reply кнопки; это меняет observable поведение чата и должно быть заранее согласовано с бизнесом/операторами.

## Out of scope

- Создание/переподача новых шаблонов в Meta.
- Изменение таймингов триггеров, send-window, dedup-стратегии.

## Как протестировать

1. Запустить автотесты в Docker (`docker build`, `pytest tests/`).
2. Unit: `extract_time_termin()`:
   - валидный Unix timestamp → `HH:MM`;
   - невалидное значение → `None`.
3. Unit: institution-priority для Б1:
   - только ДЦ, только АА, оба термина (разные даты), оба термина (одинаковая дата).
4. Unit: маппинг Б1/Б2/Б4 не отдаёт пустые `templateValues`.
5. Unit (retry, Б1 keyed): keyed-dict запись Б1 корректно восстанавливается и уходит с 4 непустыми переменными.
6. Unit (retry, Б1 legacy): legacy list `[name]` для Б1 восстанавливается через fallback и не ломает отправку.
7. Unit/Integration (backfill, Б1): `process_webhook_backfill()` для `berater_accepted` создаёт/отправляет запись с keyed `template_values` и без `None` в Б1-переменных.
8. Integration: `days_until=7` создаёт `berater_day_minus_7` со статусом `sent`.
9. Manual E2E на 2 whitelist-номерах: полная серия Б1→Б5 приходит фактически в WhatsApp.

## Критерии приёмки

1. Для Б1/Б2/Б4 активные GUID в коде относятся к `UTILITY`.
2. Б3/Б5 остаются существующими `UTILITY`-шаблонами (без изменений в тексте/логике).
3. Б2 (`berater_day_minus_7`) отправляется как реальное сообщение и сохраняется в `messages`.
4. Для Б1/Б2/Б4 не отправляется ни одной пустой template-переменной.
5. Для Б1 с двумя заполненными датами (ДЦ+АА) institution выбирается детерминированно по приоритету из задачи.
6. Legacy Б1 записи в retry-очереди обрабатываются без `None` в `templateValues` и без падений.
7. Regression: retry/pending/backfill/dedup рабочие, тесты зелёные.

## Зависимости

- Требует: T12-T15 (выполнены).
- Внешняя зависимость: `3b7211aa`, `38194e93`, `bc64a599` в статусе `approved` и доступны в рабочем канале Wazzup.

## API references

- Wazzup24 API docs: https://wazzup24.com/help/api-en/sending-messages/
- Wazzup24 templates endpoint: `GET /v3/templates/whatsapp`
- Внутренний справочник: `docs/5. unsorted/wazzup24_api_reference.md`

---

## Код-ревью T16 (10.03.2026)

Ревью проведено по всем изменённым файлам: `config.py`, `kommo.py`, `app.py`, `cron.py`, `messenger/wazzup.py`, и всем тестам.

### ВЫСОКАЯ серьёзность

**H1. Дублирование бизнес-логики между `app.py` и `cron.py`**

4 функции + 3 константы скопированы 1:1 между файлами:
- `_coerce_date()` (`app.py:58` ↔ `cron.py:54`)
- `_pick_berater_accepted_institution_and_date()` (`app.py:65` ↔ `cron.py:61`)
- `_build_berater_accepted_texts()` (`app.py:85` ↔ `cron.py:80`)
- `_B1_FALLBACK_INSTITUTION`, `_TIME_FALLBACK`, `_B1_NO_DATE_DATETIME_TEXT`, `_BERLIN_TZ`

Риск: при исправлении бага в одном файле второй останется с ошибкой — классический источник регрессий. Нужно вынести в общий модуль (например `server/b1_utils.py` или `server/template_helpers.py`).

**H2. Нет unit-тестов для `extract_time_termin()`**

Задача явно требует: "Unit: `extract_time_termin()`: валидный Unix timestamp → `HH:MM`; невалидное значение → `None`". Тесты отсутствуют. В тестах функция только мокается (`return_value = "10:30"` / `None`), реальная логика не проверена. Нужны тесты:
- валидный timestamp → `"HH:MM"` (с учётом Europe/Berlin)
- невалидная строка (`"not-a-number"`) → `None`
- поле отсутствует → `None`
- `None` value → `None`

**H3. Нет unit-тестов для `_pick_berater_accepted_institution_and_date()`**

Задача явно требует: "Unit: institution-priority для Б1: только ДЦ, только АА, оба термина (разные даты), оба термина (одинаковая дата)". Тесты отсутствуют. Через webhook-тест (`test_webhook_s02.py`) покрыт только случай DC-only. Отсутствуют:
- только АА
- оба термина, DC ближе
- оба термина, AA ближе
- оба термина, одинаковая дата (→ DC приоритет)
- обе даты отсутствуют (→ fallback)

**H4. Нет теста webhook Б1 с обоими датами DC+AA**

Webhook handler для `berater_accepted` не тестируется на сценарий с двумя датами (DC и AA). Priority-алгоритм проверяется только имплицитно через DC-only тест.

### СРЕДНЯЯ серьёзность

**M1. Нет unit-тестов для `_build_berater_accepted_texts()` и `_build_berater_day_minus_1_texts()`**

Composite-логика (формирование `datetime_text`, `topic`, `location_text`, `subject_text`) не покрыта unit-тестами. Проверяется только косвенно через end-to-end assertions в webhook-тестах.

**M2. Нет unit-тестов для `_non_empty()` helper**

Функция `_non_empty()` в `config.py` — ключевой guard от пустых template-переменных. Не протестирована ни на `None`, ни на `""`, ни на `"  "` (whitespace).

**M3. Обработка `time_raw` неконсистентна между путями**

- `app.py:310-313` (webhook): `isinstance` check + `.strip() or None`
- `cron.py:648-652` (temporal): `isinstance` check + `.strip() or None`
- `cron.py:411-413` (backfill): `isinstance` check, но **без `.strip()`**

Не баг: `_build_berater_accepted_texts()` внутри сама делает strip. Но неконсистентность в calling code — code smell.

**M4. Нет теста webhook Б1 без дат (полный fallback)**

Нет теста, проверяющего что при `date_dc=None, date_aa=None` webhook Б1 корректно формирует `datetime_text = "дату и время сообщим дополнительно"` и `institution = "Jobcenter или Agentur für Arbeit"`.

**M5. CHANGELOG не обновлён для T16**

Git показывает `M CHANGELOG.md`, но в файле нет записи о T16. Нужно добавить при акцепте.

**M6. Backfill-тест для `berater_accepted` не покрывает DC/AA priority path**

В `test_integration_webhook_backfill.py:124-169` mock для `extract_termin_date_dc/aa` не настроен — MagicMock по умолчанию не является `date`, поэтому `_coerce_date()` возвращает `None`, и тест случайно проверяет только fallback-путь. Нет теста backfill Б1 с реальными датами DC/AA.

### НИЗКАЯ серьёзность

**L1. `weekday_name()` вызывается дважды в `berater_day_0` ветке**

`cron.py:717` и `cron.py:723` — вычисление `weekday_name(termin_date_obj)` дублируется. Minor.

**L2. `except IndexError` в `_build_message_data` семантически неочевидно**

`cron.py:137`: `sqlite3.Row` при отсутствии ключа бросает `IndexError`, не `KeyError`. Формально корректно, но читается контринтуитивно.

**L3. Комментарий про shared GUID `38194e93` в TEMPLATE_MAP краткий**

Задача требует "явный комментарий, что различается line и набор переменных". Комментарий есть (`cron.py:147-148`), но в `config.py:149` он мог бы быть подробнее.

### Итог

- **Критических багов нет** — код работает корректно, 274 теста проходят.
- **Главная проблема — H1 (дублирование)**: 4 функции copy-paste между `app.py` и `cron.py`. Это наиболее вероятный источник будущих регрессий.
- **Вторая проблема — H2/H3/H4 (тестовое покрытие)**: задача явно описывает тест-кейсы (`extract_time_termin`, institution priority), но unit-тесты для них отсутствуют. Логика тестируется только косвенно через e2e/webhook тесты.

### Сравнение с прошлыми ревью

Предыдущие ревью T16 (2 раунда) были **ревью спецификации**, не кода:
1. QA-фидбэк: добавлены fallback-значения, `extract_time_termin` спецификация, explicit diffs для `app.py`/`cron.py`.
2. Второе ревью: добавлен diff для `process_webhook_backfill`, устранено противоречие `time` vs `time_text`, legacy-реконструкция Б1, операционные примечания.

Это **первое ревью кода** T16. Качество реализации **не хуже** предыдущих задач (T12-T15 проходили по 2-3 ревью-цикла с фиксами). По сравнению с T12-T15:
- (+) Сложная бизнес-логика (institution priority, composite fields) реализована корректно
- (+) Legacy-совместимость для retry/backfill работает
- (+) `_non_empty()` в TEMPLATE_MAP — хорошая защита от пустых переменных
- (-) Дублирование функций — новая проблема, отсутствовавшая в T12-T15 (раньше Б1 был простым)
- (-) Тестовое покрытие заявленных в задаче test-cases хуже, чем в T12-T13 (где все кейсы были покрыты)

---

## Результат фиксов по код-ревью (10.03.2026)

### Закрытые пункты

- **H1 (дублирование app/cron) — закрыт.**
  - Общие функции и константы вынесены в `server/template_helpers.py`.
  - `server/app.py` и `server/cron.py` используют единый набор helper-ов для Б1/Б4.
- **H2 (`extract_time_termin` без unit-тестов) — закрыт.**
  - Добавлен `tests/test_kommo.py` с кейсами: валидный timestamp, невалидная строка, отсутствующее поле, `None`.
- **H3 (нет unit-тестов приоритета institution/date) — закрыт.**
  - Добавлен `tests/test_template_helpers.py` с кейсами DC-only, AA-only, DC ближе, AA ближе, равные дистанции (DC wins), fallback.
- **H4 (нет webhook-теста с DC+AA) — закрыт.**
  - Расширен `tests/test_webhook_s02.py`: сценарий с двумя датами и проверкой выбора ближайшей.

### Дополнительно закрыто из Medium/Low

- **M1**: добавлены unit-тесты для `build_berater_accepted_texts()` и `build_berater_day_minus_1_texts()`.
- **M2**: добавлены unit-тесты для `_non_empty()` в `tests/test_config_s02.py`.
- **M3**: нормализация `time_raw` унифицирована через `normalize_time_raw()` во всех путях (webhook, temporal, backfill).
- **M4**: добавлен webhook-тест Б1 без дат (полный fallback).
- **M6**: расширен `tests/test_integration_webhook_backfill.py` на DC/AA priority-path (а не только fallback).
- **L1/L2/L3**: убрано двойное вычисление `weekday_name`, добавлен комментарий к `IndexError` для `sqlite3.Row`, расширен комментарий о shared GUID `38194e93` в `TEMPLATE_MAP`.

### Валидация

- Таргетный прогон (новые/изменённые тесты): **60 passed**.
- Полный прогон: `pytest tests` в Docker — **298 passed**, **0 failed**.

### Итог

- По T16 после фиксов: **High = 0** (H1-H4 закрыты).
- Реализация и тестовое покрытие приведены в состояние `ready_for_accept`.

---

## Код-ревью T16 — раунд 2 (10.03.2026)

Полный ревью всех изменённых файлов после фиксов первого раунда: `template_helpers.py`, `config.py`, `kommo.py`, `app.py`, `cron.py`, `messenger/wazzup.py` и все тесты.

### ВЫСОКАЯ серьёзность

**H1. Отсутствует тест retry Б1 с keyed-dict `template_values`**

Задача явно требует (тест-кейс 5): _"Unit (retry, Б1 keyed): keyed-dict запись Б1 корректно восстанавливается и уходит с 4 непустыми переменными."_ Этот тест не реализован.

В `test_cron_retry_template_values.py:98-124` протестирован только legacy list `[name]`. После T16 все новые Б1-записи (webhook и backfill) сохраняются в keyed-dict формате:
```json
{"name":"Мария","institution":"Jobcenter","date":"01.04.2026","time":"10:30","topic":"термин в Jobcenter","datetime_text":"01.04.2026 в 10:30","location_text":"в Jobcenter"}
```
Retry-путь для этого формата не покрыт. Код корректен (`_build_message_data` обрабатывает dict через `extra = loaded`), но без теста regression возможна при рефакторинге.

### СРЕДНЯЯ серьёзность

**M1. Двойная нормализация `time_raw` в `app.py` и `cron.py` backfill**

В трёх местах `normalize_time_raw()` вызывается дважды:
- `app.py:261` → `normalize_time_raw(extract_time_termin(...))`, затем `build_berater_accepted_texts(time_raw=...)` внутри вызывает `normalize_time_raw(time_raw)` повторно (`template_helpers.py:57`).
- `cron.py:353` (backfill) → аналогично.
- `cron.py:627` (temporal Б4) → `normalize_time_raw(...)`, затем `build_berater_day_minus_1_texts(time_raw=...)` внутри вызывает повторно (`template_helpers.py:81`).

Не баг (идемпотентно), но нарушает контракт: параметр `time_raw: object` в `build_*` подразумевает сырое значение, а получает уже нормализованное. Решение: либо убрать внешнюю нормализацию, либо переименовать параметр.

**M2. Нет теста для Б4 temporal composite texts (integration)**

`test_days_1_sends_berater_day_minus_1` (`test_temporal_triggers.py:258`) проверяет только `md.line == "berater_day_minus_1"`. Не проверяется, что `subject_text`, `datetime_text` и `time` корректно собраны через `build_berater_day_minus_1_texts()`. Unit-тесты хелперов в `test_template_helpers.py` есть, но их интеграция с temporal-путём (включая `normalize_time_raw` → `build_berater_day_minus_1_texts` → MessageData → TEMPLATE_MAP) не покрыта end-to-end.

**M3. Нет теста для Б2 temporal `time`/`time_text` fallback**

Для `berater_day_minus_7` код делает `time_text = time_raw or TIME_FALLBACK` (`cron.py:595`). Ни один тест не проверяет:
- `extract_time_termin` возвращает валидное время → `MessageData.time` = `"HH:MM"`;
- `extract_time_termin` возвращает None → `MessageData.time` = `"назначенное время"`.

Существующие temporal-тесты не мокают `extract_time_termin` явно (используют дефолтный `MagicMock`), и ни один не assert-ит на `MessageData.time`.

**M4. `test_integration_e2e.py:TestScenario1` — implicit MagicMock behavior**

Тест `test_full_flow_berater_accepted` (`test_integration_e2e.py:141`) не мокает `extract_termin_date_dc`, `extract_termin_date_aa`, `extract_time_termin` — они остаются `MagicMock()`. Работает только потому, что `coerce_date(MagicMock())→None` и `normalize_time_raw(MagicMock())→None`. Если защитные функции изменятся, тест начнёт молча проверять другой путь. Нужно явно: `kommo.extract_termin_date_dc.return_value = None` и т.д.

**M5. CHANGELOG не обновлён для T16**

Повторно из прошлого ревью. `CHANGELOG.md` не содержит записи о T16.

### НИЗКАЯ серьёзность

**L1. PEP 8: отсутствует пустая строка перед `_add_kommo_note`**

`cron.py:56-57`: после определения `_WEBHOOK_BACKFILL_TARGETS` нет 2 пустых строк перед `def _add_kommo_note(...)`.

**L2. `extract_time_termin` вызывается для всех temporal-линий (включая Б3/Б5)**

`cron.py:590-592`: `normalize_time_raw(kommo.extract_time_termin(...))` вызывается в общем цикле до line-specific ветвления. Для `berater_day_minus_3` и `berater_day_0` результат не используется. Minor waste.

**L3. Переменная `time_raw` в `app.py:261` содержит нормализованное значение**

Имя `time_raw` подразумевает сырые данные, но `normalize_time_raw()` уже вызвана. Путаница при чтении кода. Лучше назвать `time_normalized` или убрать outer нормализацию.

### Итог

- **Критических багов нет** — код работает корректно, все runtime-пути защищены от пустых `templateValues`.
- **H1 — единственный High**: отсутствует явно требуемый тест (keyed Б1 retry). Код для этого пути корректен, но не покрыт.
- **M1-M5 — средние**: двойная нормализация (code smell), 3 пробела в тестовом покрытии temporal-путей, implicit mocking в e2e, CHANGELOG.
- **L1-L3 — стиль**: PEP 8, лишний вызов, naming.

### Сравнение с первым раундом ревью T16

Первый раунд выявил **4 High + 6 Medium + 3 Low** — все закрыты.
Второй раунд: **1 High + 5 Medium + 3 Low**.

**Стало лучше:**
- (+) H1 первого раунда (дублирование 4 функций app/cron) — полностью закрыт через `template_helpers.py`, код стал значительно чище
- (+) H2-H4 (extract_time_termin, institution priority, webhook DC+AA) — закрыты с хорошим покрытием
- (+) M1-M4 первого раунда — закрыты (unit-тесты хелперов, `_non_empty`, fallback webhook, backfill DC/AA)
- (+) Общее количество High снизилось с 4 до 1

**Не стало хуже:**
- Единственный новый H1 (keyed Б1 retry тест) — это незакрытый пункт из первого раунда ревью (тогда он был скрыт в H2-H4 scope). Новых проблем в коде не появилось.
- M1-M5 второго раунда — это либо повторы (CHANGELOG), либо более глубокий уровень анализа тестового покрытия, который не выявлялся в первом раунде.

---

## Результат FIX 016 (10.03.2026)

### Что исправлено

- **H1 закрыт:** добавлен unit-тест retry Б1 для keyed-dict `template_values` в `tests/test_cron_retry_template_values.py` (`test_restores_keyed_vars_for_berater_accepted`).
- **M1 закрыт:** убрана двойная нормализация `time_raw` в `app.py` и `cron.py` (webhook/backfill/temporal); normalizing выполняется единообразно.
- **M2 закрыт:** расширен temporal-тест для Б4 (`berater_day_minus_1`) — теперь проверяются `subject_text`, `datetime_text`, `time` и сохранённый `template_values`.
- **M3 закрыт:** добавлены проверки Б2 (`berater_day_minus_7`) на оба пути времени:
  - валидное `extract_time_termin` → `MessageData.time="HH:MM"`;
  - `None` → fallback `"назначенное время"`.
- **M4 закрыт:** в `test_integration_e2e.py::test_full_flow_berater_accepted` добавлен явный mocking `extract_termin_date_dc/aa` и `extract_time_termin` (без implicit `MagicMock`-поведения).
- **M5 закрыт:** `CHANGELOG.md` обновлён записью по T16 и отдельной строкой по FIX 016.
- **L1/L2/L3 закрыты:** добавлена пустая строка перед `_add_kommo_note`, убран лишний вызов `extract_time_termin` для Б3/Б5, убрана двусмысленность переменной `time_raw` в webhook-пути.

### Валидация

- Docker таргетный прогон: `pytest tests/test_cron_retry_template_values.py tests/test_temporal_triggers.py tests/test_integration_e2e.py -q` → **70 passed**.
- Docker полный прогон: `pytest tests -q` → **300 passed**, **0 failed**.

### Итог

- После FIX 016 по T16: **High = 0**.
- Риск появления новых high по путям Б1/Б2/Б4 снижен за счёт прямого покрытия retry/temporal/e2e-кейсов и устранения дублирующей нормализации времени.

---

## Код-ревью T16 — раунд 3 (10.03.2026)

Полный ревью всех изменённых файлов после FIX 016: `template_helpers.py`, `config.py`, `kommo.py`, `app.py`, `cron.py`, `messenger/wazzup.py` и все тесты (`test_template_helpers.py`, `test_kommo.py`, `test_config_s02.py`, `test_webhook_s02.py`, `test_webhook.py`, `test_temporal_triggers.py`, `test_cron_retry_template_values.py`, `test_integration_webhook_backfill.py`, `test_integration_e2e.py`, `test_messenger_s02.py`).

### ВЫСОКАЯ серьёзность

Нет.

### СРЕДНЯЯ серьёзность

**M1. Нет temporal-теста Б4 с `extract_time_termin=None` (fallback-путь)**

`test_days_1_sends_berater_day_minus_1` (`test_temporal_triggers.py:259`) тестирует только `extract_time_termin.return_value = " 14:45 "`. Отсутствует тест, проверяющий что при `extract_time_termin → None` для Б4 temporal-путь формирует:
- `MessageData.time = "назначенное время"`
- `datetime_text = "<date>, назначенное время"`
- `template_values["time"] = "назначенное время"`

Unit-тест `build_berater_day_minus_1_texts(time_raw=None)` есть (`test_template_helpers.py:126`), но интеграция через temporal pipeline (cron → `build_berater_day_minus_1_texts` → MessageData → DB) не покрыта.

**M2. Нет backfill-теста Б1 без дат (полный fallback-путь)**

`test_backfill_berater_accepted_sent_and_idempotent` (`test_integration_webhook_backfill.py:124`) тестирует только DC+AA priority path (AA ближе). Отсутствует тест backfill Б1 при `extract_termin_date_dc=None, extract_termin_date_aa=None`, который бы проверял:
- `institution = "Jobcenter или Agentur für Arbeit"`
- `datetime_text = "дату и время сообщим дополнительно"`
- `template_values["date"] = null`

Webhook-путь для этого сценария покрыт (`test_webhook_s02.py:278`), но backfill — нет.

**M3. Нет теста `pick_berater_accepted_institution_and_date` с одинаковыми датами DC и AA**

Задача явно описывает сценарий (п.2 приоритета): _"Если обе даты равны — приоритет ДЦ: Jobcenter."_ Тест `test_both_equal_distance_dc_wins` (`test_template_helpers.py:55`) проверяет равные *расстояния* от today (разные даты: DC = 12.03, AA = 08.03, today = 10.03), но не одинаковые *даты* (DC = AA = 15.03). Код корректен (equal distance → DC wins, а при equal dates distance тоже equal), но кейс из задачи формально не покрыт отдельным тестом.

### НИЗКАЯ серьёзность

**L1. Б5 `berater_day_0` вычисляет и хранит неиспользуемые поля**

`cron.py:649-665`: для `berater_day_0` вычисляется `weekday_name()`, и в `template_values_json` хранятся `institution/weekday/date`, хотя TEMPLATE_MAP для Б5 использует только `{{1}}=name`. Лишние вычисления и хранение. Не баг (поглощается `**_` в lambda).

**L2. Отсутствие `_non_empty()` guard для Б3/Б5 в TEMPLATE_MAP**

`berater_day_minus_3` и `berater_day_0` в `config.py:144,159` передают значения в `templateValues` без `_non_empty()` guard — в отличие от Б1/Б2/Б4, где все переменные защищены. Обосновано: temporal pipeline гарантирует non-None (проверка name, institution из константы, weekday/date из termin_date_obj). Но стилистически неконсистентно.

### Итог

- **Критических багов нет.** Код работает корректно, все runtime-пути защищены от пустых `templateValues`.
- **High = 0.** Все H-пункты из раундов 1 и 2 остаются закрытыми.
- **M1-M3 — средние:** 3 пробела в тестовом покрытии (Б4 temporal fallback, backfill Б1 fallback, equal-date unit-тест). Все 3 пути корректны в коде, покрыты unit-тестами хелперов, но не покрыты integration-тестами.
- **L1-L2 — стиль:** лишние вычисления для Б5, неконсистентность `_non_empty` guard.

### Сравнение с раундом 2

Раунд 2 выявил **1 High + 5 Medium + 3 Low** — все закрыты в FIX 016.
Раунд 3: **0 High + 3 Medium + 2 Low**.

**Стало лучше:**
- (+) H1 раунда 2 (keyed Б1 retry тест) — закрыт, тест работает
- (+) M1 раунда 2 (двойная нормализация) — полностью устранена, `build_*` функции нормализуют внутри себя
- (+) M2-M4 раунда 2 (Б4 temporal, Б2 time fallback, implicit mocking) — закрыты с хорошими assertions
- (+) Все L-пункты раунда 2 закрыты
- (+) CHANGELOG обновлён
- (+) Общее количество issues снизилось: 9 → 5, severity сместилась вниз

**Не стало хуже:**
- M1-M3 раунда 3 — это более глубокий уровень анализа (integration-покрытие для путей, уже покрытых unit-тестами). Новых проблем в коде не появилось.
- Ни один из M-пунктов не является реальным runtime-риском: все три пути корректны и защищены `_non_empty()` guards / upstream checks.

---

## Прод-демо для заказчика (закрытие исходного Word-ТЗ)

**Дата прогона:** 10.03.2026  
**Контур:** production (`vpn-primary`, `/app/whatsapp`)  
**Тестовый номер:** `+996501354144`  
**Артефакты прогона:** `/app/whatsapp/backups/full-series-correct-20260310T164558Z/result.json`  
**Лиды:** `berater=18368164`, `gosniki=18368202`

### Фактический результат прогона

- Полная серия S02 доставлена: `missing_lines = []`.
- Все сообщения в run-window ушли только на `+996501354144`:
  - `berater_accepted` (`id=34`, `2026-03-10T16:46:47Z`)
  - `gosniki_consultation_done` (`id=35`, `2026-03-10T16:48:02Z`)
  - `berater_day_minus_7` (`id=36`, `2026-03-10T16:49:33Z`)
  - `berater_day_minus_3` (`id=37`, `2026-03-10T16:50:44Z`)
  - `berater_day_minus_1` (`id=38`, `2026-03-10T16:51:55Z`)
  - `berater_day_0` (`id=39`, `2026-03-10T16:52:59Z`)
- Счётчики ошибок прогона: `runtime=0`, `console=0`, `request=0`, `server=0`.

### Матрица соответствия Word-ТЗ -> подтверждение

| Требование Word | Линия в системе | Подтверждение в проде |
|---|---|---|
| Госники: после консультации 1-й линии | `gosniki_consultation_done` | `id=35`, есть в `result.json` и в WhatsApp-диалоге |
| Бератер: сразу после назначения термина | `berater_accepted` | `id=34`, дата/время подставлены (`17.03.2026 в 10:30`) |
| Бератер: за 7 дней | `berater_day_minus_7` | `id=36`, получено сообщение с чек-листом |
| Бератер: за 3 дня | `berater_day_minus_3` | `id=37`, получено сообщение + quick reply |
| Бератер: за 1 день | `berater_day_minus_1` | `id=38`, получено сообщение + quick reply |
| Бератер: в день термина | `berater_day_0` | `id=39`, получено утреннее мотивационное сообщение |

### Скриншоты для показа заказчику (чеклист)

Нужно приложить 6 экранов WhatsApp (можно несколькими длинными скринами, если все блоки читаются):

1. `G1` — после консультации (`gosniki_consultation_done`, id=35)
2. `B1` — подтверждение записи (`berater_accepted`, id=34)
3. `B2` — за 7 дней (`berater_day_minus_7`, id=36)
4. `B3` — за 3 дня (`berater_day_minus_3`, id=37)
5. `B4` — за 1 день (`berater_day_minus_1`, id=38)
6. `B5` — в день термина (`berater_day_0`, id=39)

Рекомендация для демонстрации: на каждом скрине оставить видимым имя чата (`SternMeister`) и timestamp сообщения.

Отдельный чеклист файлов: `docs/5. unsorted/s02_customer_demo_screenshots_2026-03-10.md`.

### Что из Word-документа не входит в текущую автосерию S02

- Блок "`После термина`" (пункт 6 в Word) — в S02 out of scope, автоотправки нет.
- Дополнительные шаблоны из Word:
  - `Просьба о переносе термина` (Email-only)
  - `Предупреждение о санкциях` (отдельный шаблон, не часть S02-автоматизации)
- Email-канал в S02 отключён по scope, показывается только WABA-цепочка.
