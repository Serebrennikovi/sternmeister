**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T08 — Cron-задача для повторов

---

## Customer-facing инкремент

Система автоматически повторяет отправку сообщений через 24 часа, если клиент не ответил. Максимум 2 повтора (итого 3 попытки). Также обрабатываются отложенные сообщения (pending), которые были созданы вне окна времени.

---

## Scope

### Делаем:
- Скрипт `cron.py` для обработки повторов и pending сообщений
- Логика повтора: `attempts < 3`, `next_retry_at <= now`, `status=sent`
- Логика pending: `status=pending`, `next_retry_at <= now`
- Обновление `attempts`, `sent_at`, `next_retry_at` после отправки
- Обработка ошибок: если отправка не удалась → `status=failed`
- Настройка cron (systemd timer или crontab) для запуска каждый час

### НЕ делаем:
- Проверку "есть ли ответ от клиента" (упрощение: повтор всегда)
- Telegram алерты при ошибках (будет в T09)

---

## cron.py (реализация)

```python
#!/usr/bin/env python3
"""
Cron-задача для обработки повторов и отложенных сообщений

Запуск: python server/cron.py
Рекомендуемая частота: каждый час
"""

from datetime import datetime, timedelta
import config
from server.db import db
from server.messenger import messenger, MessengerError, MessageData
from server.utils import is_in_send_window

def process_retries():
    """
    Обработать сообщения для повторной отправки

    Критерии:
    - status = "sent"
    - attempts < MAX_RETRY_ATTEMPTS + 1 (первая + повторы)
    - next_retry_at <= now
    """
    print(f"[{datetime.now()}] Checking for retries...")

    max_attempts = config.MAX_RETRY_ATTEMPTS + 1  # 2 повтора + первая = 3
    messages = db.get_messages_for_retry(max_attempts=max_attempts)

    print(f"Found {len(messages)} messages for retry")

    if not is_in_send_window():
        print("Outside send window, skipping retries")
        return

    # messenger уже импортирован
    success_count = 0
    failed_count = 0

    for msg in messages:
        print(f"  Retrying message {msg['id']} (attempt {msg['attempts'] + 1}/{max_attempts})")

        try:
            result = messenger.send_message(msg['phone'], msg['message_text'])

            # Успешная отправка
            db.update_message(
                msg['id'],
                status="sent",
                attempts=msg['attempts'] + 1,
                sent_at=datetime.now(),
                next_retry_at=datetime.now() + timedelta(hours=config.RETRY_INTERVAL_HOURS),
                messenger_id=result['message_id']
            )

            success_count += 1
            print(f"    ✅ Retry successful: {result['message_id']}")

        except MessengerError as e:
            # Ошибка отправки
            db.update_message(msg['id'], status="failed")
            failed_count += 1
            print(f"    ❌ Retry failed: {e}")

    print(f"Retries: {success_count} success, {failed_count} failed\n")

def process_pending():
    """
    Обработать отложенные сообщения (pending)

    Критерии:
    - status = "pending"
    - next_retry_at <= now
    """
    print(f"[{datetime.now()}] Checking for pending messages...")

    messages = db.get_pending_messages()
    print(f"Found {len(messages)} pending messages")

    if not is_in_send_window():
        print("Outside send window, skipping pending")
        return

    # messenger уже импортирован
    success_count = 0
    failed_count = 0

    for msg in messages:
        print(f"  Sending pending message {msg['id']}")

        try:
            result = messenger.send_message(msg['phone'], msg['message_text'])

            # Успешная отправка
            db.update_message(
                msg['id'],
                status="sent",
                sent_at=datetime.now(),
                next_retry_at=datetime.now() + timedelta(hours=config.RETRY_INTERVAL_HOURS),
                messenger_id=result['message_id']
            )

            success_count += 1
            print(f"    ✅ Sent: {result['message_id']}")

        except MessengerError as e:
            # Ошибка отправки → оставить pending, попробуем в следующий раз
            failed_count += 1
            print(f"    ❌ Failed: {e}")

    print(f"Pending: {success_count} success, {failed_count} failed\n")

def main():
    """Основная функция cron-задачи"""
    print("=" * 60)
    print("WhatsApp Auto-notifications Cron")
    print("=" * 60)

    try:
        process_retries()
        process_pending()
    except Exception as e:
        print(f"❌ Cron error: {e}")
        # TODO: отправить Telegram alert (T09)

    print("=" * 60)
    print("Cron finished\n")

if __name__ == "__main__":
    main()
```

---

## Настройка cron (systemd timer)

### 1. Создать systemd service

`/etc/systemd/system/whatsapp-cron.service`:

```ini
[Unit]
Description=WhatsApp Auto-notifications Cron

[Service]
Type=oneshot
User=root
WorkingDirectory=/app
Environment="PATH=/usr/local/bin:/usr/bin:/bin"
ExecStart=/usr/bin/python3 /app/server/cron.py

[Install]
WantedBy=multi-user.target
```

### 2. Создать systemd timer

`/etc/systemd/system/whatsapp-cron.timer`:

```ini
[Unit]
Description=Run WhatsApp Cron every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Активировать

```bash
sudo systemctl daemon-reload
sudo systemctl enable whatsapp-cron.timer
sudo systemctl start whatsapp-cron.timer
sudo systemctl status whatsapp-cron.timer
```

---

## Альтернатива: crontab

Если не используется systemd, можно настроить через crontab:

```bash
crontab -e
```

Добавить:

```
0 * * * * cd /app && /usr/bin/python3 server/cron.py >> /var/log/whatsapp-cron.log 2>&1
```

---

## Как протестировать

### Подготовка

1. Создать тестовые записи в БД с разными сценариями:

```python
from server.db import db
from datetime import datetime, timedelta

# Сценарий 1: Сообщение для повтора (sent, attempts=1, next_retry_at в прошлом)
db.create_message(
    kommo_contact_id=12345,
    phone="+996501354144",
    line="first",
    message_text="Тест: повтор через 24ч",
    status="sent",
    messenger_backend="green_api",
    sent_at=datetime.now() - timedelta(hours=25),
    next_retry_at=datetime.now() - timedelta(hours=1)
)

# Сценарий 2: Pending сообщение (создано ночью, now=9:00)
db.create_message(
    kommo_contact_id=67890,
    phone="+996501354144",
    line="second",
    message_text="Тест: отложенное сообщение",
    status="pending",
    messenger_backend="green_api",
    next_retry_at=datetime.now() - timedelta(minutes=10)
)

# Сценарий 3: Сообщение с 2 попытками (не должно повторяться)
msg_id = db.create_message(
    kommo_contact_id=11111,
    phone="+996501354144",
    line="first",
    message_text="Тест: максимум попыток",
    status="sent",
    messenger_backend="green_api",
    attempts=3,
    sent_at=datetime.now() - timedelta(hours=25),
    next_retry_at=datetime.now() - timedelta(hours=1)
)
```

### Тест 1: Запуск cron вручную

```bash
python server/cron.py
```

Ожидаемый вывод:

```
============================================================
WhatsApp Auto-notifications Cron
============================================================
[2026-02-23 15:30:00] Checking for retries...
Found 1 messages for retry
  Retrying message 1 (attempt 2/3)
    ✅ Retry successful: 3EB0XXXX...
Retries: 1 success, 0 failed

[2026-02-23 15:30:00] Checking for pending messages...
Found 1 pending messages
  Sending pending message 2
    ✅ Sent: 3EB0YYYY...
Pending: 1 success, 0 failed

============================================================
Cron finished
```

### Тест 2: Проверка в WhatsApp

1. Проверить получение сообщений на +996501354144
2. Убедиться, что тексты совпадают

### Тест 3: Проверка БД

```bash
sqlite3 data/messages.db
```

```sql
-- Проверить обновление attempts
SELECT id, attempts, status, sent_at, next_retry_at FROM messages;

-- Сообщение 1: attempts=2, sent_at обновлён, next_retry_at = сейчас + 24ч
-- Сообщение 2: status="sent", sent_at установлен
-- Сообщение 3: не изменилось (attempts=3, макс достигнут)
```

### Тест 4: Проверка окна времени

1. Установить `SEND_WINDOW_END=14` (вне окна после 14:00)
2. Запустить cron в 15:00
3. Проверить вывод: "Outside send window, skipping retries"
4. Убедиться, что сообщения не отправлены

---

## Критерии приёмки

- [ ] `cron.py` запускается без ошибок: `python server/cron.py`
- [ ] `process_retries()` обрабатывает сообщения с `status=sent`, `attempts < 3`, `next_retry_at <= now`
- [ ] `process_pending()` обрабатывает сообщения с `status=pending`, `next_retry_at <= now`
- [ ] После успешной отправки: `attempts` увеличивается, `sent_at` обновляется, `next_retry_at` = now + 24ч
- [ ] При ошибке отправки: `status=failed`
- [ ] Сообщения с `attempts >= 3` не обрабатываются
- [ ] Проверка окна времени: вне 9-21 → "skipping retries/pending"
- [ ] Systemd timer настроен и запускается каждый час
- [ ] Тесты с реальными сообщениями проходят: повтор и pending отправляются

---

## Зависимости

**Требует:** T02 (scaffold), T03 (db), T05 (messenger), T06 (webhook), T07 (send window)
**Блокирует:** T10 (деплой — нужно настроить cron на сервере)
