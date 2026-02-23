**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T07 — Логика окна времени и отложенные сообщения

---

## Customer-facing инкремент

Сообщения отправляются только в промежутке 9:00–21:00. Если webhook приходит ночью (например, 23:00), сообщение откладывается до 9:00 следующего дня. Это предотвращает беспокойство клиентов в нерабочее время.

---

## Scope

### Делаем:
- Функция `is_in_send_window()` — проверка текущего времени
- Функция `get_next_send_window_start()` — расчёт следующего окна
- Обработка часового пояса (CET/CEST для Германии)
- Интеграция с webhook handler (T06): вне окна → `status=pending`
- Тесты для граничных случаев (8:59, 9:00, 21:00, 21:01)

### НЕ делаем:
- Обработку разных часовых поясов для клиентов (все в Германии)
- Настройку окна времени через UI (только .env)
- Cron для обработки pending (будет в T08)

---

## Реализация (utils.py)

```python
from datetime import datetime, timedelta
import pytz
import config

# Часовой пояс Германии
GERMANY_TZ = pytz.timezone("Europe/Berlin")

def is_in_send_window() -> bool:
    """
    Проверка: текущее время в Германии в окне отправки (9-21)?

    Returns:
        True если можно отправлять, False если нужно отложить
    """
    now = datetime.now(GERMANY_TZ)
    hour = now.hour

    return config.SEND_WINDOW_START <= hour < config.SEND_WINDOW_END

def get_next_send_window_start() -> datetime:
    """
    Получить следующее время начала окна отправки (9:00)

    Returns:
        datetime объект (aware, Europe/Berlin)
    """
    now = datetime.now(GERMANY_TZ)

    if now.hour < config.SEND_WINDOW_START:
        # Сегодня в 9:00
        next_window = now.replace(
            hour=config.SEND_WINDOW_START,
            minute=0,
            second=0,
            microsecond=0
        )
    else:
        # Завтра в 9:00
        tomorrow = now + timedelta(days=1)
        next_window = tomorrow.replace(
            hour=config.SEND_WINDOW_START,
            minute=0,
            second=0,
            microsecond=0
        )

    return next_window

def format_time_for_message(dt: datetime) -> str:
    """
    Форматировать datetime для сообщения: "25.02 в 14:00"

    Args:
        dt: datetime объект (ISO string или datetime)

    Returns:
        Строка в формате "DD.MM в HH:MM"
    """
    if isinstance(dt, str):
        dt = datetime.fromisoformat(dt.replace("Z", "+00:00"))

    # Конвертировать в Europe/Berlin если нужно
    if dt.tzinfo is None:
        dt = GERMANY_TZ.localize(dt)
    else:
        dt = dt.astimezone(GERMANY_TZ)

    return dt.strftime("%d.%m в %H:%M")

def format_message(name: str, termin_date: str, line: str) -> str:
    """
    Форматировать текст сообщения для логирования

    Args:
        name: имя клиента
        termin_date: дата в формате "25.02 в 14:00"
        line: "first" или "second"

    Returns:
        Текст сообщения (для логов)
    """
    if line == "first":
        return (
            f"Здравствуйте, {name}! Это SternMeister. "
            f"Напоминаем о необходимости записаться на термин. "
            f"Ближайшая дата: {termin_date}. Скажите, запишемся?"
        )
    else:  # second
        return (
            f"Здравствуйте, {name}! Это SternMeister. "
            f"Напоминаем о термине {termin_date}. "
            f"Скажите, все в силе?"
        )
```

---

## Обновление app.py (использование utils)

```python
from server.utils import is_in_send_window, get_next_send_window_start, format_time_for_message

# В webhook handler:

# 5. Проверка окна времени
if not is_in_send_window():
    next_retry_at = get_next_send_window_start()
    message_id = db.create_message(
        kommo_contact_id=contact["id"],
        phone=phone,
        line=line,
        message_text=message_text,
        status="pending",
        messenger_backend=config.MESSENGER_BACKEND,
        next_retry_at=next_retry_at
    )
    return JSONResponse({
        "status": "ok",
        "message": f"Scheduled for {next_retry_at.isoformat()}",
        "message_id": message_id
    })
```

---

## Как протестировать

### Тест 1: Граничные случаи

```python
from server.utils import is_in_send_window, get_next_send_window_start
from datetime import datetime
import pytz

# Mock текущего времени
def test_send_window():
    test_cases = [
        ("2026-02-23 08:59:00", False, "Сегодня 09:00"),  # До окна
        ("2026-02-23 09:00:00", True, None),              # Начало окна
        ("2026-02-23 15:30:00", True, None),              # Внутри окна
        ("2026-02-23 20:59:00", True, None),              # Конец окна
        ("2026-02-23 21:00:00", False, "Завтра 09:00"),   # После окна
        ("2026-02-23 23:30:00", False, "Завтра 09:00"),   # Поздно ночью
    ]

    for time_str, expected_in_window, expected_next in test_cases:
        # Здесь можно использовать mock для datetime.now()
        # Пример: с библиотекой freezegun
        print(f"{time_str}: in_window={expected_in_window}")

test_send_window()
```

### Тест 2: Форматирование даты

```python
from server.utils import format_time_for_message
from datetime import datetime

# ISO string
dt_str = "2026-02-25T14:30:00Z"
formatted = format_time_for_message(dt_str)
print(f"{dt_str} → {formatted}")
# Ожидается: "25.02 в 14:30" (или "25.02 в 15:30" если CET/CEST)

# datetime объект
dt = datetime(2026, 3, 15, 10, 0, 0)
formatted = format_time_for_message(dt)
print(f"{dt} → {formatted}")
# Ожидается: "15.03 в 10:00"
```

### Тест 3: Интеграция с webhook

1. **Отправить webhook в рабочее время (например, 15:00):**
   ```bash
   python test_webhook.py
   ```
   Проверить: сообщение отправлено сразу, `status=sent`

2. **Изменить системное время на 22:00 или установить `SEND_WINDOW_END=14`:**
   ```bash
   SEND_WINDOW_END=14 python server/app.py
   ```
   Отправить webhook → проверить `status=pending`, `next_retry_at` установлен на 9:00 следующего дня

3. **Проверить БД:**
   ```bash
   sqlite3 data/messages.db
   SELECT id, status, next_retry_at FROM messages;
   ```

---

## Критерии приёмки

- [ ] `is_in_send_window()` возвращает `True` для 9:00–20:59, `False` для остального времени
- [ ] `get_next_send_window_start()` корректно вычисляет следующее окно:
  - Если сейчас 8:00 → сегодня в 9:00
  - Если сейчас 15:00 → завтра в 9:00
  - Если сейчас 22:00 → завтра в 9:00
- [ ] Используется часовой пояс `Europe/Berlin` (CET/CEST)
- [ ] `format_time_for_message()` форматирует дату корректно: "25.02 в 14:30"
- [ ] Webhook вне окна времени → запись в БД с `status=pending` и `next_retry_at`
- [ ] Webhook внутри окна → отправка сразу
- [ ] Тесты для граничных случаев (8:59, 9:00, 21:00, 21:01) проходят

---

## Зависимости

**Требует:** T02 (scaffold)
**Блокирует:** T06 (webhook handler), T08 (cron)
**Можно параллельно с:** T03, T04, T05
