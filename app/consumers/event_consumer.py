import asyncio
import json
from typing import Dict, Any, Optional, List
from datetime import datetime
import time

from loguru import logger

from app.deduplicator import Deduplicator
from app.services.kafka_service import KafkaService
from app.services.redis_service import RedisService
from app.services.postgres_service import PostgresService
from app.models import DedupResult


class EventConsumer:
    """Консьюмер для обработки событий из Kafka."""
    
    def __init__(self, kafka_service: KafkaService, redis_service: RedisService, postgres_service: PostgresService):
        """Инициализация консьюмера."""
        self.kafka = kafka_service
        self.redis = redis_service
        self.postgres = postgres_service
        self.deduplicator = None
        self.running = False
        self.processed_count = 0
        self.duplicate_count = 0
        self.error_count = 0
        self.saved_count = 0
        self.last_stats_time = datetime.utcnow()
        
        self.event_buffer = []
        self.buffer_lock = asyncio.Lock()
        self.buffer_size = 250
        self.buffer_flush_interval = 1.0
    
    async def start(self, 
                    topic: str = "product-events", 
                    group_id: str = "event-deduplicator",
                    bootstrap_servers: str = "localhost:9092",
                    max_concurrency: int = 500):
        """Запускает консьюмер для обработки событий."""
        if hasattr(self.redis, 'redis_pool') and self.redis.redis_pool:
            self.deduplicator = Deduplicator(
                self.redis.redis, 
                redis_pool=self.redis.redis_pool, 
                shard_count=self.redis.shard_count
            )
            logger.info(f"Using sharded deduplicator with {self.redis.shard_count} shards")
        else:
            self.deduplicator = Deduplicator(self.redis.redis)
            logger.info("Using single-instance deduplicator")
        
        await self.kafka.connect_consumer(topic, group_id, bootstrap_servers)
        
        self.running = True
        logger.info(f"Starting event consumer for topic {topic} with max concurrency {max_concurrency}")
        
        stats_task = asyncio.create_task(self._log_stats())
        buffer_task = asyncio.create_task(self._flush_buffer_periodically())
        
        try:
            await self.kafka.consume_messages(
                topic,
                self._process_event,
                max_concurrency
            )
        finally:
            self.running = False
            await stats_task
            await buffer_task
            
            await self._flush_buffer()
    
    async def stop(self):
        """Останавливает консьюмер."""
        self.running = False
        self.kafka.stop_consuming()
        logger.info("Event consumer stopping")
    
    async def _process_event(self, event: Dict[str, Any]):
        """Обрабатывает событие из Kafka."""
        try:
            self.processed_count += 1
            
            if '_dedup_hash' in event:
                event_hash = event.pop('_dedup_hash')
            else:
                start_time = time.time()
                event_hash = self.deduplicator.calculate_hash(event)
            
            dedup_result = await self.deduplicator.is_duplicate(event)
            
            if dedup_result.is_duplicate:
                self.duplicate_count += 1
                if self.duplicate_count % 1000 == 0:
                    logger.debug(f"Duplicate event detected: {event_hash}")
            else:
                async with self.buffer_lock:
                    self.event_buffer.append((event, event_hash))
                    
                    if len(self.event_buffer) >= self.buffer_size:
                        await self._flush_buffer()
                        
        except Exception as e:
            self.error_count += 1
            logger.error(f"Error processing event: {str(e)}")
    
    async def _flush_buffer(self):
        """Сбрасывает буфер событий и сохраняет их в PostgreSQL."""
        async with self.buffer_lock:
            if not self.event_buffer:
                return
                
            events_to_save = self.event_buffer.copy()
            self.event_buffer.clear()
        
        try:
            events = [event for event, _ in events_to_save]
            event_hashes = [event_hash for _, event_hash in events_to_save]
            
            success = await self.postgres.save_events_batch(events, event_hashes)
            if success:
                self.saved_count += len(events_to_save)
                logger.debug(f"Batch saved {len(events_to_save)} events to PostgreSQL")
            else:
                logger.warning("Batch save failed, trying individual saves")
                for event, event_hash in events_to_save:
                    success = await self.postgres.save_event(event, event_hash)
                    if success:
                        self.saved_count += 1
        except Exception as e:
            logger.error(f"Error batch saving events: {str(e)}")
    
    async def _flush_buffer_periodically(self):
        """Периодически сбрасывает буфер событий."""
        while self.running:
            await asyncio.sleep(self.buffer_flush_interval)
            await self._flush_buffer()
    
    async def _log_stats(self, interval: int = 10):
        """Периодически выводит статистику обработки."""
        while self.running:
            await asyncio.sleep(interval)
            
            now = datetime.utcnow()
            delta = (now - self.last_stats_time).total_seconds()
            
            if delta < interval:
                continue
            
            rps = self.processed_count / delta if delta > 0 else 0
            
            logger.info(
                f"Stats: Processed {self.processed_count} events ({rps:.1f} RPS), "
                f"Duplicates {self.duplicate_count}, "
                f"Saved to PostgreSQL {self.saved_count}, "
                f"Errors {self.error_count}, "
                f"Buffer size: {len(self.event_buffer)} in the last {delta:.1f} seconds"
            )
            
            self.processed_count = 0
            self.duplicate_count = 0
            self.saved_count = 0
            self.error_count = 0
            self.last_stats_time = now 