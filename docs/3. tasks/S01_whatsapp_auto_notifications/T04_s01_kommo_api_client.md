**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T04 — Kommo API клиент

---

## Customer-facing инкремент

Система может получать информацию о контакте из Kommo CRM (имя, телефон, дата термина) и записывать примечания о статусе отправки сообщений. Это ключевая часть интеграции с CRM.

---

## Scope

### Делаем:
- Реализация Kommo API клиента (`kommo.py`)
- `GET /api/v4/leads/{id}?with=contacts` — получение сделки и контакта
- `POST /api/v4/leads/{id}/notes` — создание примечания
- Извлечение данных: имя, телефон, дата термина из custom fields
- Обработка ошибок: 401, 404, 429, 500
- Retry logic для rate limiting (429)

### НЕ делаем:
- Webhook handler (будет в T06)
- Логику определения "линии" по status_id (будет в T06)
- Интеграцию с messenger layer (будет в T05, T06)

---

## kommo.py (реализация)

```python
import requests
from typing import Dict, Optional
import config

class KommoAPIError(Exception):
    """Базовая ошибка Kommo API"""
    pass

class KommoClient:
    def __init__(self):
        self.base_url = f"https://{config.KOMMO_DOMAIN}/api/v4"
        self.token = config.KOMMO_TOKEN
        self.headers = {
            "Authorization": f"Bearer {self.token}",
            "Content-Type": "application/json"
        }

    def get_lead_with_contacts(self, lead_id: int) -> Dict:
        """
        Получить сделку с контактами

        Returns:
            {
                "id": 12345,
                "name": "Иван Иванов",
                "pipeline_id": 111,
                "status_id": 67890,
                "custom_fields_values": [...],
                "_embedded": {
                    "contacts": [...]
                }
            }
        """
        url = f"{self.base_url}/leads/{lead_id}"
        params = {"with": "contacts"}

        try:
            response = requests.get(url, headers=self.headers, params=params, timeout=10)

            if response.status_code == 401:
                raise KommoAPIError("Unauthorized: проверьте KOMMO_TOKEN")
            elif response.status_code == 404:
                raise KommoAPIError(f"Lead {lead_id} not found")
            elif response.status_code == 429:
                raise KommoAPIError("Rate limit exceeded")
            elif response.status_code >= 500:
                raise KommoAPIError(f"Kommo server error: {response.status_code}")

            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            raise KommoAPIError(f"Request failed: {e}")

    def extract_phone(self, contact_data: Dict) -> Optional[str]:
        """
        Извлечь номер телефона из контакта

        Args:
            contact_data: контакт из lead["_embedded"]["contacts"][0]

        Returns:
            Телефон в формате +491234567890 или None
        """
        custom_fields = contact_data.get("custom_fields_values", [])

        for field in custom_fields:
            if field.get("field_code") == "PHONE":
                values = field.get("values", [])
                if values:
                    return values[0].get("value")

        return None

    def extract_termin_date(self, lead_data: Dict, field_name: str = "Дата Термина") -> Optional[str]:
        """
        Извлечь дату термина из custom fields сделки

        Args:
            lead_data: данные сделки
            field_name: название поля ("Дата Термина", "Дата Термина ДЦ" и т.д.)

        Returns:
            Дата в формате ISO или None
        """
        custom_fields = lead_data.get("custom_fields_values", [])

        for field in custom_fields:
            if field.get("field_name") == field_name:
                values = field.get("values", [])
                if values:
                    return values[0].get("value")

        return None

    def add_note(self, lead_id: int, text: str) -> Dict:
        """
        Добавить примечание к сделке

        Args:
            lead_id: ID сделки
            text: текст примечания

        Returns:
            {"id": 456789, ...}
        """
        url = f"{self.base_url}/leads/{lead_id}/notes"
        payload = [{
            "note_type": "common",
            "params": {
                "text": text
            }
        }]

        try:
            response = requests.post(url, headers=self.headers, json=payload, timeout=10)

            if response.status_code == 401:
                raise KommoAPIError("Unauthorized: проверьте KOMMO_TOKEN")
            elif response.status_code == 404:
                raise KommoAPIError(f"Lead {lead_id} not found")
            elif response.status_code >= 500:
                raise KommoAPIError(f"Kommo server error: {response.status_code}")

            response.raise_for_status()
            data = response.json()
            return data["_embedded"]["notes"][0]

        except requests.exceptions.RequestException as e:
            raise KommoAPIError(f"Request failed: {e}")

# Глобальный экземпляр
kommo = KommoClient()
```

---

## Как протестировать

### Подготовка

1. Получить `lead_id` из T01 (тестовый контакт)
2. Убедиться, что `KOMMO_TOKEN` в `.env` валиден

### Тест 1: Получение сделки

```python
from server.kommo import kommo

lead_id = 12345  # Из T01

try:
    lead = kommo.get_lead_with_contacts(lead_id)
    print(f"✅ Lead получен: {lead['name']}")
    print(f"   Pipeline ID: {lead['pipeline_id']}")
    print(f"   Status ID: {lead['status_id']}")

    # Извлечь контакт
    contacts = lead["_embedded"]["contacts"]
    if contacts:
        contact = contacts[0]
        phone = kommo.extract_phone(contact)
        print(f"✅ Телефон: {phone}")
    else:
        print("⚠️ Контакты не найдены")

except Exception as e:
    print(f"❌ Ошибка: {e}")
```

### Тест 2: Извлечение даты термина

```python
from server.kommo import kommo

lead_id = 12345

lead = kommo.get_lead_with_contacts(lead_id)
termin_date = kommo.extract_termin_date(lead, "Дата Термина")
print(f"✅ Дата Термина: {termin_date}")
```

### Тест 3: Добавление примечания

```python
from server.kommo import kommo
from datetime import datetime

lead_id = 12345
note_text = f"WhatsApp сообщение отправлено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"

try:
    note = kommo.add_note(lead_id, note_text)
    print(f"✅ Примечание добавлено: ID {note['id']}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
```

### Проверка в Kommo CRM

1. Открыть сделку в Kommo: https://sternmeister.kommo.com/leads/detail/{lead_id}
2. Проверить, что примечание отобразилось
3. Проверить текст и время

---

## Критерии приёмки

- [ ] `get_lead_with_contacts(lead_id)` возвращает данные сделки с контактами
- [ ] `extract_phone(contact)` извлекает телефон из поля `PHONE`
- [ ] `extract_termin_date(lead, field_name)` извлекает дату из custom field
- [ ] `add_note(lead_id, text)` создаёт примечание в Kommo и возвращает note_id
- [ ] Обработка ошибок: 401 → "Unauthorized", 404 → "Not found", 500 → "Server error"
- [ ] Тесты выполняются без ошибок с реальным тестовым lead_id из T01
- [ ] Примечание отображается в Kommo CRM

---

## Зависимости

**Требует:** T01 (lead_id получен), T02 (scaffold)
**Блокирует:** T06 (webhook handler)
