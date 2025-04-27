from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any, Union
from datetime import datetime


class EventModel(BaseModel):
    """Модель продуктового события для дедупликации."""
    
    client_id: str = Field(description="Идентификатор клиента")
    event_datetime: str = Field(description="Время события в ISO формате")
    event_name: str = Field(description="Тип события")
    product_id: str = Field(description="Идентификатор продукта")
    sid: str = Field(description="Идентификатор сессии")
    r: str = Field(description="Request ID")
    
    platform: Optional[str] = Field(default="", description="Платформа")
    profile_age: Optional[int] = Field(default=-1, description="Возраст профиля")
    user_agent: Optional[str] = Field(default="", description="User Agent")
    screen: Optional[str] = Field(default="", description="Экран")
    event_datetime_str: Optional[str] = Field(default="", description="Время события в строковом формате")
    event_date: Optional[str] = Field(default="", description="Дата события")
    auth_method: Optional[str] = Field(default="", description="Метод авторизации")
    auth_type: Optional[str] = Field(default="", description="Тип авторизации")
    request_id: Optional[str] = Field(default="", description="ID запроса")
    
    experiments: Optional[str] = Field(default="[]", description="Эксперименты")
    
    extra_fields: Optional[Dict[str, Any]] = Field(default_factory=dict, description="Дополнительные поля")
    
    class Config:
        populate_by_name = True
        json_encoders = {
            datetime: lambda v: v.isoformat()
        }


class DedupResult(BaseModel):
    """Результат проверки на дедупликацию."""
    is_duplicate: bool = Field(description="Флаг, указывающий является ли событие дубликатом")
    event_hash: str = Field(description="Хеш события")
    original_timestamp: Optional[str] = Field(default=None, description="Временная метка оригинального события (если дубликат)")


class EventProcessingResponse(BaseModel):
    """Ответ API на обработку события."""
    status: str = Field(description="Статус обработки: accepted, rejected, error")
    is_duplicate: bool = Field(description="Является ли событие дубликатом")
    message: str = Field(description="Описательное сообщение")
    event_hash: Optional[str] = Field(default=None, description="Хеш события (если был вычислен)") 