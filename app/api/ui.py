from fastapi import APIRouter, Depends, Request, Query, HTTPException, status
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
import os
from typing import Dict, Any, List, Optional
from pathlib import Path
import json
from datetime import datetime

from app.services.postgres_service import PostgresService
from app.services.redis_service import RedisService

ENABLE_DATABASE_UI = os.getenv("ENABLE_DATABASE_UI", "true").lower() == "true"

router = APIRouter()

templates_dir = Path(__file__).parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))
templates_dir.mkdir(exist_ok=True)

def get_postgres_service():
    from app.api.events import get_postgres_service as get_pg
    return get_pg()

def get_redis_service():
    from app.api.events import get_redis_service as get_redis
    return get_redis()

@router.get("/database", response_class=HTMLResponse)
async def database_ui(
    request: Request,
    postgres: PostgresService = Depends(get_postgres_service),
    limit: int = Query(50, description="Количество записей на странице", ge=1, le=1000),
    offset: int = Query(0, description="Смещение для пагинации", ge=0),
    event_hash: Optional[str] = None
):
    if not ENABLE_DATABASE_UI:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database UI is disabled. Set ENABLE_DATABASE_UI=true to enable it."
        )
    
    if event_hash:
        event = await postgres.get_event_by_hash(event_hash)
        if not event:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Event with hash {event_hash} not found"
            )
        
        if 'full_event' in event and event['full_event']:
            if isinstance(event['full_event'], str):
                try:
                    event['full_event'] = json.loads(event['full_event'])
                except json.JSONDecodeError:
                    pass
            
            event['full_event_json'] = json.dumps(event['full_event'], indent=2, ensure_ascii=False)
        else:
            event['full_event'] = {}
            event['full_event_json'] = '{}'
        
        shard_id = postgres._get_shard_index(event_hash)
        event['shard_id'] = shard_id
        
        return templates.TemplateResponse("event_detail.html", {"request": request, "event": event})
    else:
        all_events = await postgres.get_all_events()
        total = len(all_events)
        
        shard_stats = {}
        
        for event in all_events:
            shard_id = postgres._get_shard_index(event['event_hash'])
            event['shard_id'] = shard_id
            
            if shard_id not in shard_stats:
                shard_stats[shard_id] = 0
            shard_stats[shard_id] += 1
        
        start_idx = offset
        end_idx = min(offset + limit, total)
        events = all_events[start_idx:end_idx] if start_idx < total else []
        
        pages = (total + limit - 1) // limit if limit > 0 else 1
        current_page = offset // limit + 1 if limit > 0 else 1
        
        now = datetime.utcnow().isoformat()
        
        return templates.TemplateResponse(
            "event_list.html",
            {
                "request": request,
                "events": events,
                "total": total,
                "limit": limit,
                "offset": offset,
                "pages": pages,
                "current_page": current_page,
                "max": max,
                "min": min,
                "shard_count": postgres.shard_count,
                "shard_stats": shard_stats,
                "now": now
            }
        )

@router.get("/stats", response_class=HTMLResponse)
async def stats_ui(
    request: Request,
    redis: RedisService = Depends(get_redis_service),
    postgres: PostgresService = Depends(get_postgres_service)
):
    if not ENABLE_DATABASE_UI:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Database UI is disabled. Set ENABLE_DATABASE_UI=true to enable it."
        )
    
    from app.api.events import get_stats
    stats = await get_stats(redis=redis, postgres=postgres)
    
    return templates.TemplateResponse("stats.html", {"request": request, "stats": stats})