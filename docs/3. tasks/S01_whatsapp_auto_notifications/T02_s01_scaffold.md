**Дата:** 2026-02-23
**Статус:** done
**Спецификация:** [S01_whatsapp_auto_notifications.md](../../2.%20specifications/S01_whatsapp_auto_notifications.md)

# T02 — Scaffold проекта и базовая инфраструктура

---

## Customer-facing инкремент

Проект запускается локально, базовая структура готова к разработке. Разработчик может начать писать код интеграций.

---

## Scope

### Делаем:
- Создание структуры папок проекта (согласно S01)
- Настройка Python окружения (venv, Python 3.11+)
- Установка базовых зависимостей (Flask/FastAPI, requests, sqlite3)
- Создание `.env.example` с необходимыми переменными окружения
- Создание базового `app.py` (HTTP-сервер с health check)
- Создание `config.py` для загрузки переменных окружения
- Dockerfile для будущего деплоя
- `.gitignore` для исключения секретов

### НЕ делаем:
- Реализацию бизнес-логики (webhook handler, messenger layer и т.д.)
- Настройку production окружения (nginx, SSL)
- Деплой на сервер (будет в T10)

---

## Структура проекта

```
sternmeister/
├── server/
│   ├── __init__.py
│   ├── app.py              # Flask/FastAPI, health check endpoint
│   ├── config.py           # Загрузка переменных окружения
│   ├── messenger/
│   │   └── __init__.py
│   ├── kommo.py            # (пустой файл, заполним в T04)
│   ├── alerts.py           # (пустой файл, заполним в T09)
│   ├── db.py               # (пустой файл, заполним в T03)
│   └── cron.py             # (пустой файл, заполним в T08)
├── requirements.txt
├── .env.example
├── .gitignore
├── Dockerfile
└── README.md
```

---

## Зависимости (requirements.txt)

```
# Web framework
fastapi==0.115.0
uvicorn[standard]==0.32.0

# HTTP клиент
requests==2.32.3

# Переменные окружения
python-dotenv==1.0.1

# Database (встроенный sqlite3)

# Для работы с datetime
python-dateutil==2.9.0
```

---

## .env.example

```bash
# Kommo CRM
KOMMO_DOMAIN=sternmeister.kommo.com
KOMMO_TOKEN=your_kommo_token_here

# Wazzup24 (WhatsApp Business API)
WAZZUP_API_KEY=your_wazzup_api_key_here
WAZZUP_API_URL=https://api.wazzup24.com/v3
WAZZUP_CHANNEL_ID=your_wazzup_channel_id_here

# Telegram alerts
TELEGRAM_BOT_TOKEN=your_telegram_bot_token
TELEGRAM_ALERT_CHAT_ID=your_chat_id

# Settings
SEND_WINDOW_START=9
SEND_WINDOW_END=21
MAX_RETRY_ATTEMPTS=2
RETRY_INTERVAL_HOURS=24

# Database
DATABASE_PATH=./data/messages.db
```

---

## config.py (базовая реализация)

```python
import os
from dotenv import load_dotenv

load_dotenv()

# Kommo CRM
KOMMO_DOMAIN = os.getenv("KOMMO_DOMAIN")
KOMMO_TOKEN = os.getenv("KOMMO_TOKEN")

# Wazzup24
WAZZUP_API_KEY = os.getenv("WAZZUP_API_KEY")
WAZZUP_API_URL = os.getenv("WAZZUP_API_URL")
WAZZUP_CHANNEL_ID = os.getenv("WAZZUP_CHANNEL_ID")

# Telegram
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TELEGRAM_ALERT_CHAT_ID = os.getenv("TELEGRAM_ALERT_CHAT_ID")

# Settings
SEND_WINDOW_START = int(os.getenv("SEND_WINDOW_START", "9"))
SEND_WINDOW_END = int(os.getenv("SEND_WINDOW_END", "21"))
MAX_RETRY_ATTEMPTS = int(os.getenv("MAX_RETRY_ATTEMPTS", "2"))
RETRY_INTERVAL_HOURS = int(os.getenv("RETRY_INTERVAL_HOURS", "24"))

# Database
DATABASE_PATH = os.getenv("DATABASE_PATH", "./data/messages.db")
```

---

## app.py (базовая версия)

```python
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import config

app = FastAPI(title="WhatsApp Auto-notifications")

@app.get("/health")
async def health_check():
    """Health check endpoint"""
    return JSONResponse({
        "status": "ok",
        "send_window": f"{config.SEND_WINDOW_START}-{config.SEND_WINDOW_END}"
    })

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
```

---

## Dockerfile (базовая версия)

```dockerfile
FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY server/ ./server/
COPY .env.example .env

# Создание папки для БД
RUN mkdir -p /app/data

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
```

---

## .gitignore

```
# Python
__pycache__/
*.py[cod]
*$py.class
*.so
.Python
venv/
env/
ENV/

# Environment
.env

# Database
*.db
*.sqlite
*.sqlite3
data/

# IDE
.vscode/
.idea/
*.swp
*.swo

# OS
.DS_Store
Thumbs.db
```

---

## Как протестировать

1. **Создать виртуальное окружение:**
   ```bash
   cd /Users/is/sternmeister
   python3 -m venv venv
   source venv/bin/activate
   ```

2. **Установить зависимости:**
   ```bash
   pip install -r requirements.txt
   ```

3. **Создать .env:**
   ```bash
   cp .env.example .env
   # Заполнить реальные значения (WAZZUP_API_KEY и т.д.)
   ```

4. **Запустить сервер:**
   ```bash
   python server/app.py
   ```

5. **Проверить health check:**
   ```bash
   curl http://localhost:8000/health
   ```
   Ожидаемый ответ:
   ```json
   {
     "status": "ok",
     "send_window": "9-21"
   }
   ```

6. **Проверить Docker:**
   ```bash
   docker build -t whatsapp-notifications .
   docker run -p 8000:8000 whatsapp-notifications
   curl http://localhost:8000/health
   ```

---

## Критерии приёмки

- [ ] Структура папок создана согласно архитектуре из S01
- [ ] `requirements.txt` содержит все необходимые зависимости
- [ ] `.env.example` содержит все переменные окружения из S01
- [ ] `config.py` загружает переменные окружения корректно
- [ ] `app.py` запускается и отвечает на `/health` endpoint
- [ ] Проект запускается локально: `python server/app.py`
- [ ] Dockerfile собирается без ошибок: `docker build -t whatsapp-notifications .`
- [ ] Docker-контейнер запускается и health check работает
- [ ] `.gitignore` исключает `.env`, `__pycache__`, `*.db`
- [ ] README.md обновлён с инструкциями по запуску

---

## Зависимости

**Требует:** —
**Блокирует:** T03, T04, T05, T06, T07, T08, T09
