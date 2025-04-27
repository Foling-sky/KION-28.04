import json
import random
import argparse
import os
from datetime import datetime, timedelta
import uuid


def load_example_event(filepath):
    """Загружает пример события из JSON файла."""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception as e:
        print(f"Ошибка при загрузке файла примера: {str(e)}")
        return {}


def generate_events(base_event, count=100, duplicate_ratio=0.2):
    """Генерирует набор тестовых событий на основе базового примера."""
    events = []
    unique_events = []
    
    client_ids = [f"{random.randint(1000000, 9999999):x}{random.randint(1000000, 9999999):x}" for _ in range(50)]
    product_ids = [str(uuid.uuid4()) for _ in range(20)]
    event_names = ["app_list", "play", "pause", "resume", "stop", "content_view", "purchase"]
    session_ids = [f"{random.randint(1000000000, 9999999999)}{random.randint(1000000000, 9999999999)}" for _ in range(30)]
    
    for i in range(count):
        if unique_events and random.random() < duplicate_ratio:
            event = unique_events[random.randint(0, len(unique_events) - 1)].copy()
        else:
            now = datetime.utcnow() - timedelta(seconds=random.randint(0, 3600))
            
            event = base_event.copy()
            event["client_id"] = random.choice(client_ids)
            event["event_name"] = random.choice(event_names)
            event["event_datetime"] = now.isoformat() + ".000Z"
            event["event_datetime_str"] = now.strftime("%Y-%m-%d %H:%M:%S")
            event["event_date"] = now.strftime("%Y-%m-%d")
            event["product_id"] = random.choice(product_ids)
            event["sid"] = random.choice(session_ids)
            event["r"] = f"{random.randint(1000000000, 9999999999)}{random.randint(1000000000, 9999999999)}"
            
            unique_events.append(event)
        
        events.append(event)
    
    return events


def save_events(events, output_file):
    """Сохраняет события в JSON файл."""
    try:
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(events, f, indent=2)
        print(f"Сохранено {len(events)} событий в {output_file}")
    except Exception as e:
        print(f"Ошибка при сохранении файла: {str(e)}")


def main():
    """Основная функция."""
    parser = argparse.ArgumentParser(description='Генератор тестовых событий для KION Event Deduplicator')
    parser.add_argument('--example', default='../../Запросы/Как выглядит один запрос(пример).json', help='Путь к файлу с примером события')
    parser.add_argument('--output', default='test_events.json', help='Выходной файл с тестовыми событиями')
    parser.add_argument('--count', type=int, default=1000, help='Количество событий для генерации')
    parser.add_argument('--duplicate-ratio', type=float, default=0.2, help='Доля дубликатов (от 0 до 1)')
    
    args = parser.parse_args()
    
    base_event = load_example_event(args.example)
    if not base_event:
        print("Не удалось загрузить пример события. Проверьте путь к файлу.")
        return
    
    events = generate_events(base_event, args.count, args.duplicate_ratio)
    save_events(events, args.output)


if __name__ == "__main__":
    main()