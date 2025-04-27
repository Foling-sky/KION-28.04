#!/bin/bash

# Скрипт для анализа нагрузки и статистики PostgreSQL по шардам
# Использование: ./pg_stats.sh

echo "==============================================="
echo "  Статистика шардирования PostgreSQL в KION"
echo "==============================================="

# Получаем количество записей в каждом шарде
get_shard_count() {
    local shard=$1
    local port=$2
    local count=$(docker exec -it ${shard} psql -U kion -d events_db -c "SELECT COUNT(*) FROM events;" -t | tr -d '[:space:]')
    echo "Шард ${shard}: ${count} записей"
}

# Получаем статистику запросов
get_query_stats() {
    local shard=$1
    local port=$2
    echo "Топ 5 самых долгих запросов в ${shard}:"
    docker exec -it ${shard} psql -U kion -d events_db -c "
    SELECT 
        round(total_exec_time::numeric, 2) as total_time_ms,
        calls, 
        round(mean_exec_time::numeric, 2) as mean_time_ms, 
        round(stddev_exec_time::numeric, 2) as stddev_ms,
        substring(query, 1, 100) as query
    FROM pg_stat_statements 
    ORDER BY total_exec_time DESC 
    LIMIT 5;" 
}

# Получаем статистику использования индексов
get_index_stats() {
    local shard=$1
    local port=$2
    echo "Статистика использования индексов в ${shard}:"
    docker exec -it ${shard} psql -U kion -d events_db -c "
    SELECT 
        idstat.relname as table_name, 
        indexrelname as index_name, 
        idstat.idx_scan as times_used, 
        pg_size_pretty(pg_relation_size(quote_ident(idstat.relname)::text)) as table_size, 
        pg_size_pretty(pg_relation_size(indexrelname::text)) as index_size
    FROM 
        pg_stat_user_indexes AS idstat 
    JOIN 
        pg_indexes ON indexrelname = indexname 
    WHERE 
        indexdef !~ 'unique'
    ORDER BY 
        idstat.idx_scan DESC, 
        pg_relation_size(quote_ident(idstat.relname)::text) DESC
    LIMIT 5;" 
}

# Список шардов и портов
shards=("postgres1" "postgres2" "postgres3")
ports=("5432" "5433" "5434")

echo "== Количество записей по шардам =="
for i in "${!shards[@]}"; do
    get_shard_count ${shards[$i]} ${ports[$i]}
done

echo ""
echo "== Включаем статистику запросов =="
for i in "${!shards[@]}"; do
    docker exec -it ${shards[$i]} psql -U kion -d events_db -c "CREATE EXTENSION IF NOT EXISTS pg_stat_statements;" 
    docker exec -it ${shards[$i]} psql -U kion -d events_db -c "SELECT pg_stat_statements_reset();" > /dev/null 2>&1
done

echo ""
echo "== Запускаем тестовую нагрузку на 10 секунд =="
echo "Подождите..."
sleep 10  # В реальной системе здесь можно запустить нагрузочное тестирование

echo ""
echo "== Статистика запросов =="
for i in "${!shards[@]}"; do
    get_query_stats ${shards[$i]} ${ports[$i]}
    echo ""
done

echo "== Статистика использования индексов =="
for i in "${!shards[@]}"; do
    get_index_stats ${shards[$i]} ${ports[$i]}
    echo ""
done

echo "==============================================="
echo "  Проверка производительности завершена"
echo "===============================================" 