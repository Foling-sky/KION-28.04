FROM python:3.11.9-slim

WORKDIR /app

# Установка зависимостей системы
RUN apt-get update && \
    apt-get install -y --no-install-recommends \
    netcat-traditional \
    && rm -rf /var/lib/apt/lists/*

# Копирование только requirements.txt для лучшего кеширования
COPY requirements.txt .

# Установка зависимостей Python с оптимизацией производительности
RUN pip install --no-cache-dir -r requirements.txt \
    && pip install --no-cache-dir uvloop httptools

# Копирование кода приложения
COPY . .

# Создание директории для логов
RUN mkdir -p logs

# Создаем скрипт запуска с проверкой доступности сервисов
RUN echo '#!/bin/bash \n\
echo "Waiting for Redis and Kafka to be ready..." \n\
REDIS_READY=0 \n\
while [ $REDIS_READY -eq 0 ]; do \n\
  nc -z $REDIS_HOST 6379 \n\
  if [ $? -eq 0 ]; then \n\
    echo "Redis is ready!" \n\
    REDIS_READY=1 \n\
  else \n\
    echo "Waiting for Redis..." \n\
    sleep 2 \n\
  fi \n\
done \n\
\n\
KAFKA_HOST=$(echo $KAFKA_BOOTSTRAP_SERVERS | cut -d: -f1) \n\
KAFKA_PORT=$(echo $KAFKA_BOOTSTRAP_SERVERS | cut -d: -f2) \n\
\n\
KAFKA_READY=0 \n\
while [ $KAFKA_READY -eq 0 ]; do \n\
  nc -z $KAFKA_HOST $KAFKA_PORT \n\
  if [ $? -eq 0 ]; then \n\
    echo "Kafka is ready!" \n\
    KAFKA_READY=1 \n\
  else \n\
    echo "Waiting for Kafka..." \n\
    sleep 2 \n\
  fi \n\
done \n\
\n\
# Проверяем доступность PostgreSQL на основе PG_HOSTS, если задано \n\
if [ -n "$PG_HOSTS" ]; then \n\
  echo "Checking PostgreSQL cluster availability..." \n\
  PG_HOSTS_ARRAY=(${PG_HOSTS//,/ }) \n\
  for PG_HOST_PORT in "${PG_HOSTS_ARRAY[@]}"; do \n\
    PG_H=$(echo $PG_HOST_PORT | cut -d: -f1) \n\
    PG_P=$(echo $PG_HOST_PORT | cut -d: -f2) \n\
    \n\
    echo "Checking PostgreSQL at $PG_H:$PG_P..." \n\
    PG_HOST_READY=0 \n\
    RETRY_COUNT=0 \n\
    while [ $PG_HOST_READY -eq 0 ] && [ $RETRY_COUNT -lt 5 ]; do \n\
      nc -z $PG_H $PG_P \n\
      if [ $? -eq 0 ]; then \n\
        echo "PostgreSQL at $PG_H:$PG_P is ready!" \n\
        PG_HOST_READY=1 \n\
      else \n\
        echo "Waiting for PostgreSQL at $PG_H:$PG_P... (attempt $RETRY_COUNT)" \n\
        RETRY_COUNT=$((RETRY_COUNT+1)) \n\
        sleep 3 \n\
      fi \n\
    done \n\
    \n\
    if [ $PG_HOST_READY -eq 0 ]; then \n\
      echo "WARNING: PostgreSQL at $PG_H:$PG_P is not responding!" \n\
    fi \n\
  done \n\
else \n\
  # Проверка по старой схеме, если используется только один инстанс \n\
  PG_READY=0 \n\
  while [ $PG_READY -eq 0 ]; do \n\
    nc -z $PG_HOST $PG_PORT \n\
    if [ $? -eq 0 ]; then \n\
      echo "PostgreSQL is ready!" \n\
      PG_READY=1 \n\
    else \n\
      echo "Waiting for PostgreSQL..." \n\
      sleep 2 \n\
    fi \n\
  done \n\
fi \n\
\n\
# Вычисление количества воркеров - по умолчанию количество ядер CPU * 2 + 1 \n\
DEFAULT_WORKERS=$(($(nproc) * 2 + 1)) \n\
WORKERS=${WORKERS:-$DEFAULT_WORKERS} \n\
\n\
echo "Starting application with $WORKERS workers..." \n\
echo "PostgreSQL sharding configuration: $PG_SHARD_COUNT shards" \n\
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers $WORKERS --http httptools --loop uvloop \n\
' > /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh

# Установка переменных окружения
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
# Оптимизация Python для производительности
ENV PYTHONOPTIMIZE=1

# Порт, который будет использоваться
EXPOSE 8000

# Запуск приложения через скрипт-обертку
CMD ["/app/entrypoint.sh"] 