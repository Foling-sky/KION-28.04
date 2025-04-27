#!/bin/bash

# Скрипт для горизонтального масштабирования сервисов
# Использование: ./scale_cluster.sh [service] [replicas]
# Например: ./scale_cluster.sh deduplicator 4

SERVICE=$1
REPLICAS=$2

if [ -z "$SERVICE" ] || [ -z "$REPLICAS" ]; then
    echo "Использование: ./scale_cluster.sh [service] [replicas]"
    echo "Доступные сервисы: deduplicator"
    exit 1
fi

if [ "$SERVICE" != "deduplicator" ]; then
    echo "Ошибка: сервис $SERVICE не поддерживает масштабирование"
    echo "Доступные сервисы: deduplicator"
    exit 1
fi

if ! [[ "$REPLICAS" =~ ^[0-9]+$ ]]; then
    echo "Ошибка: количество реплик должно быть целым числом"
    exit 1
fi

echo "Масштабирование сервиса $SERVICE до $REPLICAS экземпляров..."
docker-compose up -d --scale $SERVICE=$REPLICAS

echo "Проверка статуса кластера..."
docker-compose ps

echo "Готово! Сервис $SERVICE масштабирован до $REPLICAS экземпляров."
echo "Показатели использования ресурсов:"
docker stats --no-stream 