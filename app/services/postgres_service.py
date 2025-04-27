import os
from typing import Optional, Dict, Any, List, Tuple
import asyncpg
import json
from loguru import logger
from datetime import datetime
import asyncio


class PostgresService:
    """Сервис для работы с PostgreSQL."""
    
    def __init__(self):
        self.pool = None
        self.pools = None
        self.shard_count = 1
        self.schema_created = False
    
    async def connect(self, host: str = "localhost", port: int = 5432, user: str = "postgres",
                     password: str = "postgres", database: str = "postgres",
                     hosts: List[str] = None, shard_count: int = 1) -> None:
        """Устанавливает соединение с PostgreSQL."""
        self.shard_count = shard_count
        self.pools = []
        
        try:
            if hosts:
                logger.info(f"Connecting to {len(hosts)} PostgreSQL instances for sharding")
                for host_str in hosts:
                    try:
                        if ":" in host_str:
                            pg_host, pg_port = host_str.split(":")
                            pg_port = int(pg_port)
                        else:
                            pg_host, pg_port = host_str, port
                            
                        pool = await asyncpg.create_pool(
                            host=pg_host,
                            port=pg_port,
                            user=user,
                            password=password,
                            database=database
                        )
                        
                        self.pools.append(pool)
                        logger.info(f"Connected to PostgreSQL at {pg_host}:{pg_port}/{database}")
                        
                        if not self.pool:
                            self.pool = pool
                    except Exception as e:
                        logger.error(f"Failed to connect to PostgreSQL at {host_str}: {str(e)}")
                        raise
                
                if not self.pools:
                    raise Exception("Failed to connect to any PostgreSQL instance")
            else:
                self.pool = await asyncpg.create_pool(
                    host=host,
                    port=port,
                    user=user,
                    password=password,
                    database=database
                )
                
                self.pools = [self.pool]
                logger.info(f"Connected to PostgreSQL at {host}:{port}/{database}")
            
            await self._create_schema_on_all_shards()
        except Exception as e:
            logger.error(f"Failed to connect to PostgreSQL: {str(e)}")
            raise
    
    async def disconnect(self) -> None:
        """Закрывает соединение с PostgreSQL."""
        if self.pools:
            for pool in self.pools:
                await pool.close()
            logger.info("All PostgreSQL connections closed")
        elif self.pool:
            await self.pool.close()
            logger.info("PostgreSQL connection closed")
    
    async def _create_schema_on_all_shards(self) -> None:
        """Создает схему базы данных на всех шардах."""
        if self.schema_created:
            return
        
        tasks = []
        for pool in self.pools:
            tasks.append(self._create_schema_on_shard(pool))
            
        await asyncio.gather(*tasks)
        self.schema_created = True
        
    async def _create_schema_on_shard(self, pool) -> None:
        """Создает схему базы данных на конкретном шарде."""
        try:
            async with pool.acquire() as conn:
                table_exists = await conn.fetchval(
                    "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'events')"
                )
                
                if not table_exists:
                    try:
                        await conn.execute('''
                            CREATE TABLE events (
                                id INTEGER PRIMARY KEY,
                                client_id TEXT NOT NULL,
                                event_datetime TEXT NOT NULL,
                                event_name TEXT NOT NULL, 
                                product_id TEXT NOT NULL,
                                sid TEXT NOT NULL,
                                r TEXT NOT NULL,
                                event_hash TEXT NOT NULL UNIQUE,
                                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                                full_event JSONB NOT NULL
                            )
                        ''')
                        
                        seq_exists = await conn.fetchval(
                            "SELECT EXISTS (SELECT FROM pg_sequences WHERE sequencename = 'events_id_seq')"
                        )
                        
                        if not seq_exists:
                            await conn.execute('CREATE SEQUENCE IF NOT EXISTS events_id_seq')
                            await conn.execute('ALTER TABLE events ALTER COLUMN id SET DEFAULT nextval(\'events_id_seq\')')
                        
                        await conn.execute('CREATE INDEX IF NOT EXISTS idx_events_event_hash ON events (event_hash)')
                        await conn.execute('CREATE INDEX IF NOT EXISTS idx_events_client_id ON events (client_id)')
                        await conn.execute('CREATE INDEX IF NOT EXISTS idx_events_event_name ON events (event_name)')
                        
                        logger.info("Created events table and indexes on shard")
                    except Exception as e:
                        table_check = await conn.fetchval(
                            "SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'events')"
                        )
                        
                        if table_check:
                            logger.info(f"Table was already created by another process: {str(e)}")
                        else:
                            logger.error(f"Failed to create table: {str(e)}")
                            raise
        except Exception as e:
            logger.error(f"Error creating schema on shard: {str(e)}")
            if "duplicate key value violates unique constraint" in str(e):
                logger.info("Schema appears to be created by another process, proceeding")
            else:
                raise
    
    def _get_shard_index(self, event_hash: str) -> int:
        """Определяет индекс шарда на основе хеша события."""
        if self.shard_count <= 1:
            return 0
        hash_int = int(event_hash[:8], 16)
        return hash_int % self.shard_count
    
    def _get_pool(self, event_hash: str = None):
        """Возвращает пул соединений для указанного хеша события или первый пул."""
        if not self.pools:
            return self.pool
        if event_hash is None:
            return self.pools[0]
        shard = self._get_shard_index(event_hash)
        return self.pools[min(shard, len(self.pools) - 1)]
    
    async def save_event(self, event: Dict[str, Any], event_hash: str) -> bool:
        """Сохраняет уникальное событие в PostgreSQL."""
        pool = self._get_pool(event_hash)
        if not pool:
            logger.error("PostgreSQL connection not established")
            return False
        
        try:
            async with pool.acquire() as conn:
                client_id = event.get('client_id', '')
                event_datetime = event.get('event_datetime', '')
                event_name = event.get('event_name', '')
                product_id = event.get('product_id', '')
                sid = event.get('sid', '')
                r = event.get('r', '')
                
                full_event_json = json.dumps(event)
                
                await conn.execute('''
                    INSERT INTO events (client_id, event_datetime, event_name, product_id, sid, r, event_hash, full_event)
                    VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                    ON CONFLICT (event_hash) DO NOTHING
                ''', client_id, event_datetime, event_name, product_id, sid, r, event_hash, full_event_json)
                
                return True
        except Exception as e:
            logger.error(f"Error saving event to PostgreSQL: {str(e)}")
            return False
    
    async def save_events_batch(self, events: List[Dict[str, Any]], event_hashes: List[str]) -> bool:
        """Сохраняет пакет уникальных событий в PostgreSQL."""
        if not self.pools or not events:
            return False
        if len(events) != len(event_hashes):
            logger.error("Количество событий и хешей не совпадает")
            return False
        
        sharded_events = {}
        for event, event_hash in zip(events, event_hashes):
            shard_index = self._get_shard_index(event_hash)
            if shard_index not in sharded_events:
                sharded_events[shard_index] = []
            sharded_events[shard_index].append((event, event_hash))
        
        tasks = []
        for shard_index, shard_events in sharded_events.items():
            pool_index = min(shard_index, len(self.pools) - 1)
            tasks.append(self._save_batch_to_shard(self.pools[pool_index], shard_events))
            
        results = await asyncio.gather(*tasks, return_exceptions=True)
        
        for result in results:
            if isinstance(result, Exception):
                logger.error(f"Error in batch save: {str(result)}")
                return False
            elif not result:
                return False
                
        return True
            
    async def _save_batch_to_shard(self, pool, events_with_hashes: List[Tuple[Dict[str, Any], str]]) -> bool:
        """Сохраняет пакет событий на конкретном шарде."""
        if not events_with_hashes:
            return True
            
        max_retries = 3
        retry_count = 0
            
        while retry_count < max_retries:
            try:
                async with pool.acquire() as conn:
                    async with conn.transaction():
                        values = []
                        
                        for event, event_hash in events_with_hashes:
                            client_id = event.get('client_id', '')
                            event_datetime = event.get('event_datetime', '')
                            event_name = event.get('event_name', '')
                            product_id = event.get('product_id', '')
                            sid = event.get('sid', '')
                            r = event.get('r', '')
                            
                            full_event_json = json.dumps(event)
                            values.append((client_id, event_datetime, event_name, product_id, sid, r, event_hash, full_event_json))
                        
                        if len(values) > 100:
                            copy_data = '\n'.join('\t'.join(str(field) if field is not None else '\\N' 
                                                           for field in row) for row in values)
                            
                            await conn.copy_to_table(
                                'events', 
                                columns=['client_id', 'event_datetime', 'event_name', 'product_id', 'sid', 'r', 'event_hash', 'full_event'],
                                data=copy_data,
                                format='csv'
                            )
                        else:
                            await conn.executemany('''
                                INSERT INTO events (client_id, event_datetime, event_name, product_id, sid, r, event_hash, full_event)
                                VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                                ON CONFLICT (event_hash) DO NOTHING
                            ''', values)
                        
                        return True
            except Exception as e:
                retry_count += 1
                logger.warning(f"Error batch saving events to shard (attempt {retry_count}/{max_retries}): {str(e)}")
                
                if retry_count < max_retries:
                    await asyncio.sleep(0.1 * retry_count)
                    continue
                else:
                    logger.error(f"Failed to batch save events to shard after {max_retries} attempts: {str(e)}")
                    return False
        
        return False
        
    async def get_event_by_hash(self, event_hash: str) -> Optional[Dict[str, Any]]:
        """Получает событие по его хешу."""
        pool = self._get_pool(event_hash)
        if not pool:
            logger.error("PostgreSQL connection not established")
            return None
        
        try:
            async with pool.acquire() as conn:
                row = await conn.fetchrow('SELECT * FROM events WHERE event_hash = $1', event_hash)
                
                if row is None:
                    return None
                
                result = dict(row)
                result['full_event'] = result['full_event']
                
                return result
        except Exception as e:
            logger.error(f"Error getting event from PostgreSQL: {str(e)}")
            return None
    
    async def get_events(self, limit: int = 100, offset: int = 0) -> List[Dict[str, Any]]:
        """Получает события из PostgreSQL с учетом пагинации."""
        if not self.pools:
            return []
            
        try:
            # Для больших запросов (более 1000 записей) применяем асинхронное выполнение
            if limit > 1000:
                # Делим запрос по шардам
                shard_limit = limit // len(self.pools)
                shard_offset = offset // len(self.pools)
                
                tasks = []
                for i, pool in enumerate(self.pools):
                    # Добавляем дополнительные записи на первый шард для компенсации остатка
                    extra = limit % len(self.pools) if i == 0 else 0
                    tasks.append(self._get_events_from_shard(
                        pool, 
                        shard_limit + extra, 
                        shard_offset + (offset % len(self.pools) if i == 0 else 0)
                    ))
                    
                results = await asyncio.gather(*tasks)
                
                all_events = []
                for events in results:
                    all_events.extend(events)
                    
                # Сортируем все события и применяем лимит/смещение
                all_events.sort(key=lambda x: x['id'], reverse=True)
                
                # Возвращаем запрошенное количество записей
                return all_events[:limit]
            else:
                # Для небольших запросов используем первый шард для простоты
                pool = self.pools[0]
                return await self._get_events_from_shard(pool, limit, offset)
        except Exception as e:
            logger.error(f"Error getting events from PostgreSQL: {str(e)}")
            return []
    
    async def count_events(self) -> int:
        """Получает общее количество событий в PostgreSQL."""
        if not self.pools:
            return 0
            
        try:
            tasks = []
            for pool in self.pools:
                tasks.append(self._count_events_on_shard(pool))
                
            counts = await asyncio.gather(*tasks)
            return sum(counts)
        except Exception as e:
            logger.error(f"Error counting events in PostgreSQL: {str(e)}")
            return 0
            
    async def _count_events_on_shard(self, pool) -> int:
        """Считает количество событий на конкретном шарде."""
        try:
            async with pool.acquire() as conn:
                count = await conn.fetchval('SELECT COUNT(*) FROM events')
                return count or 0
        except Exception as e:
            logger.error(f"Error counting events on shard: {str(e)}")
            return 0
    
    async def get_all_events(self) -> List[Dict[str, Any]]:
        """Получает все события из PostgreSQL со всех шардов."""
        if not self.pools:
            return []
            
        try:
            tasks = []
            for pool in self.pools:
                tasks.append(self._get_all_events_from_shard(pool))
                
            results = await asyncio.gather(*tasks)
            
            all_events = []
            for events in results:
                all_events.extend(events)
                
            all_events.sort(key=lambda x: x['created_at'], reverse=True)
            
            return all_events
        except Exception as e:
            logger.error(f"Error getting all events from PostgreSQL: {str(e)}")
            return []
            
    async def _get_all_events_from_shard(self, pool) -> List[Dict[str, Any]]:
        """Получает все события с конкретного шарда."""
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT * FROM events 
                    ORDER BY created_at DESC
                ''')
                
                results = []
                for row in rows:
                    event = dict(row)
                    event['full_event'] = json.loads(event['full_event'])
                    results.append(event)
                
                return results
        except Exception as e:
            logger.error(f"Error getting events from shard: {str(e)}")
            return []
    
    async def _get_events_from_shard(self, pool, limit: int, offset: int) -> List[Dict[str, Any]]:
        """Получает события с конкретного шарда с учетом пагинации."""
        try:
            async with pool.acquire() as conn:
                rows = await conn.fetch('''
                    SELECT * FROM events 
                    ORDER BY created_at DESC 
                    LIMIT $1 OFFSET $2
                ''', limit, offset)
                
                results = []
                for row in rows:
                    event = dict(row)
                    if event['full_event'] and isinstance(event['full_event'], str):
                        try:
                            event['full_event'] = json.loads(event['full_event'])
                        except json.JSONDecodeError:
                            # Если не удалось преобразовать в JSON, оставляем как строку
                            pass
                    results.append(event)
                
                return results
        except Exception as e:
            logger.error(f"Error getting events from shard: {str(e)}")
            return [] 