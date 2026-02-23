FROM python:3.11-slim

WORKDIR /app

# Установка зависимостей
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Копирование кода
COPY server/ ./server/

# Непривилегированный пользователь
RUN useradd --system --no-create-home app \
    && mkdir -p /app/data \
    && chown -R app:app /app/data

USER app

EXPOSE 8000

CMD ["python", "-m", "uvicorn", "server.app:app", "--host", "0.0.0.0", "--port", "8000"]
