# Kommo API v4 Reference — Leads

**Дата верификации:** 2026-03-04
**Источник:** https://developers.kommo.com/reference/leads-list

---

## GET /leads — Список лидов

### URL
```
GET https://{subdomain}.kommo.com/api/v4/leads
```

### Параметры запроса

| Параметр | Тип | Описание |
|---------|-----|----------|
| `filter[pipeline_id][]` | array | Фильтр по pipeline ID (массив). Пример: `filter[pipeline_id][]=12154099` |
| `filter[statuses][0][pipeline_id]` | int | ID воронки для фильтрации по этапу |
| `filter[statuses][0][status_id]` | int | ID этапа для фильтрации |
| `with` | string | Embed связанных сущностей. Значение `contacts` включает `_embedded.contacts` в каждом лиде |
| `page` | int | Номер страницы (начиная с 1) |
| `limit` | int/string | Количество лидов на странице, максимум **250** |

### Пагинация

- `limit` max = 250
- Когда страница пустая или ответ `204 No Content` — лиды закончились
- Итерировать: `page=1, page=2, ...` пока `_embedded.leads` непустой

### Коды ответа

| Код | Описание |
|-----|----------|
| 200 | Лиды найдены, `_embedded.leads` содержит массив |
| 204 | Нет лидов (пустой результат или конец пагинации) |

### Структура ответа

```json
{
  "_embedded": {
    "leads": [
      {
        "id": 123456,
        "name": "Клиент Иван",
        "status_id": 93860331,
        "pipeline_id": 12154099,
        "custom_fields_values": [
          {
            "field_id": 887026,
            "field_name": "Дата термина ДЦ",
            "values": [{"value": 1740960000}]
          }
        ],
        "_embedded": {
          "contacts": [
            {
              "id": 583338,
              "_links": {"self": {"href": "https://subdomain.kommo.com/api/v4/contacts/583338"}}
            }
          ]
        }
      }
    ]
  }
}
```

### Важно о `_embedded.contacts` в leads list

`_embedded.contacts` в ответе `GET /leads` содержит **только `id` и `_links`** (не полные данные контакта).
Для получения телефона и имени нужен отдельный вызов: `GET /contacts/{id}`.

### Закрытые лиды (won/lost)

Kommo по умолчанию **не включает** лиды в статусах "Закрыто/Успешно" (won) и "Закрыто/Неуспешно" (lost) в обычный список. Фильтрация по СТОП-этапам внутри активной воронки делается **в Python** после получения лидов.

---

## Пример запроса (Python)

```python
response = self._request("GET", "/leads", params={
    "filter[pipeline_id][]": 12154099,
    "with": "contacts",
    "page": page,
    "limit": 250,
})
```

---

## Паттерн пагинации

```python
leads = []
page = 1
while True:
    response = self._request("GET", "/leads", params={
        "filter[pipeline_id][]": pipeline_id,
        "with": "contacts",
        "page": page,
        "limit": 250,
    })
    if response.status_code == 204:
        break
    data = self._parse_json(response)
    page_leads = (data.get("_embedded") or {}).get("leads") or []
    if not page_leads:
        break
    leads.extend(page_leads)
    if len(page_leads) < 250:
        break  # Last page
    page += 1
```
