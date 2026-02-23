# Security Checklist — VPS / Docker

**Версия:** 2.0
**Дата обновления:** 20 февраля 2026
**Статус:** Действует

---

## Назначение

Чеклист безопасности для проверки перед деплоем на VPS. Специфичен для архитектуры TGB: VPS + Docker Compose + PostgreSQL + Telegram Bot.

---

## 1. VPS и сеть

### Firewall (UFW)

- [ ] UFW включён и активен (`ufw status`)
- [ ] Порт 22 (SSH) открыт только для конкретных IP (или через ключи)
- [ ] Порт 5432 (PostgreSQL) открыт только для IP разработчика
- [ ] Все остальные порты закрыты (`ufw default deny incoming`)
- [ ] Проверить: `ufw status numbered` — нет лишних правил

### SSH

- [ ] Вход только по SSH-ключам (PasswordAuthentication no)
- [ ] Root login отключён или только по ключу
- [ ] SSH-ключ защищён паролем локально

---

## 2. Docker

### Контейнеры

- [ ] Контейнеры не запускаются с `--privileged`
- [ ] PostgreSQL volume смонтирован для персистентности данных
- [ ] Контейнеры в одной Docker network (не используют host network)
- [ ] Порт 5432 проброшен через docker-compose (не через `--network host`)

### Образы

- [ ] Используются официальные образы (pgvector/pgvector, python)
- [ ] Образы с конкретными тегами, не `latest`

---

## 3. Secrets и credentials

### Хранение

- [ ] `.env` файл с `chmod 600` (только owner)
- [ ] `.env` в `.gitignore` (никогда не коммитится)
- [ ] `.env.example` содержит только ключи без значений
- [ ] Нет secrets в git history (`git log -p | grep -i "password\|token\|api_key" | head -20`)

### Переменные

- [ ] `TMB_DATABASE_URL` — пароль 32+ символов, сгенерирован (`openssl rand -base64 32`)
- [ ] `TMB_TELEGRAM_BOT_TOKEN` — получен через BotFather
- [ ] `TMB_OPENAI_API_KEY` — с минимальными scopes

### Локальная машина

- [ ] MCP-конфигурация (`~/.claude/settings.json`, `~/.cursor/mcp.json`) содержит реальные credentials — убедиться что эти файлы не синхронизируются в облако
- [ ] Локальный `.env` (если есть) в `.gitignore`

---

## 4. PostgreSQL

- [ ] Пользователь `tmb_user` — не superuser (только необходимые права)
- [ ] Пароль сгенерирован, не словарный
- [ ] БД `telegram_mcp` — отдельная от системных
- [ ] Подключение через UFW IP whitelist (SSL не используется)

---

## 5. Telegram Bot

- [ ] Bot token хранится только в `.env` на VPS
- [ ] Бот добавлен только в нужные группы
- [ ] Бот не имеет admin-прав в группах (нужны только права на чтение сообщений)

---

## 6. Backup

- [ ] Docker volume с данными PostgreSQL бэкапится (pg_dump или volume snapshot)
- [ ] Проверен restore из бэкапа

---

## 7. Перед первым деплоем

### Критичные (блокеры)

- [ ] UFW настроен (PostgreSQL не 0.0.0.0/0!)
- [ ] Secrets в `.env` с `chmod 600`
- [ ] SSH только по ключам
- [ ] Пароль PostgreSQL сгенерирован

### Важные (можно чуть позже)

- [ ] `tmb_user` не superuser
- [ ] Backup настроен
- [ ] Docker образы с конкретными тегами

---

## Команды для проверки

### UFW

```bash
# Проверить что PostgreSQL не открыт всем
ufw status | grep 5432
```

### Docker

```bash
# Проверить что контейнеры работают
docker compose ps

# Проверить что порты не проброшены на 0.0.0.0
docker compose port db 5432
```

### Secrets в git

```bash
# Поиск потенциальных секретов в истории
git log -p | grep -i "password\|secret\|api_key\|token" | head -20
```

---

_Чеклист является обязательным перед первым деплоем на VPS._
