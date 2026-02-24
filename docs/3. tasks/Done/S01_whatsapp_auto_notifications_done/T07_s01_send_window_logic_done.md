**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T07 — Логика окна времени и отложенные сообщения

---

## Customer-facing инкремент

Сообщения отправляются только в промежутке 9:00–21:00. Если webhook приходит ночью (например, 23:00), сообщение откладывается до 9:00 следующего дня. Это предотвращает беспокойство клиентов в нерабочее время.

---

## Scope

### Делаем:
- Функция `is_in_send_window()` — проверка текущего времени
- Функция `get_next_send_window_start()` — расчёт следующего окна (возвращает UTC ISO string)
- Обработка часового пояса (CET/CEST для Германии) через `zoneinfo.ZoneInfo`
- Интеграция с webhook handler (T06): вне окна → `status=pending`
- DST-safe вычисление завтрашнего дня (через `datetime()` constructor, не `timedelta(days=1)`)
- Валидация `SEND_WINDOW_START < SEND_WINDOW_END` при старте
- Тесты для граничных случаев (8:59, 9:00, 21:00, 21:01), CET/CEST, DST transitions

### НЕ делаем:
- `format_time_for_message()` / `format_message()` — покрывается `WazzupMessenger.build_message_text()` (T05)
- Обработку разных часовых поясов для клиентов (все в Германии)
- Настройку окна времени через UI (только .env)
- Cron для обработки pending (будет в T08)

---

## Реализация (utils.py)

```python
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from server.config import SEND_WINDOW_START, SEND_WINDOW_END

_BERLIN_TZ = ZoneInfo("Europe/Berlin")

def is_in_send_window() -> bool:
    """Check if current Berlin time is within the send window (9:00-21:00)."""
    now_berlin = datetime.now(tz=_BERLIN_TZ)
    return SEND_WINDOW_START <= now_berlin.hour < SEND_WINDOW_END

def get_next_send_window_start() -> str:
    """Return next send window start (9:00 Berlin) as ISO 8601 UTC string.

    Constructs the target datetime from date + hour to avoid
    timedelta(days=1) giving wrong wall-clock across DST transitions
    (spring-forward = 23h day, fall-back = 25h day).
    """
    now_berlin = datetime.now(tz=_BERLIN_TZ)
    today_start = now_berlin.replace(
        hour=SEND_WINDOW_START, minute=0, second=0, microsecond=0,
    )
    if now_berlin < today_start:
        next_start = today_start
    else:
        tomorrow = now_berlin.date() + timedelta(days=1)
        next_start = datetime(
            tomorrow.year, tomorrow.month, tomorrow.day,
            SEND_WINDOW_START, 0, 0,
            tzinfo=_BERLIN_TZ,
        )
    return next_start.astimezone(timezone.utc).isoformat(timespec="seconds")
```

---

## Интеграция с app.py

```python
from server.utils import is_in_send_window, get_next_send_window_start

# В webhook handler (step 7):
if not is_in_send_window():
    next_retry_at = get_next_send_window_start()  # UTC ISO string
    msg_id = create_message(
        ...,
        status="pending",
        next_retry_at=next_retry_at,
    )
    return JSONResponse({"status": "ok", "message": "Scheduled for next send window"})
```

**Стратегия retry:**
- **pending** (вне окна): `next_retry_at` = следующее 9:00 Berlin (UTC)
- **sent** (успешная отправка): `next_retry_at` = now + 24h (для T08 cron, повтор при отсутствии ответа)
- **failed** (ошибка мессенджера): `next_retry_at` = следующее 9:00 Berlin (retry ASAP в ближайшее окно)

---

## Критерии приёмки

- [x] `is_in_send_window()` возвращает `True` для 9:00–20:59, `False` для остального времени
- [x] `get_next_send_window_start()` корректно вычисляет следующее окно:
  - Если сейчас 8:00 → сегодня в 9:00
  - Если сейчас 15:00 → завтра в 9:00
  - Если сейчас 22:00 → завтра в 9:00
- [x] Используется часовой пояс `Europe/Berlin` (CET/CEST)
- [x] DST transitions обрабатываются корректно (spring-forward, fall-back)
- [x] Webhook вне окна времени → запись в БД с `status=pending` и `next_retry_at`
- [x] Webhook внутри окна → отправка сразу
- [x] Тесты для граничных случаев (8:59, 9:00, 21:00, 21:01, midnight) проходят
- [x] Валидация SEND_WINDOW_START < SEND_WINDOW_END при старте

---

## Зависимости

**Требует:** T06 (webhook handler)
**Блокирует:** T08 (cron)
**Можно параллельно с:** T09 (Telegram alerts)
