**Дата:** 2026-03-04
**Статус:** draft
**Спецификация:** docs/2. specifications/S02_notifications_expansion.md

# T14 — Деплой S02 на Hetzner

## Customer-facing инкремент

Новая цепочка уведомлений S02 работает в продакшне: реальные клиенты получают Г1, Б1 при смене этапа, а клиенты Бератера — автоматические напоминания за 3/1 день и в день термина.

## Scope

1. **Миграция БД на сервере**: запустить `migrate_db()` на живой базе (SQLite на Hetzner)
   - Сделать backup перед миграцией:
     ```bash
     cp /app/whatsapp/data/messages.db /app/whatsapp/data/messages.db.bak.$(date +%Y%m%d%H%M)
     ```
   - `migrate_db()` идемпотентна — безопасно вызывать при старте контейнера
   - Проверить после: новые line-значения проходят CHECK; индекс idx_dedup_temporal существует
2. **Docker rebuild + redeploy**: аналогично T10 (S01 деплой)
   - `docker build`, `docker stop`, `docker run`
   - Проверить `/health` после старта
3. **Smoke-тест в продакшне**:
   - Перевести тестовый лид в «Консультация проведена» (Бух Гос) → проверить WABA
   - Перевести тестовый лид в «Принято от первой линии» (Бух Бератер) → проверить WABA
   - Создать лид с датой ДЦ = завтра → дождаться cron → проверить WABA

## Rollback-процедура

Если деплой завершился с ошибкой после `migrate_db()`:

```bash
# 1. Остановить новый контейнер
docker stop whatsapp-notifications

# 2. Восстановить базу из backup
cp /app/whatsapp/data/messages.db.bak.<timestamp> /app/whatsapp/data/messages.db

# 3. Запустить старый образ (предыдущий тег)
docker run -d --name whatsapp-notifications \
  --env-file /app/whatsapp/.env \
  -v /app/whatsapp/data:/app/data \
  -p 8000:8000 \
  whatsapp-notifications:<previous_tag>

# 4. Проверить /health
curl https://shternmeister.ngrok.pro/health
```

> **Важно:** всегда тегировать образ перед деплоем (`docker build -t whatsapp-notifications:s02 .`), чтобы старый образ был доступен для отката. Предыдущий тег — `whatsapp-notifications:s01` (или проверить `docker images`).

---

## Out of scope

- Настройка нового webhook URL (URL не меняется с S01)
- WABA-шаблон Б2 (за 7 дней) — деплоится с заглушкой, GUID добавим отдельно

## Как протестировать

1. `curl https://shternmeister.ngrok.pro/health` → `status: ok`, `failed_temporal` присутствует
2. Тестовый webhook-вызов → WABA-сообщение в тестовый номер
3. Проверить логи: `docker logs <container>` — нет FATAL/CRITICAL ошибок
4. Проверить БД: `sqlite3 data/messages.db "SELECT line, COUNT(*) FROM messages GROUP BY line"` → новые типы присутствуют

## Критерии приёмки

1. Сервис стартует без ошибок (`/health` → 200 OK)
2. `/health` содержит поле `failed_temporal`
3. Backup БД сделан до миграции
4. Миграция БД применена: CHECK constraint и idx_dedup_temporal проверены
5. Webhook Г1 и Б1 работают в продакшне (тест на тестовом лиде)
6. Cron process_temporal_triggers() запускается (виден в логах)
7. Telegram-алерт при старте не сигнализирует об ошибках

## Зависимости

- **Требует:** T12, T13 (всё реализовано и тесты зелёные)
