# Wazzup24 API v3 — Справочник

**Дата:** 2026-02-24
**Источник:** https://wazzup24.com/help/api-en/
**Используется в:** T05 (WazzupMessenger), server/messenger/wazzup.py

---

## Базовый URL

```
https://api.wazzup24.com/v3
```

## Авторизация

```
Authorization: Bearer {API_KEY}
Content-Type: application/json
```

---

## POST /v3/message — Отправка сообщения

### Обычное сообщение

```json
{
  "channelId": "uuid-канала",
  "chatId": "491234567890",
  "chatType": "whatsapp",
  "text": "Текст сообщения"
}
```

### WABA-шаблон (наш случай)

```json
{
  "channelId": "uuid-канала",
  "chatId": "491234567890",
  "chatType": "whatsapp",
  "templateId": "uuid-шаблона",
  "templateValues": ["Переменная1", "Переменная2", "Переменная3"]
}
```

### Поля запроса

| Поле | Тип | Обязательное | Описание |
|------|-----|-------------|----------|
| `channelId` | string (UUID) | Да | ID канала WhatsApp |
| `chatId` | string | Да | Номер телефона получателя (только цифры, без `+`) |
| `chatType` | string | Да | `"whatsapp"` |
| `text` | string | Для обычных | Текст сообщения (для обычных, не шаблонных) |
| `templateId` | string (UUID) | Для шаблонов | GUID WABA-шаблона |
| `templateValues` | string[] | Для шаблонов | Значения переменных шаблона (порядок соответствует `{{1}}`, `{{2}}`, ...) |
| `buttonsObject` | object | Нет | Для кнопок с payload |
| `contentUri` | string | Нет | URL вложения |

### Ответ — 201 Created

```json
{
  "messageId": "f66c53a6-957a-46b2-b41b-5a2ef4844bcb",
  "chatId": "491234567890"
}
```

**Важно:** Успешная отправка возвращает **201**, не 200.

---

## GET /v3/templates/whatsapp — Список шаблонов

```bash
GET https://api.wazzup24.com/v3/templates/whatsapp
Authorization: Bearer {API_KEY}
```

Возвращает массив доступных одобренных WABA-шаблонов.

---

## PATCH /v3/message/:messageId — Обновление сообщения

- Можно обновить `text` или `contentUri`
- **Нельзя** менять оба одновременно

---

## Коды ошибок

| Код | Описание | Наше поведение |
|-----|----------|----------------|
| **201** | Успех | Парсим `messageId` |
| **400** | Невалидный запрос | `MessengerError`, не ретраим |
| **401** | Неверный API-ключ | `MessengerError`, не ретраим |
| **403** | Forbidden (sidecar API KEY для routers) | `MessengerError`, не ретраим |
| **429** | Rate limit (>500 req/5s) | Retry до 3 раз с паузой 1с |
| **5xx** | Серверная ошибка | Retry до 3 раз с паузой 1с |

### Формат ошибки

```json
{
  "code": "ERROR_CODE",
  "description": "Short description in English",
  "data": {}
}
```

### Известные коды ошибок

| Код | Описание |
|-----|----------|
| `TOO_MACH_ENTITIES` | Превышен лимит сущностей (100 за раз) |
| `INVALID_CONTACTS_DATA` | Невалидные данные контакта |
| `INVALID_USERS_DATA` | Невалидные данные пользователя |
| `USER_LIMIT_EXCEEDED` | Превышен лимит пользователей |
| `CHANNEL_BLOCKED` | Канал заблокирован |

---

## Rate Limits

- **500 запросов за 5 секунд** — максимум
- Счётчик сбрасывается каждые 5 секунд
- При превышении → 429 Too Many Requests

---

## WABA-шаблоны — правила

1. **Первое сообщение** клиенту — ТОЛЬКО через одобренный WABA-шаблон
2. **После ответа клиента** — 24 часа на произвольный текст (session window)
3. **Без ответа** — только шаблоны
4. Шаблоны должны быть одобрены Meta

---

## Наши шаблоны

### "Напоминание о записи или встрече" (основной)

- **templateId:** `38194e93-e926-4826-babe-19032e0bd74c`
- **Текст:** "Здравствуйте. Это {{1}}. Напоминаем о {{2}} в {{3}}. Скажите, все в силе?"
- **Кнопки (QUICK_REPLY):** "Да, буду вовремя" / "Нет, не могу прийти"
- **templateValues:** `["SternMeister", "записи на термин|термине", "25.02.2026"]`

### Другие доступные

| Шаблон | templateId |
|--------|-----------|
| "Уведомление о записи" | `3b7211aa-6fbd-4b60-bb96-02d7cc837c73` |
| "Универсальный шаблон 4" | `4e049e0c-c404-45ba-b516-5ae932260b19` |
| + ещё 6 одобренных | — |

---

## Legacy-формат (НЕ используем)

Старый формат через поле `text` (для Salesbot/amoCRM):
```
@template: {guid} { [[var1]]; [[var2]]; [[var3]] }
```

**Мы используем формат v3 API:** `templateId` + `templateValues` как отдельные поля.

---

## Ссылки

- [Sending messages](https://wazzup24.com/help/api-en/sending-messages/)
- [WABA templates](https://wazzup24.com/help/api-en/whatsapp-business-api-templates/)
- [Common errors](https://wazzup24.com/help/api-en/common-errors/)
- [Webhooks](https://wazzup24.com/help/api-en/webhooks/)
- [API entities](https://wazzup24.com/help/api-en/api-entities-and-terminology/)
