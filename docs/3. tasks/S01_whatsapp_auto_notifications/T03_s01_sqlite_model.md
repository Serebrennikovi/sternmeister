**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T03 — SQLite модель и логирование

---

## Customer-facing инкремент

Система может сохранять и читать логи отправленных WhatsApp-сообщений. Это позволяет отслеживать историю отправок, повторы и статусы доставки.

---

## Scope

### Делаем:
- Создание SQLite базы данных и таблицы `messages` (согласно S01)
- Реализация CRUD операций: создание, чтение, обновление записей
- Функции для cron-задачи: `get_messages_for_retry()`, `get_pending_messages()`
- Индексы для оптимизации запросов повторов
- Инициализация БД при первом запуске (автоматическая миграция)

### НЕ делаем:
- Интеграцию с мессенджером Wazzup24 (будет в T05)
- Бизнес-логику отправки сообщений (будет в T06)
- Веб-интерфейс для просмотра логов

---

## Схема БД (SQLite)

```sql
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kommo_lead_id INTEGER NOT NULL,
    kommo_contact_id INTEGER NOT NULL,
    phone TEXT NOT NULL,
    line TEXT NOT NULL CHECK(line IN ('first', 'second')),
    message_text TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status IN ('pending', 'sent', 'delivered', 'failed')),
    attempts INTEGER DEFAULT 1,
    created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at DATETIME,
    next_retry_at DATETIME,
    messenger_id TEXT,
    messenger_backend TEXT NOT NULL DEFAULT 'wazzup'
);

-- Индексы для производительности
CREATE INDEX IF NOT EXISTS idx_status_next_retry
    ON messages(status, next_retry_at);

CREATE INDEX IF NOT EXISTS idx_kommo_contact
    ON messages(kommo_contact_id);
```

---

## db.py (актуальная реализация)

Реализация использует модульные функции (не класс), column whitelist для защиты от SQL injection, WAL mode, и все timestamps хранятся в UTC.

Актуальный код: [`server/db.py`](../../../server/db.py)

**Ключевые функции:**
- `now_iso()` — текущее UTC время в ISO 8601 (публичная, для использования в других модулях)
- `init_db()` — создание таблицы и индексов
- `create_message(*, ...)` — keyword-only аргументы, возвращает row id
- `update_message(message_id, **fields)` — обновление произвольных полей с whitelist-валидацией
- `get_messages(**filters)` — фильтрация по полям
- `get_message_by_id(message_id)` — получение одной записи
- `get_messages_for_retry(at=None)` — сообщения для повтора (status=sent, attempts < max)
- `get_pending_messages(at=None)` — отложенные сообщения (status=pending)

---

## Как протестировать

1. **Создать тестовый скрипт `test_db.py`:**

```python
from server.db import init_db, create_message, update_message, get_message_by_id
from server.db import get_messages_for_retry, get_pending_messages, now_iso

# 0. Инициализация
init_db()

# 1. Создать тестовое сообщение
ts = now_iso()
message_id = create_message(
    kommo_lead_id=99999,
    kommo_contact_id=12345,
    phone="+996501354144",
    line="first",
    message_text="Тестовое сообщение",
    status="sent",
    sent_at=ts,
    next_retry_at="2020-01-01T00:00:00+00:00",  # в прошлом для теста
)
print(f"Создано сообщение ID: {message_id}")

# 2. Прочитать сообщение
msg = get_message_by_id(message_id)
print(f"Прочитано: {msg['phone']}, статус: {msg['status']}")

# 3. Обновить статус
update_message(message_id, status="delivered", attempts=2)
print(f"Обновлено: статус -> delivered, attempts -> 2")

# 4. Проверить получение сообщений для повтора
update_message(message_id, status="sent")
retry_messages = get_messages_for_retry()
print(f"Найдено сообщений для повтора: {len(retry_messages)}")

# 5. Проверить pending сообщения
create_message(
    kommo_lead_id=88888,
    kommo_contact_id=67890,
    phone="+79167310500",
    line="second",
    message_text="Отложенное сообщение",
    status="pending",
    next_retry_at="2020-01-01T00:00:00+00:00",
)
pending = get_pending_messages()
print(f"Найдено отложенных сообщений: {len(pending)}")

print("\n✅ Все тесты пройдены!")
```

2. **Запустить тест:**
   ```bash
   python test_db.py
   ```

3. **Проверить БД вручную:**
   ```bash
   sqlite3 data/messages.db
   ```
   ```sql
   SELECT * FROM messages;
   .schema messages
   ```

---

## Критерии приёмки

- [ ] Таблица `messages` создаётся автоматически при первом запуске
- [ ] Индексы `idx_status_next_retry` и `idx_kommo_contact` существуют
- [ ] `create_message()` создаёт запись и возвращает `message_id`
- [ ] `update_message()` обновляет поля корректно
- [ ] `get_messages_for_retry()` возвращает только сообщения с `attempts < 3` и `next_retry_at <= now`
- [ ] `get_pending_messages()` возвращает только `status=pending` с `next_retry_at <= now`
- [ ] `get_message_by_id()` возвращает запись по ID
- [ ] Тестовый скрипт `test_db.py` выполняется без ошибок
- [ ] БД создаётся в папке `data/messages.db` (путь из .env)

---

## Зависимости

**Требует:** T02 (scaffold)
**Блокирует:** T06 (webhook handler), T08 (cron для повторов)
