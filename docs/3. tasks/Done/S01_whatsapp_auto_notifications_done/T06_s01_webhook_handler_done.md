**Дата:** 2026-02-23
**Статус:** done
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

Реальные ID из T01, заполнены в `server/config.py`:

```python
# config.py
PIPELINE_CONFIG = {
    12154099: {  # Берётар
        9386032: "first",    # Принято от первой линии
        10093587: "second",  # Термин ДЦ
    },
    10631243: {  # Госники
        8152349: "first",    # Принято от первой линии
    },
}
```

---

## app.py (дополнение webhook endpoint)

**Примечание:** Ниже — упрощённый дизайн-скетч. Актуальная реализация — в `server/app.py`.

Ключевые решения реализации:
- **Sync handler** (`def`, не `async def`) — потому что используем блокирующие requests + sqlite3
- **Kommo отправляет `x-www-form-urlencoded`** — парсим через `parse_bracket_form()` в async dependency
- **Все ответы — HTTP 200** — чтобы Kommo не ретраил бесконечно; ошибки в JSON body (`"status": "error"`)
- **Дедупликация** — проверка `get_recent_message(lead_id, line)` перед обработкой
- **Текст формируется через `messenger.build_message_text(MessageData)`** — привязан к WABA-шаблону
- **Дата термина по `field_id`** — перебираем 3 поля (date_termin, date_termin_dc, date_termin_aa)

```python
@app.post("/webhook/kommo")
def kommo_webhook(payload: dict = Depends(_parse_webhook_payload)):
    # 1. Validate payload → extract lead_id, status_id, pipeline_id
    # 2. determine_line(pipeline_id, status_id) → "first"/"second"/None
    # 2b. Dedup: get_recent_message(lead_id, line) → skip if exists
    # 3. kommo.get_lead_contact(lead_id) → (lead, contact)
    # 4. kommo.extract_phone(contact) → "+49..."
    # 5. kommo.extract_termin_date(lead, field_id) → "25.02.2026"
    # 6. messenger.build_message_text(MessageData(line, termin_date))
    # 7. is_in_send_window() → if outside: create_message(status="pending")
    # 8. messenger.send_message(phone, message_data)
    # 9. create_message(status="sent", sent_at=..., messenger_id=...)
    # 10. kommo.add_note(lead_id, "WhatsApp сообщение отправлено ...")
```

---

## Как протестировать

### Подготовка

1. Заполнить `PIPELINE_CONFIG` в `config.py` данными из T01 (уже заполнено)
2. Убедиться, что все токены в `.env` корректны
3. Собрать и запустить: `docker build -t whatsapp-notifications . && docker run -p 8000:8000 --env-file .env whatsapp-notifications`

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

- [x] `POST /webhook/kommo` принимает form-encoded и JSON payload, возвращает 200
- [x] `determine_line()` корректно определяет "first"/"second" по `pipeline_id` + `status_id`
- [x] `messenger.build_message_text(MessageData)` формирует текст по WABA-шаблону
- [x] Сообщение отправляется через Wazzup24 WABA при смене этапа воронки
- [x] Окно времени работает: вне 9-21 → `status=pending` (attempts=0), внутри → отправка
- [x] Запись в SQLite создаётся с корректными данными (phone, line, status, attempts)
- [x] Примечание добавляется в Kommo: "WhatsApp сообщение отправлено (line) — timestamp"
- [x] Дедупликация: повторный webhook за 10 мин не вызывает повторную отправку
- [x] Все ответы — HTTP 200 (ошибки в JSON body с `"status": "error"`)

---

## Зависимости

**Требует:** T01 (PIPELINE_CONFIG), T02 (scaffold), T03 (db), T04 (kommo), T05 (messenger)
**Создаёт:** utils.py (is_in_send_window, get_next_send_window_start, parse_bracket_form)
**Блокирует:** T07 (send window), T08 (cron), T10 (деплой)
