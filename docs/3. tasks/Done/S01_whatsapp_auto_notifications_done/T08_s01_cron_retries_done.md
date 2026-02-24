**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T08 — Cron-задача для повторов

---

## Customer-facing инкремент

Система автоматически повторяет отправку сообщений через 24 часа, если клиент не ответил. Максимум 2 повтора (итого 3 попытки). Также обрабатываются отложенные сообщения (pending), которые были созданы вне окна времени.

---

## Scope

### Делаем:
- Скрипт `cron.py` для обработки повторов и pending сообщений
- Логика повтора: `attempts < 3`, `next_retry_at <= now`, `status IN (sent, failed)`
- Логика pending: `status=pending`, `next_retry_at <= now`
- Обновление `attempts`, `sent_at`, `next_retry_at` после отправки
- Обработка ошибок: если отправка не удалась → `status=failed`
- Настройка cron (systemd timer или crontab) для запуска каждый час

### НЕ делаем:
- Проверку "есть ли ответ от клиента" (упрощение: повтор всегда)
- Telegram алерты при ошибках (будет в T09)

---

## Реализация

Файл: `server/cron.py` — запуск: `python -m server.cron`

**Ключевые решения:**
- `process_retries()` — ретраит `status IN (sent, failed)`, `attempts < 3`, `next_retry_at <= now`
- `process_pending()` — отправляет `status=pending`, `next_retry_at <= now`, при успехе `attempts=1`
- При ошибке retry: `status=failed`, `attempts` инкрементируется (предотвращает бесконечный цикл)
- При ошибке pending: остаётся `pending` (следующий час попробует снова)
- После успешной отправки: Kommo note "WhatsApp сообщение отправлено (line, тип)"
- `main()` возвращает exit code (0/1) + `sys.exit(main())` для корректной работы с cron
- UTC timestamps (ISO 8601) для всех временных полей
- `logging` вместо `print` для production-ready логирования

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
ExecStart=/usr/local/bin/python -m server.cron

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
0 * * * * cd /app && /usr/local/bin/python -m server.cron >> /var/log/whatsapp-cron.log 2>&1
```

---

## Как протестировать

### Подготовка

1. Создать тестовые записи в БД с разными сценариями:

```python
from server.db import create_message, now_iso
from datetime import datetime, timedelta, timezone

past_25h = (datetime.now(tz=timezone.utc) - timedelta(hours=25)).isoformat(timespec="seconds")
past_1h = (datetime.now(tz=timezone.utc) - timedelta(hours=1)).isoformat(timespec="seconds")

# Сценарий 1: Сообщение для повтора (sent, attempts=1, next_retry_at в прошлом)
create_message(
    kommo_lead_id=100, kommo_contact_id=12345,
    phone="+496501354144", line="first",
    termin_date="25.02.2026",
    message_text="Тест: повтор через 24ч",
    status="sent", attempts=1,
    sent_at=past_25h, next_retry_at=past_1h,
)

# Сценарий 2: Pending сообщение (создано ночью, now=9:00)
create_message(
    kommo_lead_id=101, kommo_contact_id=67890,
    phone="+496501354144", line="second",
    termin_date="01.03.2026",
    message_text="Тест: отложенное сообщение",
    status="pending", attempts=0,
    next_retry_at=past_1h,
)

# Сценарий 3: Сообщение с 3 попытками (не должно повторяться)
create_message(
    kommo_lead_id=102, kommo_contact_id=11111,
    phone="+496501354144", line="first",
    termin_date="25.02.2026",
    message_text="Тест: максимум попыток",
    status="sent", attempts=3,
    sent_at=past_25h, next_retry_at=past_1h,
)
```

### Тест 1: Запуск cron вручную

```bash
python -m server.cron
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

- [x] `cron.py` запускается без ошибок: `python -m server.cron`
- [x] `process_retries()` обрабатывает сообщения с `status IN (sent, failed)`, `attempts < 3`, `next_retry_at <= now`
- [x] `process_pending()` обрабатывает сообщения с `status=pending`, `next_retry_at <= now`
- [x] После успешной отправки: `attempts` увеличивается, `sent_at` обновляется, `next_retry_at` = now + 24ч
- [x] При ошибке retry: `status=failed`, `attempts` увеличивается (предотвращает бесконечный цикл)
- [x] При ошибке pending: остаётся `pending` (попробуем в следующий час)
- [x] Сообщения с `attempts >= 3` не обрабатываются
- [x] Проверка окна времени: вне 9-21 → "skipping retries/pending"
- [ ] Systemd timer настроен и запускается каждый час (T10 — деплой)
- [x] После успешной отправки (retry/pending): примечание добавляется в Kommo lead (non-critical, ошибка логируется)
- [x] 24 pytest-теста: retry (success, failure, max attempts, future next_retry_at, outside window, failed msg retry, multiple, mixed, no msgs, second line, kommo note, kommo note failure), pending (success, failure, outside window, future, no msgs, kommo note, sent not picked), main (success, error), lifecycle (full, failed counts, pending→sent→retry)

---

## Зависимости

**Требует:** T02 (scaffold), T03 (db), T05 (messenger), T06 (webhook), T07 (send window)
**Блокирует:** T10 (деплой — нужно настроить cron на сервере)
