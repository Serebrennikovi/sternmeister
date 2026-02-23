**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T09 — Telegram алерты

---

## Customer-facing инкремент

При ошибке отправки WhatsApp-сообщения (Green API недоступен, невалидный номер, Kommo API не отвечает) команда получает мгновенное уведомление в Telegram. Это позволяет быстро реагировать на проблемы.

---

## Scope

### Делаем:
- Реализация `alerts.py` для отправки Telegram-сообщений
- Создание Telegram бота через @BotFather
- Получение `chat_id` для получения алертов
- Интеграция алертов в webhook handler (T06) и cron (T08)
- Форматирование алертов: тип ошибки, контекст, timestamp

### НЕ делаем:
- Telegram бота для управления системой (только алерты)
- Интеграцию с другими каналами (Slack, Email)
- Dashboard для мониторинга (только push-уведомления)

---

## Создание Telegram бота

### 1. Создать бота через @BotFather

1. Открыть Telegram, найти @BotFather
2. Отправить `/newbot`
3. Указать имя: `Sternmeister WhatsApp Alerts`
4. Указать username: `sternmeister_whatsapp_bot`
5. Получить токен: `1234567890:ABCdefGHIjklMNOpqrsTUVwxyz`

### 2. Получить chat_id

1. Отправить любое сообщение боту
2. Открыть: `https://api.telegram.org/bot<TOKEN>/getUpdates`
3. Найти `"chat":{"id": 123456789, ...}`
4. Записать `chat_id`

---

## alerts.py (реализация)

```python
import requests
from typing import Optional
import config
from datetime import datetime

class TelegramAlerter:
    def __init__(self):
        self.bot_token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = config.TELEGRAM_ALERT_CHAT_ID
        self.base_url = f"https://api.telegram.org/bot{self.bot_token}"

    def send_alert(self, message: str, level: str = "ERROR"):
        """
        Отправить алерт в Telegram

        Args:
            message: текст сообщения
            level: уровень ("ERROR", "WARNING", "INFO")
        """
        if not self.bot_token or not self.chat_id:
            print(f"⚠️ Telegram not configured, skipping alert: {message}")
            return

        # Форматирование с emoji
        emoji_map = {
            "ERROR": "🔴",
            "WARNING": "⚠️",
            "INFO": "ℹ️"
        }

        emoji = emoji_map.get(level, "📌")
        timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        formatted_message = f"{emoji} *{level}*\n\n{message}\n\n_Time: {timestamp}_"

        try:
            response = requests.post(
                f"{self.base_url}/sendMessage",
                json={
                    "chat_id": self.chat_id,
                    "text": formatted_message,
                    "parse_mode": "Markdown"
                },
                timeout=10
            )

            if response.status_code != 200:
                print(f"❌ Telegram alert failed: {response.text}")

        except Exception as e:
            print(f"❌ Telegram alert error: {e}")

    def alert_messenger_error(self, phone: str, error: str):
        """Алерт: ошибка отправки WhatsApp"""
        message = (
            f"Ошибка отправки WhatsApp-сообщения\n"
            f"Телефон: `{phone}`\n"
            f"Ошибка: {error}"
        )
        self.send_alert(message, level="ERROR")

    def alert_kommo_error(self, lead_id: int, error: str):
        """Алерт: ошибка Kommo API"""
        message = (
            f"Ошибка Kommo API\n"
            f"Lead ID: {lead_id}\n"
            f"Ошибка: {error}"
        )
        self.send_alert(message, level="ERROR")

    def alert_cron_error(self, error: str):
        """Алерт: ошибка cron-задачи"""
        message = f"Ошибка cron-задачи\n\nОшибка: {error}"
        self.send_alert(message, level="ERROR")

    def alert_info(self, message: str):
        """Информационный алерт"""
        self.send_alert(message, level="INFO")

# Глобальный экземпляр
alerter = TelegramAlerter()
```

---

## Интеграция с webhook handler (app.py)

```python
from server.alerts import alerter

@app.post("/webhook/kommo")
async def kommo_webhook(request: Request):
    # ... (существующий код)

    # 3. Получение контакта из Kommo
    try:
        lead = kommo.get_lead_with_contacts(lead_id)
    except KommoAPIError as e:
        alerter.alert_kommo_error(lead_id, str(e))
        raise HTTPException(status_code=500, detail=f"Kommo API error: {e}")

    # ... проверки телефона, даты термина

    if not phone:
        alerter.send_alert(
            f"Телефон не найден для lead {lead_id}",
            level="WARNING"
        )
        raise HTTPException(status_code=400, detail="Phone not found")

    # 6. Отправка сообщения
    try:
        result = messenger.send_message(phone, message_text)
    except MessengerError as e:
        alerter.alert_messenger_error(phone, str(e))
        db.create_message(
            # ... status="failed"
        )
        raise HTTPException(status_code=500, detail=f"Messenger error: {e}")

    # ... (остальной код)
```

---

## Интеграция с cron (cron.py)

```python
from server.alerts import alerter

def process_retries():
    # ... (существующий код)

    for msg in messages:
        try:
            result = messenger.send_message(msg['phone'], msg['message_text'])
            # ...
        except MessengerError as e:
            db.update_message(msg['id'], status="failed")
            alerter.alert_messenger_error(msg['phone'], str(e))
            print(f"    ❌ Retry failed: {e}")

def main():
    try:
        process_retries()
        process_pending()
    except Exception as e:
        alerter.alert_cron_error(str(e))
        print(f"❌ Cron error: {e}")
```

---

## Как протестировать

### Тест 1: Прямая отправка алерта

```python
from server.alerts import alerter

# Тест ERROR
alerter.send_alert("Тестовое сообщение об ошибке", level="ERROR")

# Тест WARNING
alerter.send_alert("Тестовое предупреждение", level="WARNING")

# Тест INFO
alerter.alert_info("Система запущена успешно")

# Проверить Telegram → должны прийти 3 сообщения с разными emoji
```

### Тест 2: Алерт при ошибке messenger

```python
from server.alerts import alerter

alerter.alert_messenger_error(
    phone="+996501354144",
    error="Green API timeout after 15s"
)

# Проверить Telegram → должен прийти алерт с телефоном и ошибкой
```

### Тест 3: Алерт при ошибке Kommo API

```python
from server.alerts import alerter

alerter.alert_kommo_error(
    lead_id=12345,
    error="404 Not Found"
)

# Проверить Telegram → должен прийти алерт с lead_id
```

### Тест 4: Интеграция с webhook

1. Временно изменить Green API token на невалидный
2. Отправить webhook → должен прийти алерт "Ошибка отправки WhatsApp"
3. Вернуть корректный token

### Тест 5: Интеграция с cron

1. Создать сообщение с несуществующим номером
2. Запустить `python server/cron.py`
3. Проверить Telegram → должен прийти алерт об ошибке повтора

---

## Критерии приёмки

- [ ] Telegram бот создан через @BotFather, токен получен
- [ ] `chat_id` получен и записан в `.env`
- [ ] `alerts.py` отправляет сообщения в Telegram корректно
- [ ] Форматирование работает: emoji (🔴/⚠️/ℹ️), уровень, timestamp
- [ ] `alert_messenger_error()` отправляет алерт с телефоном и ошибкой
- [ ] `alert_kommo_error()` отправляет алерт с lead_id и ошибкой
- [ ] `alert_cron_error()` отправляет алерт при ошибке cron
- [ ] Интеграция с webhook handler: при ошибке Green API → алерт в Telegram
- [ ] Интеграция с cron: при ошибке повтора → алерт в Telegram
- [ ] Если токен не настроен → вывод в консоль, но не падает

---

## Зависимости

**Требует:** T02 (scaffold)
**Блокирует:** —
**Можно параллельно с:** T03, T04, T05, T07
