import os
from typing import Optional, Any, Dict, List
import redis.asyncio as redis
from loguru import logger


class RedisService:
    """Сервис для работы с Redis."""
    
    def __init__(self):
        self.redis = None
        self.redis_pool = []
        self.ttl_seconds = 7 * 24 * 60 * 60
        self.key_prefix = "event_dedup:"
        self.shard_count = 1
    
    async def connect(self, 
                     host: str = "localhost", 
                     port: int = 6379, 
                     db: int = 0, 
                     password: Optional[str] = None,
                     ttl_days: int = 7,
                     shard_count: int = 1,
                     hosts: Optional[List[str]] = None) -> None:
        """Устанавливает соединение с Redis."""
        self.shard_count = shard_count

        try:
            if hosts and len(hosts) > 0:
                logger.info(f"Connecting to {len(hosts)} Redis instances for sharding")
                for host_info in hosts:
                    parts = host_info.split(":")
                    shard_host = parts[0]
                    shard_port = int(parts[1]) if len(parts) > 1 else port
                    
                    client = redis.Redis(
                        host=shard_host,
                        port=shard_port,
                        db=db,
                        password=password,
                        decode_responses=False
                    )
                    await client.ping()
                    self.redis_pool.append(client)
                    logger.info(f"Connected to Redis shard at {shard_host}:{shard_port}/{db}")
                
                self.redis = self.redis_pool[0]
                self.shard_count = len(self.redis_pool)
            else:
                self.redis = redis.Redis(
                    host=host,
                    port=port,
                    db=db,
                    password=password,
                    decode_responses=False
                )
                
                await self.redis.ping()
                logger.info(f"Connected to Redis at {host}:{port}/{db}")
                
                if shard_count > 1:
                    logger.info(f"Initializing {shard_count} Redis connections for sharding on same host")
                    self.redis_pool = [self.redis]
                    
                    for i in range(1, shard_count):
                        client = redis.Redis(
                            host=host,
                            port=port,
                            db=db,
                            password=password,
                            decode_responses=False
                        )
                        await client.ping()
                        self.redis_pool.append(client)
                else:
                    self.redis_pool = [self.redis]
                    self.shard_count = 1
            
            self.ttl_seconds = ttl_days * 24 * 60 * 60
            logger.info(f"Redis service initialized with {self.shard_count} shards")
            
        except Exception as e:
            logger.error(f"Failed to connect to Redis: {str(e)}")
            raise
    
    async def disconnect(self) -> None:
        """Закрывает соединение с Redis."""
        for client in self.redis_pool:
            await client.close()
        
        if self.redis and self.redis not in self.redis_pool:
            await self.redis.close()
            
        logger.info("All Redis connections closed")
    
    async def set(self, key: str, value: Any, ex: Optional[int] = None) -> bool:
        """Устанавливает значение для ключа в Redis."""
        if not self.redis:
            logger.error("Redis connection not established")
            return False
        
        try:
            if ex is None:
                ex = self.ttl_seconds
            
            await self.redis.set(key, value, ex=ex)
            return True
        except Exception as e:
            logger.error(f"Redis set error: {str(e)}")
            return False
    
    async def get(self, key: str) -> Optional[bytes]:
        """Получает значение по ключу из Redis."""
        if not self.redis:
            logger.error("Redis connection not established")
            return None
        
        try:
            return await self.redis.get(key)
        except Exception as e:
            logger.error(f"Redis get error: {str(e)}")
            return None
    
    async def exists(self, key: str) -> bool:
        """Проверяет существование ключа в Redis."""
        if not self.redis:
            logger.error("Redis connection not established")
            return False
        
        try:
            return await self.redis.exists(key) > 0
        except Exception as e:
            logger.error(f"Redis exists error: {str(e)}")
            return False
    
    async def delete(self, key: str) -> bool:
        """Удаляет ключ из Redis."""
        if not self.redis:
            logger.error("Redis connection not established")
            return False
        
        try:
            return await self.redis.delete(key) > 0
        except Exception as e:
            logger.error(f"Redis delete error: {str(e)}")
            return False
    
    async def store_event_hash(self, event_hash: str, timestamp: str) -> bool:
        """Сохраняет хеш события в Redis с указанной временной меткой."""
        key = f"{self.key_prefix}{event_hash}"
        return await self.set(key, timestamp, ex=self.ttl_seconds)
    
    async def check_event_hash(self, event_hash: str) -> Optional[bytes]:
        """Проверяет наличие хеша события в Redis."""
        key = f"{self.key_prefix}{event_hash}"
        return await self.get(key)
    
    def pipeline(self):
        """Возвращает объект pipeline Redis для выполнения нескольких операций за один запрос."""
        if not self.redis:
            logger.error("Redis connection not established")
            raise RuntimeError("Redis connection not established")
            
        return self.redis.pipeline()
        
    def get_connection_count(self) -> int:
        """Возвращает количество доступных соединений Redis."""
        return len(self.redis_pool) if self.redis_pool else 1 