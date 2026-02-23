**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T01 — Сбор конфигурации из Kommo CRM

---

## Customer-facing инкремент

Готовы реальные ID воронок/этапов для разработки. API токен получен и сохранён. Вся конфигурация задокументирована в [kommo_config.md](../../kommo_config.md).

---

## Scope

### Делаем:
- Получение OAuth токена или API-ключа для Kommo API v4
- Сбор pipeline_id и status_id для воронок "Бератер" и "Госники"
- Документирование custom fields (телефон, дата термина, и т.д.)
- Создание reference-файла с полной конфигурацией

### НЕ делаем:
- Настройку webhook URL в Kommo (будет позже)
- Написание кода интеграции
- Создание тестовых контактов

---

## Результаты

### Pipeline и Status ID

**Воронка "Берётар" (pipeline_id: 12154099):**
- Триггер first line: status_id 9386032 ("Принято от первой линии")
- Триггер second line: status_id 10093587 ("Термин ДЦ")

**Воронка "Госники" (pipeline_id: 10631243):**
- Триггер first line: status_id 8152349 ("Принято от первой линии")

### Custom Fields ID

**Leads:**
- Дата термина: field_id 885996
- Дата термина ДЦ: field_id 887026
- Дата термина АА: field_id 887028
- LANGUAGE_LEVEL: field_id 869928
- Lead Email: field_id 889539

**Contacts:**
- Phone: field_id 849496 (code: PHONE)
- Email: field_id 849498 (code: EMAIL)

### API Токен

- Создана private integration "Sternmeister WhatsApp"
- Long-lived token сохранён в `.env`
- Expires: 2027-05-01

---

## Документация

Создана полная конфигурация:
- [kommo_config.md](../../kommo_config.md) — reference файл с маппингом ID
- [architecture.md](../../architecture.md) — обновлены воронки и поля
- `.env` — сохранён API токен

---

## Критерии приёмки

- [x] Получены pipeline_id и status_id для всех триггерных этапов
- [x] Документирован маппинг status_id → line ("first" / "second")
- [x] Получены field_id для всех необходимых custom fields
- [x] Получен OAuth токен для Kommo API v4
- [x] Все данные задокументированы в kommo_config.md
- [x] API токен сохранён в .env
