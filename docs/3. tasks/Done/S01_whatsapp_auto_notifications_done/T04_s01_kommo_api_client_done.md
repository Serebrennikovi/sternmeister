**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T04 — Kommo API клиент

---

## Customer-facing инкремент

Система может получать информацию о контакте из Kommo CRM (имя, телефон, дата термина) и записывать примечания о статусе отправки сообщений. Это ключевая часть интеграции с CRM.

---

## Scope

### Делаем:
- Реализация Kommo API клиента (`kommo.py`)
- `GET /api/v4/leads/{id}?with=contacts` — получение сделки (embedded contacts сокращённые)
- `GET /api/v4/contacts/{id}` — получение полного контакта с custom_fields_values
- `POST /api/v4/leads/{id}/notes` — создание примечания
- Извлечение данных: телефон (из контакта), дата термина (из сделки) по field_id
- Нормализация телефона: пробелы, дефисы, скобки, `00`→`+`, `(0)` trunk prefix
- Конвертация Unix timestamp → "DD.MM.YYYY" (timezone: Europe/Berlin)
- Обработка ошибок: 401, 404, 429, 4xx, 5xx → `KommoAPIError`
- Retry logic для 429 (Retry-After) и 5xx (transient server errors)
- Lazy init: `get_kommo_client()` вместо module-level instance
- `_reset_client()` для тестов

### НЕ делаем:
- Webhook handler (будет в T06)
- Логику определения "линии" по status_id (будет в T06)
- Интеграцию с messenger layer (будет в T05, T06)

---

## Архитектура

### Важно: Kommo API v4 — embedded contacts сокращённые

`GET /api/v4/leads/{id}?with=contacts` возвращает lead с embedded contacts, но контакты **сокращённые** — содержат только `id`, `is_main`, `_links`. Поля `custom_fields_values` (телефон, email) **отсутствуют**.

Для получения телефона нужен **отдельный запрос** `GET /api/v4/contacts/{id}`.

**Правильный flow:**
```
get_lead_with_contacts(lead_id)  →  lead + abbreviated contacts
    ↓ extract contact_id
get_contact(contact_id)          →  full contact with custom_fields_values
    ↓
extract_phone(contact)           →  normalized phone
```

Convenience-метод `get_lead_contact(lead_id)` делает оба запроса и возвращает `(lead, contact)`.

### Даты термина — Unix timestamps

Kommo хранит кастомные поля типа `date` как **Unix timestamp** (int). Метод `extract_termin_date()` конвертирует в `"DD.MM.YYYY"` в timezone `Europe/Berlin` (все клиенты в Германии — единый CET/CEST).

---

## kommo.py (ключевые методы)

### KommoAPIError

```python
class KommoAPIError(Exception):
    """Kommo API error with optional HTTP status code."""
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code
```

### KommoClient

```python
class KommoClient:
    """Kommo CRM API v4 client.
    Uses blocking requests.Session + time.sleep for 429/5xx retry.
    Safe from sync def FastAPI handlers (threadpool).
    """

    def _request(method, path, **kwargs) -> Response
        # Centralized: 429/5xx retry, error handling (no PII in exceptions)

    def get_lead_with_contacts(lead_id) -> dict
        # GET /leads/{id}?with=contacts — abbreviated contacts

    def get_contact(contact_id) -> dict
        # GET /contacts/{id} — full data with custom_fields_values

    def get_lead_contact(lead_id) -> tuple[dict, dict]
        # Convenience: get_lead_with_contacts + get_contact → (lead, contact)

    @staticmethod
    def extract_phone(contact_data) -> str | None
        # Extract + normalize first phone from full contact
        # Handles: 00→+, (0) trunk prefix, spaces/dashes/parens

    @staticmethod
    def extract_termin_date(lead_data, field_id: int) -> str | None
        # Extract date from lead custom fields, convert timestamp → "DD.MM.YYYY"
        # Uses Europe/Berlin timezone

    def add_note(lead_id, text) -> dict
        # POST /leads/{id}/notes — returns created note with 'id'
```

### Lazy init

```python
_client: KommoClient | None = None

def get_kommo_client() -> KommoClient:
    """Shared instance — не создаётся при импорте модуля."""
    global _client
    if _client is None:
        _client = KommoClient()
    return _client
```

Это позволяет импортировать `KommoAPIError` и другие имена без `.env`.

---

## Как протестировать

### Подготовка

1. Получить `lead_id` из T01 (тестовый контакт)
2. Убедиться, что `KOMMO_TOKEN` в `.env` валиден

### Тест 1: Получение сделки и контакта (get_lead_contact)

```python
from server.kommo import get_kommo_client

kommo = get_kommo_client()
lead_id = 12345  # Из T01

try:
    lead, contact = kommo.get_lead_contact(lead_id)
    print(f"Lead: {lead['name']} (pipeline={lead['pipeline_id']}, status={lead['status_id']})")

    phone = kommo.extract_phone(contact)
    print(f"Phone: {phone}")

except Exception as e:
    print(f"Error: {e}")
```

### Тест 2: Извлечение даты термина

```python
from server.kommo import get_kommo_client
from server.config import FIELD_IDS

kommo = get_kommo_client()
lead_id = 12345

lead, _ = kommo.get_lead_contact(lead_id)
termin_date = kommo.extract_termin_date(lead, FIELD_IDS["date_termin"])
print(f"Termin date: {termin_date}")  # e.g. "25.02.2026"
```

### Тест 3: Добавление примечания

```python
from server.kommo import get_kommo_client
from datetime import datetime

kommo = get_kommo_client()
lead_id = 12345
note_text = f"WhatsApp test: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

try:
    note = kommo.add_note(lead_id, note_text)
    print(f"Note added: ID {note['id']}")
except Exception as e:
    print(f"Error: {e}")
```

### Проверка в Kommo CRM

1. Открыть сделку в Kommo: `https://sternmeister.kommo.com/leads/detail/{lead_id}`
2. Проверить, что примечание отобразилось
3. Проверить текст и время

---

## Критерии приёмки

- [ ] `get_lead_with_contacts(lead_id)` возвращает данные сделки с abbreviated contacts
- [ ] `get_contact(contact_id)` возвращает полные данные контакта с custom_fields_values
- [ ] `get_lead_contact(lead_id)` возвращает `(lead, contact)` за два API-вызова
- [ ] `extract_phone(contact)` извлекает и нормализует телефон из поля `PHONE`
- [ ] `extract_termin_date(lead, field_id)` конвертирует Unix timestamp → "DD.MM.YYYY"
- [ ] `add_note(lead_id, text)` создаёт примечание в Kommo и возвращает note с id
- [ ] Обработка ошибок: 401, 404, 429 (retry с Retry-After), 4xx, 5xx → KommoAPIError
- [ ] `get_kommo_client()` — lazy init, модуль можно импортировать без `.env`
- [ ] Docker build проходит
- [ ] Тесты выполняются без ошибок с реальным тестовым lead_id

---

## Зависимости

**Требует:** T01 (lead_id получен), T02 (scaffold)
**Блокирует:** T06 (webhook handler)
