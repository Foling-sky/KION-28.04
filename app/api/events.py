from fastapi import APIRouter, HTTPException, Depends, BackgroundTasks, status, Query
from fastapi.responses import JSONResponse

from app.models import EventModel, EventProcessingResponse
from app.services.kafka_service import KafkaService
from app.deduplicator import Deduplicator
from app.services.redis_service import RedisService
from app.services.postgres_service import PostgresService
from loguru import logger
import time
from typing import Dict, Any, List
import random
from datetime import datetime

router = APIRouter()

kafka_service = None
redis_service = None
postgres_service = None

def get_kafka_service():
    global kafka_service
    if kafka_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Kafka service not initialized"
        )
    return kafka_service

def get_redis_service():
    global redis_service
    if redis_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Redis service not initialized"
        )
    return redis_service

def get_postgres_service():
    global postgres_service
    if postgres_service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PostgreSQL service not initialized"
        )
    return postgres_service

def get_deduplicator(redis=Depends(get_redis_service)):
    if hasattr(redis, 'redis_pool') and redis.redis_pool:
        return Deduplicator(redis.redis, redis_pool=redis.redis_pool, shard_count=redis.shard_count)
    else:
        return Deduplicator(redis.redis)

@router.post("/event", response_model=EventProcessingResponse)
async def process_event(
    event: EventModel,
    background_tasks: BackgroundTasks,
    kafka: KafkaService = Depends(get_kafka_service),
    deduplicator: Deduplicator = Depends(get_deduplicator),
    postgres: PostgresService = Depends(get_postgres_service)
):
    start_time = time.time()
    
    try:
        event_dict = {
            'client_id': event.client_id,
            'event_datetime': event.event_datetime,
            'event_name': event.event_name,
            'product_id': event.product_id,
            'sid': event.sid,
            'r': event.r
        }
        
        event_hash = deduplicator.calculate_hash(event_dict)
        dedup_result = await deduplicator.is_duplicate(event_dict)
        
        response = EventProcessingResponse(
            status="rejected" if dedup_result.is_duplicate else "accepted",
            is_duplicate=dedup_result.is_duplicate,
            message="Event is a duplicate" if dedup_result.is_duplicate else "Event accepted for processing and storage",
            event_hash=dedup_result.event_hash
        )
        
        if not dedup_result.is_duplicate:
            if hasattr(event, 'model_dump'):
                full_event_dict = event.model_dump(exclude_unset=True)
            else:
                full_event_dict = event.dict(exclude_unset=True)
                
            full_event_dict['_dedup_hash'] = dedup_result.event_hash
                
            background_tasks.add_task(kafka.send_message, "product-events", full_event_dict)
            background_tasks.add_task(postgres.save_event, full_event_dict, dedup_result.event_hash)
        
        processing_time = (time.time() - start_time) * 1000
        
        if random.random() < 0.01:
            if dedup_result.is_duplicate:
                logger.info(f"Duplicate event rejected in {processing_time:.2f}ms, hash: {dedup_result.event_hash}")
            else:
                logger.info(f"Event processed and sent to Kafka in {processing_time:.2f}ms, hash: {dedup_result.event_hash}")
            
        return response
    
    except Exception as e:
        logger.error(f"Error processing event: {str(e)}")
        
        return EventProcessingResponse(
            status="error",
            is_duplicate=False,
            message=f"Error processing event: {str(e)}",
            event_hash=None
        )

@router.get("/health")
async def health_check(
    redis=Depends(get_redis_service),
    kafka=Depends(get_kafka_service),
    postgres=Depends(get_postgres_service)
):
    redis_ok = await redis.redis.ping() if redis.redis else False
    postgres_ok = postgres.pool is not None
    kafka_ok = kafka.producer is not None
    
    all_ok = redis_ok and postgres_ok and kafka_ok
    
    return {
        "status": "ok" if all_ok else "degraded",
        "service": "deduplicator",
        "components": {
            "redis": "ok" if redis_ok else "error",
            "postgres": "ok" if postgres_ok else "error",
            "kafka": "ok" if kafka_ok else "error"
        }
    }

@router.get("/events", response_model=Dict[str, Any])
async def get_events(
    limit: int = Query(50, description="Количество возвращаемых событий", ge=1, le=1000),
    offset: int = Query(0, description="Смещение для пагинации", ge=0),
    postgres: PostgresService = Depends(get_postgres_service)
):
    try:
        total = await postgres.count_events()
        events = await postgres.get_events(limit=limit, offset=offset)
        
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": events
        }
    except Exception as e:
        logger.error(f"Error getting events: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting events: {str(e)}"
        )

@router.get("/export-all", response_model=Dict[str, Any])
async def export_all_events(
    postgres: PostgresService = Depends(get_postgres_service)
):
    try:
        total = await postgres.count_events()
        
        # Устанавливаем предел на количество событий для экспорта
        max_export_limit = 10000
        if total > max_export_limit:
            # Если общее число событий превышает лимит, выбираем только последние max_export_limit записей
            events = await postgres.get_events(limit=max_export_limit, offset=0)
            logger.warning(f"Export limited to {max_export_limit} events out of {total}")
        else:
            # Используем get_all_events для получения данных со ВСЕХ шардов
            events = await postgres.get_all_events()
        
        return {
            "total": total, 
            "exported": len(events),
            "data": events
        }
    except Exception as e:
        logger.error(f"Error exporting all events: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error exporting all events: {str(e)}"
        )

@router.get("/events/{event_hash}", response_model=Dict[str, Any])
async def get_event_by_hash(
    event_hash: str,
    postgres: PostgresService = Depends(get_postgres_service)
):
    try:
        event = await postgres.get_event_by_hash(event_hash)
        
        if event is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event with hash {event_hash} not found"
            )
        
        return event
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error getting event by hash: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting event by hash: {str(e)}"
        )

@router.get("/export", response_model=Dict[str, Any])
async def export_events(
    limit: int = Query(50, description="Количество возвращаемых событий", ge=1, le=1000),
    offset: int = Query(0, description="Смещение для пагинации", ge=0),
    postgres: PostgresService = Depends(get_postgres_service)
):
    try:
        total = await postgres.count_events()
        events = await postgres.get_events(limit=limit, offset=offset)
        
        return {
            "total": total,
            "limit": limit,
            "offset": offset,
            "data": events
        }
    except Exception as e:
        logger.error(f"Error exporting events: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error exporting events: {str(e)}"
        )

@router.get("/stats", response_model=Dict[str, Any])
async def get_stats(
    redis=Depends(get_redis_service),
    postgres=Depends(get_postgres_service)
):
    """Возвращает статистику работы сервиса."""
    try:
        redis_info = {}
        if redis.redis:
            prefix = "event_dedup:"
            keys_count = 0
            
            for i in range(redis.shard_count if redis.shard_count else 1):
                client = redis.redis_pool[i] if redis.redis_pool else redis.redis
                # Получаем количество ключей с префиксом "event_dedup:"
                keys = await client.keys(f"{prefix}*")
                keys_count += len(keys)
            
            redis_info = {
                "dedup_keys_count": keys_count,
                "shard_count": redis.shard_count if redis.shard_count else 1,
            }
        
        pg_stats = {}
        shard_stats = {}
        
        total_events = await postgres.count_events()
        
        for i in range(postgres.shard_count):
            if i < len(postgres.pools):
                try:
                    count = await postgres._count_events_on_shard(postgres.pools[i])
                    shard_stats[f"shard_{i}"] = count
                except Exception:
                    shard_stats[f"shard_{i}"] = 0
        
        pg_stats = {
            "total_events": total_events,
            "shard_count": postgres.shard_count,
            "shards": shard_stats
        }
        
        return {
            "service": "kion-deduplicator",
            "redis": redis_info,
            "postgres": pg_stats,
            "timestamp": datetime.utcnow().isoformat()
        }
    except Exception as e:
        logger.error(f"Error getting stats: {str(e)}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Error getting stats: {str(e)}"
        )