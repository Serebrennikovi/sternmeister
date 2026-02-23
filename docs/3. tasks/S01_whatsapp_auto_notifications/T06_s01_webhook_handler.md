**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T06 — Webhook handler для Kommo

---

## Customer-facing инкремент

При смене этапа воронки в Kommo CRM клиенту автоматически отправляется персонализированное WhatsApp-сообщение с напоминанием о записи на термин. Это основная бизнес-логика системы.

---

## Scope

### Делаем:
- Endpoint `POST /webhook/kommo` для приёма событий от Kommo
- Валидация webhook payload
- Определение "линии" (first/second) по `status_id` (маппинг из T01)
- Получение контакта через Kommo API: имя, телефон, дата термина
- Формирование персонализированного сообщения
- Проверка окна времени 9-21 (если вне окна → pending)
- Отправка сообщения через messenger layer
- Запись в SQLite лога отправки
- Добавление примечания в Kommo "WhatsApp сообщение отправлено"

### НЕ делаем:
- Повторную отправку (будет в T08 — cron)
- Telegram алерты (будет в T09)

---

## Маппинг воронок (PIPELINE_CONFIG)

**Примечание:** Конкретные `pipeline_id` и `status_id` будут получены в T01. Ниже — шаблон.

```python
# config.py (дополнение)

# Маппинг status_id → линия
# Заполнить после T01
PIPELINE_CONFIG = {
    111: {  # Pipeline ID воронки "Бератер" (пример)
        67890: "first",   # "Принято от первой линии"
        67891: "second",  # "Термин ДЦ назначен"
        # ... другие этапы
    },
    222: {  # Pipeline ID воронки "Госники" (пример)
        # ... этапы
    }
}
```

---

## app.py (дополнение webhook endpoint)

```python
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse
from datetime import datetime, timedelta
import config
from server.kommo import kommo, KommoAPIError
from server.messenger import messenger, MessengerError, MessageData
from server.db import db
from server.utils import is_in_send_window, get_next_send_window_start, format_time_for_message, format_message

app = FastAPI(title="WhatsApp Auto-notifications")

# ... (health check endpoint)

def determine_line(pipeline_id: int, status_id: int) -> str | None:
    """
    Определить линию по pipeline_id и status_id

    Returns:
        "first" | "second" | None (если этап не триггерный)
    """
    pipeline = config.PIPELINE_CONFIG.get(pipeline_id)
    if not pipeline:
        return None
    return pipeline.get(status_id)

    if line == "first":
        return (
            f"Здравствуйте, {name}! Это SternMeister. "
            f"Напоминаем о необходимости записаться на термин. "
            f"Ближайшая дата: {formatted_date}. Скажите, запишемся?"
        )
    else:  # second
        return (
            f"Здравствуйте, {name}! Это SternMeister. "
            f"Напоминаем о термине {formatted_date}. "
            f"Скажите, все в силе?"
        )

@app.post("/webhook/kommo")
async def kommo_webhook(request: Request):
    """
    Webhook от Kommo CRM при смене этапа воронки
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # 1. Валидация payload
    if "leads" not in payload or "status" not in payload["leads"]:
        return JSONResponse({"status": "ok", "message": "Not a status change event"})

    lead_status = payload["leads"]["status"][0]
    lead_id = lead_status["id"]
    new_status_id = lead_status["status_id"]
    pipeline_id = lead_status["pipeline_id"]

    # 2. Определение линии
    line = determine_line(pipeline_id, new_status_id)
    if line is None:
        return JSONResponse({"status": "ok", "message": "Status not relevant"})

    # 3. Получение контакта из Kommo
    try:
        lead = kommo.get_lead_with_contacts(lead_id)
    except KommoAPIError as e:
        raise HTTPException(status_code=500, detail=f"Kommo API error: {e}")

    name = lead.get("name", "Клиент")
    contacts = lead["_embedded"].get("contacts", [])
    if not contacts:
        raise HTTPException(status_code=400, detail="No contacts found")

    contact = contacts[0]
    phone = kommo.extract_phone(contact)
    if not phone:
        raise HTTPException(status_code=400, detail="Phone not found")

    termin_date = kommo.extract_termin_date(lead, "Дата Термина")
    if not termin_date:
        raise HTTPException(status_code=400, detail="Termin date not found")

    # 4. Формирование сообщения
    message_text = format_message(name, termin_date, line)

    # 5. Проверка окна времени
    if not is_in_send_window():
        # Отложить до утра
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
            "message": "Scheduled for next send window",
            "message_id": message_id,
            "next_retry_at": next_retry_at.isoformat()
        })

    # 6. Отправка сообщения
    formatted_date = format_time_for_message(termin_date)
    message_data = MessageData(line=line, termin_date=formatted_date)

    try:
        result = messenger.send_message(phone, message_data)
    except MessengerError as e:
        # Логирование ошибки, но не падаем
        message_id = db.create_message(
            kommo_contact_id=contact["id"],
            phone=phone,
            line=line,
            message_text=message_text,
            status="failed",
            messenger_backend="wazzup"
        )
        raise HTTPException(status_code=500, detail=f"Messenger error: {e}")

    # 7. Логирование в SQLite
    message_id = db.create_message(
        kommo_contact_id=contact["id"],
        phone=phone,
        line=line,
        message_text=message_text,
        status="sent",
        messenger_backend="wazzup",
        sent_at=datetime.now(),
        next_retry_at=datetime.now() + timedelta(hours=config.RETRY_INTERVAL_HOURS),
        messenger_id=result["message_id"]
    )

    # 8. Примечание в Kommo
    try:
        note_text = f"WhatsApp сообщение отправлено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        kommo.add_note(lead_id, note_text)
    except KommoAPIError:
        # Не критично, если примечание не создалось
        pass

    return JSONResponse({
        "status": "ok",
        "message_id": message_id,
        "messenger_message_id": result["message_id"]
    })
```

---

## Как протестировать

### Подготовка

1. Заполнить `PIPELINE_CONFIG` в `config.py` данными из T01
2. Убедиться, что все токены в `.env` корректны
3. Запустить сервер: `python server/app.py`

### Тест 1: Эмуляция webhook от Kommo

Создать файл `test_webhook.py`:

```python
import requests
import json

# Webhook payload (пример, скорректировать под реальные данные из T01)
payload = {
    "leads": {
        "status": [{
            "id": 12345,  # lead_id из T01
            "status_id": 67890,  # status_id для "first" линии
            "pipeline_id": 111,  # pipeline_id воронки "Бератер"
            "old_status_id": 67889
        }]
    },
    "account": {
        "id": "xxxxx",
        "subdomain": "sternmeister"
    }
}

response = requests.post(
    "http://localhost:8000/webhook/kommo",
    json=payload,
    headers={"Content-Type": "application/json"}
)

print(f"Status: {response.status_code}")
print(f"Response: {response.json()}")
```

Запустить: `python test_webhook.py`

### Тест 2: Проверка в WhatsApp

1. Открыть WhatsApp на номере +996501354144
2. Проверить получение сообщения с персонализацией (имя, дата)

### Тест 3: Проверка в Kommo CRM

1. Открыть сделку: https://sternmeister.kommo.com/leads/detail/{lead_id}
2. Проверить примечание: "WhatsApp сообщение отправлено: YYYY-MM-DD HH:MM:SS"

### Тест 4: Проверка окна времени

1. Изменить время в `config.py`: `SEND_WINDOW_START=23, SEND_WINDOW_END=24` (вне окна)
2. Отправить webhook → проверить `status=pending` в БД
3. Вернуть реальное время → запустить cron (T08) → проверить отправку

---

## Критерии приёмки

- [ ] `POST /webhook/kommo` принимает payload и возвращает 200
- [ ] `determine_line()` корректно определяет "first"/"second" по `status_id`
- [ ] `format_message()` формирует персонализированное сообщение с именем и датой
- [ ] Сообщение отправляется через Green API при смене этапа воронки
- [ ] Окно времени работает: вне 9-21 → `status=pending`, внутри → отправка
- [ ] Запись в SQLite создаётся с корректными данными (phone, line, status, attempts)
- [ ] Примечание добавляется в Kommo: "WhatsApp сообщение отправлено"
- [ ] Тестовый webhook успешно отправляет сообщение на номер +996501354144
- [ ] Обработка ошибок: невалидный payload → 400, Kommo API недоступен → 500

---

## Зависимости

**Требует:** T01 (PIPELINE_CONFIG), T02 (scaffold), T03 (db), T04 (kommo), T05 (messenger), T07 (utils)
**Блокирует:** T08 (cron), T10 (деплой)
