import json
import random
import uuid
import os
import time
from locust import HttpUser, task, between, events
from datetime import datetime, timedelta, timezone, UTC


class EventData:
    """Генератор тестовых данных для событий."""
    
    def __init__(self):
        self.event_names = ["app_list", "play", "pause", "resume", "stop", "content_view", "purchase"]
        self.client_ids = [f"{random.randint(1000000, 9999999):x}{random.randint(1000000, 9999999):x}" for _ in range(200)]
        self.product_ids = [f"{uuid.uuid4()}" for _ in range(50)]
        self.session_ids = [f"{random.randint(1000000000, 9999999999)}{random.randint(1000000000, 9999999999)}" for _ in range(100)]
    
    def generate_event(self):
        """Генерирует тестовое событие."""
        now = datetime.now(UTC) - timedelta(seconds=random.randint(0, 60))
        
        event = {
            "client_id": random.choice(self.client_ids),
            "event_name": random.choice(self.event_names),
            "event_datetime": now.isoformat() + ".000Z",
            "product_id": random.choice(self.product_ids),
            "sid": random.choice(self.session_ids),
            "r": f"{random.randint(1000000000, 9999999999)}{random.randint(1000000000, 9999999999)}",
            "platform": "",
            "profile_age": -1,
            "user_agent": "ru.mts.mtstv/1.1.137.74.6.1(20240214)",
            "screen": "",
            "event_datetime_str": now.strftime("%Y-%m-%d %H:%M:%S"),
            "event_date": now.strftime("%Y-%m-%d"),
            "sc": 1,
            "sr": "1920x1080",
            "os": "Android",
            "user_device_is_tv": 1
        }
        
        return event


# Единый конфиг для тестов
class TestConfig:

    DUPLICATE_RATIO = 0.7  # 70% дубликатов


# Единый класс пользователя для всех тестов
class EventUser(HttpUser):
    """Пользователь для тестирования API событий и дедупликации."""
    
    wait_time = between(0.005, 0.05)
    
    def on_start(self):
        self.event_data = EventData()
        self.duplicate_events = []
        self.events_sent = 0
        self.unique_events_sent = 0
        self.duplicate_events_sent = 0
        self.server_detected_duplicates = 0
        self.start_time = time.time()
        
    def on_stop(self):
        elapsed = time.time() - self.start_time
        if elapsed > 0 and self.events_sent > 0:
            rps = self.events_sent / elapsed
            client_duplicate_percent = (self.duplicate_events_sent / self.events_sent) * 100 if self.events_sent > 0 else 0
            server_duplicate_percent = (self.server_detected_duplicates / self.events_sent) * 100 if self.events_sent > 0 else 0
            
            print(f"User RPS: {rps:.2f}, Total sent: {self.events_sent}")
            print(f"Client sent duplicates: {self.duplicate_events_sent} ({client_duplicate_percent:.2f}%)")
            print(f"Server detected duplicates: {self.server_detected_duplicates} ({server_duplicate_percent:.2f}%)")
            print(f"Unique events saved: {self.events_sent - self.server_detected_duplicates} ({(self.events_sent - self.server_detected_duplicates) / self.events_sent * 100:.2f}%)")
    
    @task(10)
    def send_event(self):
        
        is_duplicate = (random.random() < TestConfig.DUPLICATE_RATIO and 
                        len(self.duplicate_events) > min(10, self.events_sent * 0.1))
        
        if is_duplicate:
            
            event = random.choice(self.duplicate_events)
            self.duplicate_events_sent += 1
        else:
            # Генерируем новое уникальное событие
            event = self.event_data.generate_event()
            self.duplicate_events.append(event)
            self.unique_events_sent += 1
            

            if len(self.duplicate_events) > 500: 
                self.duplicate_events.pop(0)
        
        try:
            with self.client.post(
                "/api/event",
                json=event,
                catch_response=True
            ) as response:
                if response.status_code == 200:
                    self.events_sent += 1
                    resp_data = response.json()
                    

                    if resp_data.get("is_duplicate", False):
                        self.server_detected_duplicates += 1
                else:
                    response.failure(f"Failed with status code: {response.status_code}")
        except Exception as e:

            self.environment.events.request_failure.fire(
                request_type="POST",
                name="/api/event",
                response_time=0,
                exception=e,
            )
    
    @task(1)  # Редко проверяем health
    def check_health(self):
        try:
            with self.client.get(
                "/api/health",
                catch_response=True
            ) as response:
                if response.status_code != 200:
                    response.failure(f"Health check failed with status code: {response.status_code}")
        except Exception as e:
            # Явно регистрируем исключение как ошибку в статистике Locust
            self.environment.events.request_failure.fire(
                request_type="GET",
                name="/api/health",
                response_time=0,
                exception=e,
            )


# Статистика для RPS
total_requests = 0
total_duplicates = 0
start_time = time.time()
last_print_time = time.time()
last_request_count = 0

@events.request.add_listener
def request_success(request_type, name, response_time, response_length, **kwargs):
    global total_requests, total_duplicates, start_time, last_print_time, last_request_count
    total_requests += 1
    
    # Проверяем, был ли это дубликат, если можем извлечь ответ
    response = kwargs.get("response", None)
    if response and hasattr(response, "text") and response.text:
        try:
            resp_data = json.loads(response.text)
            if resp_data.get("is_duplicate", False):
                total_duplicates += 1
        except:
            pass
    
    current_time = time.time()
    if current_time - last_print_time >= 5:
        time_diff = current_time - last_print_time
        request_diff = total_requests - last_request_count
        
        if time_diff > 0:
            current_rps = request_diff / time_diff
            total_rps = total_requests / (current_time - start_time) if current_time > start_time else 0
            duplicate_percent = (total_duplicates / total_requests) * 100 if total_requests > 0 else 0
            expected_percent = TestConfig.DUPLICATE_RATIO * 100
            
            print(f"Current RPS: {current_rps:.2f}, Average RPS: {total_rps:.2f}, Total Requests: {total_requests}")
            print(f"Total duplicates: {total_duplicates} ({duplicate_percent:.2f}%), Target: {expected_percent:.1f}%")
            print(f"Unique saved: {total_requests - total_duplicates} ({(total_requests - total_duplicates) / total_requests * 100:.2f}%)")
            
            last_print_time = current_time
            last_request_count = total_requests 