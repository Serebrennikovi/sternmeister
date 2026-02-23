**Дата:** 2026-02-23
**Статус:** draft
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T10 — Деплой на Hetzner и настройка webhook

---

## Customer-facing инкремент

Система работает в продакшне на сервере Hetzner. Webhook от Kommo CRM доходит до сервиса и запускает автоматическую отправку WhatsApp-сообщений клиентам.

---

## Scope

### Делаем:
- Деплой Docker-контейнера на сервер Hetzner (65.108.154.202)
- Настройка nginx reverse proxy (HTTPS, SSL сертификат)
- Получение публичного URL для webhook: `https://whatsapp.sternmeister.com/webhook/kommo`
- Настройка webhook в Kommo CRM: событие "смена этапа воронки"
- Настройка systemd cron timer для повторов (каждый час)
- Проверка firewall, открытие портов 80, 443

### НЕ делаем:
- CI/CD автоматизацию (пока деплой вручную)
- Мониторинг и логирование (опционально в будущем)
- Load balancing / масштабирование (не требуется на текущем этапе)

---

## Подготовка сервера

### 1. SSH подключение

```bash
ssh -i ~/.ssh/max_server root@65.108.154.202
```

### 2. Установка зависимостей (если не установлены)

```bash
# Docker уже установлен (29.2.1)
docker --version

# Nginx
apt update
apt install nginx -y

# Certbot для SSL
apt install certbot python3-certbot-nginx -y
```

### 3. Настройка домена (DNS)

Добавить A-запись в DNS:

```
whatsapp.sternmeister.com → 65.108.154.202
```

Проверить:
```bash
dig whatsapp.sternmeister.com
```

---

## Деплой Docker-контейнера

### 1. Скопировать код на сервер

```bash
# Локально
rsync -avz -e "ssh -i ~/.ssh/max_server" \
  /Users/is/sternmeister/server \
  /Users/is/sternmeister/requirements.txt \
  /Users/is/sternmeister/Dockerfile \
  /Users/is/sternmeister/.env \
  root@65.108.154.202:/app/
```

### 2. Собрать Docker image

```bash
# На сервере
cd /app
docker build -t whatsapp-notifications .
```

### 3. Запустить контейнер

```bash
docker run -d \
  --name whatsapp-notifications \
  --restart unless-stopped \
  -p 8000:8000 \
  -v /app/data:/app/data \
  -v /app/.env:/app/.env \
  whatsapp-notifications
```

### 4. Проверить статус

```bash
docker ps
docker logs whatsapp-notifications

# Проверить health check
curl http://localhost:8000/health
```

---

## Настройка Nginx reverse proxy

### 1. Создать конфигурацию

`/etc/nginx/sites-available/whatsapp`:

```nginx
server {
    listen 80;
    server_name whatsapp.sternmeister.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
```

### 2. Активировать конфигурацию

```bash
ln -s /etc/nginx/sites-available/whatsapp /etc/nginx/sites-enabled/
nginx -t
systemctl reload nginx
```

### 3. Настроить SSL (Let's Encrypt)

```bash
certbot --nginx -d whatsapp.sternmeister.com
```

Выбрать:
- Email для уведомлений
- Согласиться с условиями
- Redirect HTTP → HTTPS: Yes

### 4. Проверить HTTPS

```bash
curl https://whatsapp.sternmeister.com/health
```

---

## Настройка webhook в Kommo CRM

### 1. Открыть Kommo CRM

https://sternmeister.kommo.com → Настройки → Интеграции → Webhooks

### 2. Создать webhook

- **URL:** `https://whatsapp.sternmeister.com/webhook/kommo`
- **Событие:** "Изменение статуса сделки" (leads.status_changed)
- **Воронки:** "Бератер", "Госники"

### 3. Сохранить и активировать

### 4. Проверить webhook (тестовая отправка)

В Kommo UI есть кнопка "Отправить тестовый webhook" → нажать

Проверить логи:

```bash
docker logs whatsapp-notifications | tail -20
```

---

## Настройка systemd cron timer

### 1. Создать service

`/etc/systemd/system/whatsapp-cron.service`:

```ini
[Unit]
Description=WhatsApp Auto-notifications Cron

[Service]
Type=oneshot
ExecStart=/usr/bin/docker exec whatsapp-notifications python server/cron.py

[Install]
WantedBy=multi-user.target
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
systemctl status whatsapp-cron.timer
```

### 4. Проверить выполнение

```bash
# Запустить вручную
systemctl start whatsapp-cron.service

# Проверить логи
journalctl -u whatsapp-cron.service -f
```

---

## Firewall и безопасность

### 1. Проверить открытые порты

```bash
ufw status
```

### 2. Открыть порты 80, 443 (если закрыты)

```bash
ufw allow 80/tcp
ufw allow 443/tcp
ufw reload
```

### 3. Ограничить доступ к webhook (опционально)

В nginx добавить whitelist IP Kommo (если известен):

```nginx
location /webhook/kommo {
    allow 123.45.67.89;  # IP Kommo
    deny all;

    proxy_pass http://127.0.0.1:8000;
    # ...
}
```

---

## Как протестировать

### Тест 1: Проверка деплоя

```bash
# На сервере
curl https://whatsapp.sternmeister.com/health

# Ожидаемый ответ:
{
  "status": "ok",
  "messenger_backend": "wazzup",
  "send_window": "9-21"
}
```

### Тест 2: Проверка webhook от Kommo

1. В Kommo: открыть тестовый контакт (из T01)
2. Переместить на этап "Принято от первой линии"
3. Проверить логи на сервере:
   ```bash
   docker logs whatsapp-notifications -f
   ```
4. Проверить WhatsApp: сообщение должно прийти на номер контакта
5. Проверить Kommo: примечание "WhatsApp сообщение отправлено"

### Тест 3: Проверка cron

```bash
# Создать тестовое сообщение для повтора
docker exec -it whatsapp-notifications python
>>> from server.db import db
>>> from datetime import datetime, timedelta
>>> db.create_message(
...     kommo_contact_id=12345,
...     phone="+996501354144",
...     line="first",
...     message_text="Тест cron",
...     status="sent",
...     messenger_backend="wazzup",
...     sent_at=datetime.now() - timedelta(hours=25),
...     next_retry_at=datetime.now() - timedelta(minutes=5)
... )

# Запустить cron вручную
systemctl start whatsapp-cron.service

# Проверить логи
journalctl -u whatsapp-cron.service -n 50
```

### Тест 4: Проверка SSL

```bash
curl -I https://whatsapp.sternmeister.com/health

# Проверить сертификат
openssl s_client -connect whatsapp.sternmeister.com:443 -servername whatsapp.sternmeister.com
```

---

## Критерии приёмки

- [ ] Docker-контейнер запущен на сервере и работает: `docker ps`
- [ ] Health check доступен: `curl https://whatsapp.sternmeister.com/health` возвращает 200
- [ ] Nginx reverse proxy настроен корректно (HTTPS, SSL сертификат валиден)
- [ ] Webhook URL настроен в Kommo CRM: `https://whatsapp.sternmeister.com/webhook/kommo`
- [ ] Тестовая смена этапа воронки в Kommo → сообщение отправляется в WhatsApp
- [ ] Примечание в Kommo создаётся: "WhatsApp сообщение отправлено"
- [ ] Systemd cron timer запускается каждый час: `systemctl status whatsapp-cron.timer`
- [ ] Firewall настроен: порты 80, 443 открыты
- [ ] Логи пишутся корректно: `docker logs whatsapp-notifications`
- [ ] `.env` содержит продакшн-значения (WAZZUP API, KOMMO токен)

---

## Зависимости

**Требует:** T02-T09 (весь код готов)
**Блокирует:** T11 (интеграционное тестирование в продакшне)
