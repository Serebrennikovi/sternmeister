**Дата:** 2026-02-23
**Статус:** in_progress
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T10 — Деплой на Hetzner и настройка webhook

---

## Customer-facing инкремент

Система работает в продакшне на сервере Hetzner. Webhook от Kommo CRM доходит до сервиса и запускает автоматическую отправку WhatsApp-сообщений клиентам.

---

## Scope

### Делаем:
- Деплой Docker-контейнера на сервер Hetzner (65.108.154.202)
- HTTPS через ngrok tunnel (статический домен `shternmeister.ngrok.pro`)
- Webhook URL: `https://shternmeister.ngrok.pro/webhook/kommo?secret=YOUR_SECRET`
- Настройка webhook в Kommo CRM: событие "смена этапа воронки"
- Настройка systemd cron timer для повторов (каждый час)
- Webhook secret validation (secret-in-URL, т.к. Kommo не шлёт HMAC headers)

### НЕ делаем:
- CI/CD автоматизацию (пока деплой вручную)
- Мониторинг и логирование (опционально в будущем)
- Load balancing / масштабирование (не требуется на текущем этапе)
- Nginx / certbot (порт 443 занят VPN, используем ngrok)
- Rate limiting на webhook endpoint (TODO для T11 — сейчас защита через secret-in-URL)

---

## Что на сервере

Существующие сервисы (НЕ трогаем):
- **tmb-bot** + **tmb-db** — Telegram MCP bridge (PostgreSQL на порте 5432)
- **watchtower** — авто-обновление Docker контейнеров
- **x-ui** (VPN) — остановлен (порты 443, 2083, 8443 освобождены)

> **Примечание:** x-ui остановлен, поэтому порт 443 формально свободен. Однако при
> необходимости VPN может быть перезапущен, поэтому используем ngrok для HTTPS —
> это позволяет не зависеть от порта 443. Если VPN окончательно не нужен —
> альтернативой будет Nginx + certbot (надёжнее, нет зависимости от ngrok SLA).

---

## Деплой Docker-контейнера

### 1. Скопировать код на сервер

```bash
# Локально — код и Dockerfile (НЕ копировать .env — секреты создаются на сервере вручную)
# --delete удаляет на сервере файлы, которых больше нет локально (переименования и т.д.)
rsync -avz --delete -e "ssh -i ~/.ssh/max_server" \
  server/ root@65.108.154.202:/app/whatsapp/server/

rsync -avz -e "ssh -i ~/.ssh/max_server" \
  requirements.txt Dockerfile \
  root@65.108.154.202:/app/whatsapp/
```

> **Внимание:** `.env` НЕ копируется через rsync. Файл `/app/whatsapp/.env`
> создаётся и редактируется вручную на сервере через SSH, чтобы исключить
> случайное перезатирание production секретов локальными значениями.

### 2. Собрать Docker image

```bash
# На сервере
cd /app/whatsapp
docker build -t whatsapp-notifications .
```

### 3. Запустить контейнер

```bash
# Проверить, что UID 999 не занят другим системным пользователем:
# id 999 → если "no such user" — ОК, если занят — изменить UID в Dockerfile и здесь
mkdir -p /app/whatsapp/data
chown 999:999 /app/whatsapp/data

docker run -d \
  --name whatsapp-notifications \
  --restart unless-stopped \
  -p 127.0.0.1:8000:8000 \
  -v /app/whatsapp/data:/app/data \
  --env-file /app/whatsapp/.env \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  whatsapp-notifications
```

**Важно:**
- `-p 127.0.0.1:8000:8000` — только localhost, внешний доступ через ngrok
- `--log-opt` — ротация логов, предотвращает заполнение диска
- UID 999 задан в Dockerfile (`useradd --uid 999`)

### 4. Проверить статус

```bash
docker ps
docker logs whatsapp-notifications
curl http://localhost:8000/health
```

---

## Настройка ngrok tunnel

Порт 443 занят VPN (x-ui, VLESS Reality). Вместо nginx + certbot используем ngrok:
- Бесплатный статический домен
- Автоматический SSL/TLS
- Не требует DNS настройки и открытия портов

### 1. Установить ngrok

```bash
# Прямой binary install (snap auto-refresh убивает процесс → не годится для production)
# Для обновления: повторить эту команду
curl -sSL https://ngrok-agent.s3.amazonaws.com/ngrok-v3-stable-linux-amd64.tgz \
  | tar xzf - -C /usr/local/bin
```

### 2. Настроить authtoken

```bash
# Конфиг в /etc/ngrok/ (nobody не имеет home dir → явный путь)
mkdir -p /etc/ngrok
ngrok config add-authtoken <TOKEN> --config /etc/ngrok/ngrok.yml
```

### 3. Systemd service

`/etc/systemd/system/ngrok-whatsapp.service`:

```ini
[Unit]
Description=ngrok tunnel for WhatsApp notifications
After=docker.service
Requires=docker.service
StartLimitBurst=5
StartLimitIntervalSec=300

[Service]
Type=simple
User=nobody
ExecStart=/usr/local/bin/ngrok http 8000 --domain=shternmeister.ngrok.pro --config=/etc/ngrok/ngrok.yml --log=stdout --log-level=warn
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

```bash
systemctl daemon-reload
systemctl enable ngrok-whatsapp
systemctl start ngrok-whatsapp
```

### 4. Проверить HTTPS

```bash
curl https://shternmeister.ngrok.pro/health
```

---

## Настройка webhook secret

Kommo стандартные webhooks (leads.status_changed) **не** шлют HMAC-подпись. Для защиты endpoint от несанкционированных запросов используется shared secret в URL.

> **Known risk:** Secret в query string может попасть в access-логи uvicorn и ngrok.
> Kommo не поддерживает кастомные заголовки для стандартных webhooks, поэтому
> secret-in-URL — единственный вариант. Для митигации: не выводить логи uvicorn
> в shared-системы и ротировать секрет при компрометации.

### 1. Сгенерировать секрет

```bash
# Выполнить на сервере (или локально — результат скопировать в .env на сервер)
python3 -c "import secrets; print(secrets.token_urlsafe(32))"
```

### 2. Добавить в .env на сервере

Полный список переменных — см. `.env.example` в репозитории. Обязательные для production:

```bash
# Kommo CRM
KOMMO_DOMAIN=sternmeister.kommo.com
KOMMO_TOKEN=<реальный_токен>

# Wazzup24
WAZZUP_API_KEY=<реальный_ключ>
WAZZUP_API_URL=https://api.wazzup24.com/v3
WAZZUP_CHANNEL_ID=<реальный_channel_id>
WAZZUP_TEMPLATE_ID=<реальный_template_id>

# Webhook secret
KOMMO_WEBHOOK_SECRET=<сгенерированный_секрет>

# Telegram alerts (опционально)
TELEGRAM_BOT_TOKEN=<бот_токен>
TELEGRAM_ALERT_CHAT_ID=<chat_id>

# Settings
SEND_WINDOW_START=9
SEND_WINDOW_END=21
MAX_RETRY_ATTEMPTS=2
RETRY_INTERVAL_HOURS=24
DEDUP_WINDOW_MINUTES=10

# Database
DATABASE_PATH=./data/messages.db
```

### 3. Перезапустить контейнер

```bash
docker restart whatsapp-notifications
```

### 4. Использовать URL с секретом при настройке webhook в Kommo

```
https://shternmeister.ngrok.pro/webhook/kommo?secret=<сгенерированный_секрет>
```

---

## Настройка webhook в Kommo CRM

### 1. Открыть Kommo CRM

https://sternmeister.kommo.com → Настройки → Интеграции → Webhooks

### 2. Создать webhook

- **URL:** `https://shternmeister.ngrok.pro/webhook/kommo?secret=YOUR_SECRET`
- **Событие:** "Изменение статуса сделки" (leads.status_changed)
- **Воронки:** "Бератер", "Госники"

### 3. Сохранить и активировать

### 4. Проверить webhook

Проверить логи:

```bash
docker logs whatsapp-notifications -f
```

---

## Настройка systemd cron timer

### 1. Создать service

`/etc/systemd/system/whatsapp-cron.service`:

```ini
[Unit]
Description=WhatsApp Cron - retries and pending messages
Requires=docker.service
After=docker.service

[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec whatsapp-notifications python -m server.cron
```

### 2. Создать timer

`/etc/systemd/system/whatsapp-cron.timer`:

```ini
[Unit]
Description=Run WhatsApp Cron every hour

[Timer]
OnCalendar=hourly
Persistent=true

[Install]
WantedBy=timers.target
```

### 3. Активировать

```bash
systemctl daemon-reload
systemctl enable whatsapp-cron.timer
systemctl start whatsapp-cron.timer
```

### 4. Проверить выполнение

```bash
# Запустить вручную
docker exec whatsapp-notifications python -m server.cron

# Проверить timer
systemctl list-timers whatsapp-cron.timer
```

---

## Как протестировать

### Тест 1: Проверка деплоя

```bash
curl https://shternmeister.ngrok.pro/health
# Ожидаемый ответ:
# {"status":"ok","send_window":"9-21","in_window":true,"server_time_utc":"...","server_time_berlin":"..."}
```

### Тест 2: Проверка webhook secret

```bash
# Без секрета → 403
curl -X POST https://shternmeister.ngrok.pro/webhook/kommo
# {"status":"error","message":"Forbidden"}

# С неверным секретом → 403
curl -X POST "https://shternmeister.ngrok.pro/webhook/kommo?secret=wrong"
# {"status":"error","message":"Forbidden"}

# С правильным секретом → 200
curl -X POST "https://shternmeister.ngrok.pro/webhook/kommo?secret=YOUR_SECRET" \
  -H "Content-Type: application/json" -d '{}'
# {"status":"ok","message":"Not a status change event"}
```

### Тест 3: Проверка webhook от Kommo

1. В Kommo: открыть тестовый контакт
2. Переместить на этап "Принято от первой линии"
3. Проверить логи: `docker logs whatsapp-notifications -f`
4. Проверить WhatsApp: сообщение должно прийти
5. Проверить Kommo: примечание "WhatsApp сообщение отправлено"

### Тест 4: Проверка cron

```bash
docker exec whatsapp-notifications python -m server.cron
# Ожидаемый вывод:
# Cron started
# Retries: 0 message(s) eligible
# Pending: 0 message(s) eligible
# Cron finished
```

---

## Критерии приёмки

### Код и инфраструктура (автоматизированные)
- [x] Docker-контейнер запущен на сервере: `docker ps`
- [x] Health check доступен: `curl https://shternmeister.ngrok.pro/health` → 200
- [x] ngrok tunnel работает как systemd service (auto-restart)
- [x] Webhook secret validation работает (без секрета → 403)
- [x] Systemd cron timer работает: `systemctl status whatsapp-cron.timer`
- [x] Cron отрабатывает без ошибок: `docker exec ... python -m server.cron`
- [x] Логи пишутся с ротацией: `docker logs whatsapp-notifications`
- [x] `.env` содержит продакшн-значения (WAZZUP API, KOMMO токен, WEBHOOK_SECRET)
- [x] SQLite БД создаётся в volume: `/app/whatsapp/data/messages.db`
- [x] Docker HEALTHCHECK настроен (мониторинг через `docker ps`, статус unhealthy)

### Ручные шаги (выполняются после деплоя, требуют доступа к Kommo UI)
- [ ] Webhook URL настроен в Kommo CRM: `https://shternmeister.ngrok.pro/webhook/kommo?secret=...`
- [ ] Тестовая смена этапа воронки в Kommo → webhook доходит до сервиса → WhatsApp отправлен

> **Примечание:** Ручные шаги будут выполнены при первом production-запуске совместно
> с владельцем Kommo CRM. Если webhook настроен — отметить `[x]` и акцептовать задачу.

---

## Зависимости

**Требует:** T02-T09 (весь код готов)
**Блокирует:** T11 (интеграционное тестирование в продакшне)
