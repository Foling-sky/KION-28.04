import os
import asyncio
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn
from loguru import logger
import time

from app.api import events
from app.api import ui
from app.services.redis_service import RedisService
from app.services.kafka_service import KafkaService
from app.services.postgres_service import PostgresService
from app.consumers.event_consumer import EventConsumer


logger.add("logs/app.log", rotation="10 MB", level="INFO", backtrace=True, diagnose=True)

app = FastAPI(
    title="KION Event Deduplicator",
    description="Сервис дедупликации продуктовых событий для KION",
    version="1.0.0"
)

# Настройка статических файлов
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)
app.mount("/static", StaticFiles(directory=static_dir), name="static")

# Корневая страница
root_dir = Path(__file__).parent.parent
index_file = root_dir / "index.html"

@app.get("/", response_class=HTMLResponse)
async def root():
    """Корневая страница."""
    if index_file.exists():
        return HTMLResponse(content=index_file.read_text(), status_code=200)
    else:
        return HTMLResponse(content="<html><body><h1>KION Event Deduplicator</h1><p>Главная страница не найдена. Перейдите к <a href='/api/ui/stats'>статистике</a> или <a href='/api/ui/database'>базе данных</a>.</p></body></html>")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

redis_service = RedisService()
kafka_service = KafkaService()
postgres_service = PostgresService()
event_consumer = None


@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    """Добавляет заголовок с временем обработки запроса."""
    start_time = time.time()
    response = await call_next(request)
    process_time = time.time() - start_time
    response.headers["X-Process-Time"] = str(process_time)
    return response


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Глобальный обработчик исключений."""
    logger.error(f"Unhandled exception: {str(exc)}")
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error", "message": str(exc)}
    )


app.include_router(events.router, prefix="/api", tags=["events"])
app.include_router(ui.router, prefix="/api/ui", tags=["ui"])

if ui.ENABLE_DATABASE_UI:
    logger.info("Database UI is enabled. Access it at /api/ui/database")
else:
    logger.info("Database UI is disabled. Set ENABLE_DATABASE_UI=true to enable it.")


async def create_kafka_topic(bootstrap_servers, topic_name, num_partitions=1, replication_factor=1):
    """Создает топик в Kafka, если он не существует."""
    from kafka.admin import KafkaAdminClient, NewTopic
    from kafka.errors import TopicAlreadyExistsError
    
    try:
        admin_client = KafkaAdminClient(
            bootstrap_servers=bootstrap_servers,
            client_id='kion-admin'
        )
        
        topic_list = [
            NewTopic(
                name=topic_name,
                num_partitions=num_partitions,
                replication_factor=replication_factor
            )
        ]
        
        logger.info(f"Attempting to create Kafka topic: {topic_name}")
        admin_client.create_topics(new_topics=topic_list, validate_only=False)
        logger.info(f"Successfully created Kafka topic: {topic_name}")
    except TopicAlreadyExistsError:
        logger.info(f"Kafka topic {topic_name} already exists")
    except Exception as e:
        logger.warning(f"Error creating Kafka topic {topic_name}: {str(e)}")
    finally:
        try:
            admin_client.close()
        except Exception:
            pass


@app.on_event("startup")
async def startup_event():
    """Инициализация при запуске приложения."""
    global event_consumer
    
    redis_host = os.getenv("REDIS_HOST", "localhost")
    redis_port = int(os.getenv("REDIS_PORT", "6379"))
    redis_db = int(os.getenv("REDIS_DB", "0"))
    redis_password = os.getenv("REDIS_PASSWORD")
    redis_shard_count = int(os.getenv("REDIS_SHARD_COUNT", "4"))
    redis_hosts = os.getenv("REDIS_HOSTS", "")
    
    kafka_bootstrap_servers = os.getenv("KAFKA_BOOTSTRAP_SERVERS", "localhost:9092")
    kafka_topic = os.getenv("KAFKA_TOPIC", "product-events")
    kafka_group_id = os.getenv("KAFKA_GROUP_ID", "event-deduplicator")
    
    pg_host = os.getenv("PG_HOST", "localhost")
    pg_port = int(os.getenv("PG_PORT", "5432"))
    pg_user = os.getenv("PG_USER", "postgres")
    pg_password = os.getenv("PG_PASSWORD", "postgres")
    pg_database = os.getenv("PG_DATABASE", "postgres")
    pg_hosts = os.getenv("PG_HOSTS", "")
    pg_shard_count = int(os.getenv("PG_SHARD_COUNT", "1"))
    
    redis_host_list = None
    if redis_hosts:
        redis_host_list = [h.strip() for h in redis_hosts.split(",") if h.strip()]
        logger.info(f"Found {len(redis_host_list)} Redis hosts: {redis_host_list}")
    
    try:
        await redis_service.connect(
            host=redis_host,
            port=redis_port,
            db=redis_db,
            password=redis_password,
            ttl_days=7,
            shard_count=redis_shard_count,
            hosts=redis_host_list
        )
        
        events.redis_service = redis_service
        logger.info(f"Redis service initialized with {redis_service.get_connection_count()} connections")
    except Exception as e:
        logger.error(f"Failed to connect to Redis: {str(e)}")
        raise
    
    try:
        pg_host_list = None
        if pg_hosts:
            pg_host_list = [h.strip() for h in pg_hosts.split(",") if h.strip()]
            logger.info(f"Found {len(pg_host_list)} PostgreSQL hosts: {pg_host_list}")
        
        await postgres_service.connect(
            host=pg_host,
            port=pg_port,
            user=pg_user,
            password=pg_password,
            database=pg_database,
            hosts=pg_host_list,
            shard_count=pg_shard_count
        )
        
        events.postgres_service = postgres_service
        logger.info(f"PostgreSQL service initialized with {len(postgres_service.pools or [])} connections")
    except Exception as e:
        logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
        raise
    
    try:
        asyncio.create_task(create_kafka_topic(
            kafka_bootstrap_servers,
            kafka_topic
        ))
    except Exception as e:
        logger.warning(f"Could not create Kafka topic: {str(e)}")
    
    try:
        await kafka_service.connect_producer(
            bootstrap_servers=kafka_bootstrap_servers
        )
        
        events.kafka_service = kafka_service
    except Exception as e:
        logger.error(f"Failed to connect to Kafka: {str(e)}")
        return
    
    try:
        event_consumer = EventConsumer(kafka_service, redis_service, postgres_service)
        
        max_concurrency = int(os.getenv("MAX_CONCURRENCY", "500"))
        
        asyncio.create_task(
            event_consumer.start(
                topic=kafka_topic,
                group_id=kafka_group_id,
                bootstrap_servers=kafka_bootstrap_servers,
                max_concurrency=max_concurrency
            )
        )
    except Exception as e:
        logger.error(f"Failed to start event consumer: {str(e)}")


@app.on_event("shutdown")
async def shutdown_event():
    """Очистка ресурсов при завершении приложения."""
    if event_consumer:
        await event_consumer.stop()
    
    await kafka_service.disconnect_producer()
    await redis_service.disconnect()
    await postgres_service.disconnect()
    
    logger.info("Application shutdown complete")


if __name__ == "__main__":
    uvicorn.run(
        "app.main:app",
        host=os.getenv("HOST", "0.0.0.0"),
        port=int(os.getenv("PORT", "8000")),
        reload=os.getenv("DEBUG", "false").lower() == "true",
        workers=int(os.getenv("WORKERS", "4")),
        http="httptools"
    ) 