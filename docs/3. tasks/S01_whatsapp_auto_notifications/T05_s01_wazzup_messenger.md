**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T05 — Система отправляет WhatsApp через Wazzup24 WABA

---

## Customer-facing инкремент

Система может отправлять WhatsApp-сообщения клиентам через официальный WhatsApp Business API с использованием одобренных WABA-шаблонов на номер Sternmeister +49 3046690188.

---

## Scope

### Делаем:
- Реализация `WazzupMessenger` (`messenger/wazzup.py`)
- Отправка через WABA-шаблон "Напоминание о записи или встрече"
- Структура данных `MessageData` для передачи параметров
- Форматирование шаблона: `@template: {guid} { [[var1]]; [[var2]]; [[var3]] }`
- Обработка ошибок: невалидный шаблон, недоступный API

### НЕ делаем:
- Динамический выбор шаблона (только один: "Напоминание о записи")
- Абстракцию BaseMessenger (не нужна — один backend)
- Webhook для статусов доставки (опционально в будущем)

---

## Структура

```
server/messenger/
├── __init__.py      # Экспорт messenger, MessageData, MessengerError
└── wazzup.py        # WazzupMessenger
```

---

## messenger/wazzup.py

```python
import requests
from typing import Dict
from dataclasses import dataclass
import config

class MessengerError(Exception):
    """Ошибка отправки сообщения"""
    pass

@dataclass
class MessageData:
    """Данные для формирования сообщения"""
    line: str  # "first" или "second"
    termin_date: str  # "25.02 в 14:00"

# WABA шаблон: "Напоминание о записи или встрече"
# Текст: "Здравствуйте. Это {{1}}. Напоминаем о {{2}} в {{3}}. Скажите, все в силе?"
WABA_TEMPLATE_GUID = "38194e93-e926-4826-babe-19032e0bd74c"

class WazzupMessenger:
    def __init__(self):
        self.api_key = config.WAZZUP_API_KEY
        self.channel_id = config.WAZZUP_CHANNEL_ID
        self.base_url = config.WAZZUP_API_URL

    def _format_chat_id(self, phone: str) -> str:
        """
        Форматировать номер телефона для Wazzup24

        Args:
            phone: +491234567890

        Returns:
            491234567890 (без + и других символов)
        """
        return phone.replace("+", "").replace("-", "").replace(" ", "")

    def _build_template_text(self, message_data: MessageData) -> str:
        """
        Построить текст WABA-шаблона

        Args:
            message_data: данные сообщения (line, termin_date)

        Returns:
            "@template: {guid} { [[SternMeister]]; [[термине]]; [[25.02 в 14:00]] }"
        """
        company_name = "SternMeister"

        if message_data.line == "first":
            event_type = "записи на термин"
        else:  # second
            event_type = "термине"

        # Формат: @template: {guid} { [[var1]]; [[var2]]; [[var3]] }
        template_text = (
            f"@template: {WABA_TEMPLATE_GUID} "
            f"{{ [[{company_name}]]; [[{event_type}]]; [[{message_data.termin_date}]] }}"
        )

        return template_text

    def send_message(self, phone: str, message_data: MessageData) -> Dict:
        """
        Отправить WhatsApp через Wazzup24 WABA

        Args:
            phone: +491234567890
            message_data: данные сообщения (line, termin_date)

        Returns:
            {"message_id": "waba_msg_123", "status": "sent"}

        Raises:
            MessengerError: если отправка не удалась
        """
        url = f"{self.base_url}/message"
        chat_id = self._format_chat_id(phone)
        template_text = self._build_template_text(message_data)

        payload = {
            "channelId": self.channel_id,
            "chatId": chat_id,
            "chatType": "whatsapp",
            "text": template_text
        }

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }

        try:
            response = requests.post(url, json=payload, headers=headers, timeout=15)

            if response.status_code == 400:
                raise MessengerError(f"Invalid request: {response.text}")
            elif response.status_code == 401:
                raise MessengerError("Unauthorized: проверьте WAZZUP_API_KEY")
            elif response.status_code >= 500:
                raise MessengerError(f"Wazzup24 server error: {response.status_code}")

            response.raise_for_status()
            data = response.json()

            return {
                "message_id": data.get("messageId", "unknown"),
                "status": "sent"
            }

        except requests.exceptions.Timeout:
            raise MessengerError("Wazzup24 timeout")
        except requests.exceptions.RequestException as e:
            raise MessengerError(f"Request failed: {e}")

# Глобальный экземпляр
messenger = WazzupMessenger()
```

---

## messenger/__init__.py

```python
from .wazzup import messenger, MessengerError, MessageData

__all__ = ['messenger', 'MessengerError', 'MessageData']
```

---

## Как протестировать

### Тест 1: Отправка сообщения

```python
from server.messenger import messenger, MessageData

message_data = MessageData(
    line="first",
    termin_date="25.02 в 14:00"
)

try:
    result = messenger.send_message(
        phone="+996501354144",  # Тестовый номер Ивана
        message_data=message_data
    )
    print(f"✅ Сообщение отправлено: {result['message_id']}")
except Exception as e:
    print(f"❌ Ошибка: {e}")
```

### Тест 2: Построение шаблона

```python
from server.messenger.wazzup import WazzupMessenger, MessageData

messenger = WazzupMessenger()

# Тест first линия
msg_data = MessageData(line="first", termin_date="25.02 в 14:00")
template = messenger._build_template_text(msg_data)
print(f"First: {template}")
# Ожидается: @template: 38194e93-... { [[SternMeister]]; [[записи на термин]]; [[25.02 в 14:00]] }

# Тест second линия
msg_data = MessageData(line="second", termin_date="26.02 в 10:00")
template = messenger._build_template_text(msg_data)
print(f"Second: {template}")
# Ожидается: @template: 38194e93-... { [[SternMeister]]; [[термине]]; [[26.02 в 10:00]] }
```

### Тест 3: Проверка в WhatsApp

1. Запустить тест отправки на +996501354144
2. Проверить формат сообщения:
   ```
   Здравствуйте. Это SternMeister. Напоминаем о записи на термин в 25.02 в 14:00. Скажите, все в силе?

   [Да, буду вовремя] [Нет, не могу прийти]
   ```
3. Проверить наличие кнопок (QUICK_REPLY)

---

## Критерии приёмки

- [ ] `WazzupMessenger` реализует `send_message(phone, message_data)`
- [ ] `_build_template_text()` корректно форматирует WABA-шаблон
- [ ] Отправка через Wazzup24 API работает: статус 200, messageId возвращается
- [ ] Тестовое сообщение приходит на +996501354144 с кнопками
- [ ] Обработка ошибок: 400, 401, 500 → `MessengerError`
- [ ] `MessageData` используется для передачи параметров (line, termin_date)

---

## Зависимости

**Требует:** T02 (scaffold)
**Блокирует:** T06 (webhook handler)
