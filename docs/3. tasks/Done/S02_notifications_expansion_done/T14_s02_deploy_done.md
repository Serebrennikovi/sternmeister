**Дата:** 2026-03-04
**Статус:** done
**Спецификация:** docs/2. specifications/S02_notifications_expansion.md

# T14 — Деплой S02 на Hetzner

## Customer-facing инкремент

Новая цепочка уведомлений S02 работает в продакшне: реальные клиенты получают Г1, Б1 при смене этапа, а клиенты Бератера — автоматические напоминания за 3/1 день и в день термина.

## Scope

1. **Миграция БД на сервере**: запустить `migrate_db()` на живой базе (SQLite на Hetzner)
   - Сделать backup перед миграцией:
     ```bash
     cp /app/whatsapp/data/messages.db /app/whatsapp/data/messages.db.bak.$(date +%Y%m%d%H%M)
     ```
   - `migrate_db()` идемпотентна — безопасно вызывать при старте контейнера
   - Проверить после: новые line-значения проходят CHECK; индекс idx_dedup_temporal существует
2. **Docker rebuild + redeploy**: аналогично T10 (S01 деплой)
   - `docker build`, `docker stop`, `docker run`
   - Проверить `/health` после старта
3. **Smoke-тест в продакшне**:
   - Перевести тестовый лид в «Консультация проведена» (Бух Гос) → проверить WABA
   - Перевести тестовый лид в «Принято от первой линии» (Бух Бератер) → проверить WABA
   - Создать лид с датой ДЦ = завтра → дождаться cron → проверить WABA

## Rollback-процедура

Если деплой завершился с ошибкой после `migrate_db()`:

```bash
# 1. Остановить новый контейнер
docker stop whatsapp-notifications

# 2. Восстановить базу из backup
cp /app/whatsapp/data/messages.db.bak.<timestamp> /app/whatsapp/data/messages.db

# 3. Запустить старый образ (предыдущий тег)
docker run -d --name whatsapp-notifications \
  --env-file /app/whatsapp/.env \
  -v /app/whatsapp/data:/app/data \
  -p 8000:8000 \
  whatsapp-notifications:<previous_tag>

# 4. Проверить /health
curl https://shternmeister.ngrok.pro/health
```

> **Важно:** всегда тегировать образ перед деплоем (`docker build -t whatsapp-notifications:s02 .`), чтобы старый образ был доступен для отката. Предыдущий тег — `whatsapp-notifications:s01` (или проверить `docker images`).

---

## Out of scope

- Настройка нового webhook URL (URL не меняется с S01)
- WABA-шаблон Б2 (за 7 дней) — деплоится с заглушкой, GUID добавим отдельно

## Как протестировать

1. `curl https://shternmeister.ngrok.pro/health` → `status: ok`, `failed_temporal` присутствует
2. Тестовый webhook-вызов → WABA-сообщение в тестовый номер
3. Проверить логи: `docker logs <container>` — нет FATAL/CRITICAL ошибок
4. Проверить БД: `sqlite3 data/messages.db "SELECT line, COUNT(*) FROM messages GROUP BY line"` → новые типы присутствуют

## Критерии приёмки

1. Сервис стартует без ошибок (`/health` → 200 OK)
2. `/health` содержит поле `failed_temporal`
3. Backup БД сделан до миграции
4. Миграция БД применена: CHECK constraint и idx_dedup_temporal проверены
5. Webhook Г1 и Б1 работают в продакшне (тест на тестовом лиде)
6. Cron process_temporal_triggers() запускается (виден в логах)
7. Telegram-алерт при старте не сигнализирует об ошибках

## Зависимости

- **Требует:** T12, T13 (всё реализовано и тесты зелёные)

---

## Код-ревью T14 (2026-03-04) — финальное ревью перед деплоем

Ревью кода, задеплоенного в рамках S02 (T12 + T13). Пять предыдущих ревью-циклов (T12: 2, T13: 3) закрыли все HIGH-баги. Ниже — полная приоритизированная картина текущего состояния кода.

---

### 🔴 HIGH — нет

---

### 🟡 MEDIUM — 2 замечания

#### M1. M1-фикс T13 — мёртвый код: `is_main` отсутствует в bulk-ответе Kommo API

**Где:** `server/cron.py:329` — `process_temporal_triggers()`

```python
main_contact = next((c for c in contacts if c.get("is_main")), contacts[0])
```

**Проблема.** Фикс M1 первого ревью T13 исправил `contacts[0]["id"]` → `next(is_main, contacts[0])` — аналогично `get_lead_contact()`. Однако `kommo_api_reference.md` (создан в T13) документирует реальный ответ API:

> `_embedded.contacts` в ответе `GET /leads` содержит **только `id` и `_links`** — поле `is_main` отсутствует.

Следовательно, `c.get("is_main")` всегда `None` → `next()` всегда возвращает `contacts[0]`. Фикс M1 — no-op. Поведение идентично исходному коду до фикса. Для лида с несколькими контактами, где главный — не первый в списке, будет взят неверный контакт (имя + телефон).

На практике: Kommo обычно возвращает главный контакт первым. Но гарантии нет.

**Влияние:** клиент может получить сообщение на телефон/имя не главного контакта. Редко, но customer-facing.

---

#### M2 (carry-over M1-3RD). Dead code `skipped`-ветки создают latent infinite-retry

**Где:** `server/cron.py:119-121` (`process_retries`), `cron.py:211-213` (`process_pending`)

`continue` без обновления DB при `status="skipped"`. Если запись с `berater_day_minus_7` окажется в БД (например, кто-то переместит placeholder-check после `create_message` при рефакторинге) — cron будет подбирать её каждый час бесконечно. `attempts` не инкрементируется, `next_retry_at` не сдвигается.

Сейчас недостижимо: placeholder-check в `process_temporal_triggers` гарантированно происходит до `get_temporal_dedup()` и до `send_message()`, поэтому записи для `berater_day_minus_7` никогда не создаются.

---

### 🟢 LOW — 7 замечаний (приоритет убывает)

#### L1 (carry-over L3-3RD). `process_pending()` не проверяет `_TEMPORAL_LINES`

**Где:** `server/cron.py:217-219`

```python
next_retry_at = (now + timedelta(hours=RETRY_INTERVAL_HOURS)).isoformat(...)
```

В `process_retries()` на этом месте стоит `None if msg["line"] in _TEMPORAL_LINES else +24h`. В `process_pending()` аналогичной проверки нет. Если temporal-сообщение окажется в `pending` — повторная отправка через 24ч.

Сейчас недостижимо: temporal-сообщения создаются только со статусом `sent` или `failed`.

---

#### L2 (carry-over L2-3RD). `get_contact()` вызывается дважды для одного лида при активных ДЦ и АА

**Где:** `server/cron.py:331` — внутри `for extract_date, institution in termin_fields`

При N лидах с обеими датами нагрузка на Kommo API удваивается. Риск 429 rate limit при большой базе. Решение: кешировать `{contact_id: contact_data}` до цикла `for termin_fields`.

---

#### L3 (carry-over L-NEW-1 из T12). `app.py` не обрабатывает `{"status": "skipped"}` от `send_message`

**Где:** `server/app.py:324`

```python
messenger_id=result["message_id"],  # KeyError если result = {"status": "skipped"}
```

Если temporal-line когда-либо попадёт в `PIPELINE_CONFIG`, webhook-handler упадёт с KeyError (поймается outer-except, залогируется как unexpected error, в БД запись не создастся). Сейчас недостижимо: ни один temporal-line не в `PIPELINE_CONFIG`.

---

#### L4 (carry-over L-NEW-2 из T12). Двойное вычисление `vars_fn` в webhook-пути

**Где:** `server/app.py:256` + `server/messenger/wazzup.py:128`

`build_message_text(message_data)` вычисляет `vars_fn`. Затем `send_message()` снова вызывает `vars_fn`. Тривиальные лямбды, производительность не критична. Аналогичная ситуация есть и в cron-пути (но там это задокументировано в комментарии).

---

#### L5 (carry-over L1-3RD). Двойной вызов `weekday_name()` в `process_temporal_triggers()`

**Где:** `server/cron.py:358` и `cron.py:371`

```python
message_data = MessageData(weekday=weekday_name(termin_date_obj), ...)
template_values_json = json.dumps({"weekday": weekday_name(termin_date_obj), ...})
```

Второй вызов можно заменить на `message_data.weekday`.

---

#### L6 (carry-over L-NEW-6 из T12). Idempotency-check `migrate_db()` вне транзакции

**Где:** `server/db.py:91-94`

`PRAGMA table_info` выполняется до `BEGIN IMMEDIATE`. Теоретическая гонка при одновременном старте двух процессов. На практике невозможно: один Docker-контейнер, одновременный старт исключён.

---

#### L7 (carry-over L4-3RD). Тест мокирует `""` вместо `None` для отсутствующего телефона

**Где:** `tests/test_temporal_triggers.py:434`

```python
mock_cron_deps["kommo"].extract_phone.return_value = ""  # phone not found
```

Реальный `extract_phone()` возвращает `None`, не `""`. Оба значения falsy → поведение одинаковое. Семантически некорректно.

---

### Деплой-специфичные замечания

1. **PHONE_WHITELIST**: перед деплоем убедиться, что переменная не установлена или пуста в `.env` на сервере — иначе реальные клиенты не получат сообщения.
2. **Smoke test temporal**: для теста «дата ДЦ = завтра → дождаться cron» нужно до 1 часа ожидания. Запустить cron вручную через `docker exec` для ускорения проверки.
3. **Telegram-алерты**: в HANDOFF отмечено `[ ] Bot token` и `[ ] Chat ID` — если не настроены, все алерты логируются, не отправляются. Не блокирует деплой.

---

### Сравнение с предыдущими ревью

| Ревью | HIGH | MEDIUM | LOW |
|-------|------|--------|-----|
| T12 первое | 0 | 2 | 8 |
| T12 второе | 0 | 0 | 6 |
| T13 первое | 2 | 3 | 2 |
| T13 второе | 1 | 2 | 1 |
| T13 третье | 0 | 1 | 4 |
| **T14 (текущее)** | **0** | **2** | **7** |

**Вывод: не ухудшилось по HIGH (0).** Добавлен один новый MEDIUM (M1: is_main no-op) — это регрессия по сравнению с T13 третьим ревью (был 0 HIGH, 1 MEDIUM). Вновь найденная проблема с `is_main` была упущена во всех трёх ревью T13, несмотря на то что `kommo_api_reference.md` со свидетельством об отсутствии поля был создан в той же задаче. LOW-замечания в основном carry-over; новых LOW нет. Код находится в приемлемом состоянии для деплоя — ни один баг не является customer-facing при нормальных условиях.
