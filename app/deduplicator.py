import hashlib
import json
from datetime import datetime
from typing import Dict, Any

from loguru import logger
from app.models import DedupResult


class Deduplicator:
    def __init__(self, redis_client, ttl_days: int = 7, shard_count: int = 1, redis_pool=None):
        self.redis = redis_client
        self.redis_pool = redis_pool
        self.ttl_seconds = ttl_days * 24 * 60 * 60
        self.key_prefix = "event_dedup:"
        self.shard_count = shard_count

    def _extract_dedup_fields(self, event: Dict[str, Any]) -> Dict[str, Any]:
        dedup_fields = {
            'client_id': event.get('client_id', ''),
            'event_datetime': event.get('event_datetime', ''),
            'event_name': event.get('event_name', ''),
            'product_id': event.get('product_id', ''),
            'sid': event.get('sid', ''),
            'r': event.get('r', '')
        }
        
        for field, value in dedup_fields.items():
            if not value:
                logger.warning(f"Поле {field} отсутствует или пустое в событии")
        
        return dedup_fields

    def calculate_hash(self, event: Dict[str, Any]) -> str:
        dedup_fields = self._extract_dedup_fields(event)
        sorted_fields = dict(sorted(dedup_fields.items()))
        json_str = json.dumps(sorted_fields, sort_keys=True, ensure_ascii=False)
        return hashlib.sha256(json_str.encode('utf-8')).hexdigest()

    def _get_shard_index(self, event_hash: str) -> int:
        if self.shard_count <= 1:
            return 0
            
        hash_int = int(event_hash[:8], 16)
        return hash_int % self.shard_count

    def _get_redis_client(self, event_hash: str):
        if self.redis_pool is None:
            return self.redis
            
        shard = self._get_shard_index(event_hash)
        return self.redis_pool[shard]

    async def is_duplicate(self, event: Dict[str, Any]) -> DedupResult:
        event_hash = self.calculate_hash(event)
        redis_key = f"{self.key_prefix}{event_hash}"
        redis_client = self._get_redis_client(event_hash)
        
        pipe = redis_client.pipeline()
        pipe.exists(redis_key)
        pipe.get(redis_key)
        
        exists, original_timestamp = await pipe.execute()
        
        if exists:
            return DedupResult(
                is_duplicate=True,
                event_hash=event_hash,
                original_timestamp=original_timestamp.decode('utf-8') if original_timestamp else None
            )
        else:
            now = datetime.utcnow().isoformat()
            await redis_client.set(redis_key, now, ex=self.ttl_seconds)
            
            return DedupResult(
                is_duplicate=False,
                event_hash=event_hash,
                original_timestamp=None
            )

    async def store_bloom_filter(self, event_hash: str) -> None:
        pass

    async def check_bloom_filter(self, event_hash: str) -> bool:
        return False