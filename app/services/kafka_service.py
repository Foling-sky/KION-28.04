import os
import json
from typing import Dict, Any, List, Optional, Callable
import asyncio
import time

from aiokafka import AIOKafkaProducer, AIOKafkaConsumer
from aiokafka.errors import KafkaError
from loguru import logger


class KafkaService:
    """Сервис для работы с Kafka."""
    
    def __init__(self):
        self.producer = None
        self.consumers = {}
        self.running = False
    
    async def connect_producer(self, 
                              bootstrap_servers: str = "localhost:9092",
                              acks: str = "all",
                              max_retries: int = 5,
                              retry_backoff: float = 1.5) -> None:
        """Устанавливает соединение для Producer Kafka."""
        retry_count = 0
        retry_delay = 1.0
        
        while retry_count < max_retries:
            try:
                self.producer = AIOKafkaProducer(
                    bootstrap_servers=bootstrap_servers,
                    value_serializer=lambda v: json.dumps(v).encode('utf-8'),
                    acks=acks
                )
                
                await self.producer.start()
                logger.info(f"Successfully connected Kafka producer to {bootstrap_servers}")
                return
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"Failed to connect Kafka producer: {str(e)}")
                    raise
                
                logger.warning(f"Kafka connection attempt {retry_count} failed: {str(e)}. Retrying in {retry_delay:.1f} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= retry_backoff
    
    async def disconnect_producer(self) -> None:
        """Закрывает соединение Producer с Kafka."""
        if self.producer:
            await self.producer.stop()
            logger.info("Kafka producer disconnected")
    
    async def connect_consumer(self, 
                               topic: str,
                               group_id: str,
                               bootstrap_servers: str = "localhost:9092",
                               auto_offset_reset: str = "earliest",
                               max_retries: int = 5,
                               retry_backoff: float = 1.5) -> None:
        """Устанавливает соединение для Consumer Kafka."""
        retry_count = 0
        retry_delay = 1.0
        
        while retry_count < max_retries:
            try:
                consumer = AIOKafkaConsumer(
                    topic,
                    bootstrap_servers=bootstrap_servers,
                    group_id=group_id,
                    auto_offset_reset=auto_offset_reset,
                    value_deserializer=lambda m: json.loads(m.decode('utf-8'))
                )
                
                await consumer.start()
                logger.info(f"Successfully connected Kafka consumer to {bootstrap_servers} for topic {topic}")
                
                self.consumers[topic] = consumer
                
                return consumer
            except Exception as e:
                retry_count += 1
                if retry_count >= max_retries:
                    logger.error(f"Failed to connect Kafka consumer: {str(e)}")
                    raise
                
                logger.warning(f"Kafka consumer connection attempt {retry_count} failed: {str(e)}. Retrying in {retry_delay:.1f} seconds...")
                await asyncio.sleep(retry_delay)
                retry_delay *= retry_backoff
    
    async def disconnect_consumer(self, topic: str) -> None:
        """Закрывает соединение Consumer с Kafka."""
        if topic in self.consumers:
            consumer = self.consumers[topic]
            await consumer.stop()
            del self.consumers[topic]
            logger.info(f"Kafka consumer for topic {topic} disconnected")
    
    async def send_message(self, topic: str, message: Dict[str, Any]) -> bool:
        """Отправляет сообщение в топик Kafka."""
        if not self.producer:
            logger.error("Kafka producer not connected")
            return False
        
        try:
            await self.producer.send_and_wait(topic, message)
            return True
        except KafkaError as e:
            logger.error(f"Failed to send message to Kafka: {str(e)}")
            return False
        except Exception as e:
            logger.error(f"Unexpected error when sending to Kafka: {str(e)}")
            return False
    
    async def consume_messages(self, 
                               topic: str, 
                               callback: Callable[[Dict[str, Any]], None],
                               max_concurrency: int = 10) -> None:
        """Запускает асинхронное потребление сообщений из топика."""
        if topic not in self.consumers:
            logger.error(f"Consumer for topic {topic} not connected")
            return
        
        consumer = self.consumers[topic]
        self.running = True
        
        semaphore = asyncio.Semaphore(max_concurrency)
        tasks = set()
        
        async def process_message(message):
            async with semaphore:
                try:
                    await callback(message.value)
                except Exception as e:
                    logger.error(f"Error processing message: {str(e)}")
        
        try:
            async for message in consumer:
                if not self.running:
                    break
                
                task = asyncio.create_task(process_message(message))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except Exception as e:
            logger.error(f"Error in Kafka consumer loop: {str(e)}")
        finally:
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            
            self.running = False
    
    def stop_consuming(self) -> None:
        """Останавливает потребление сообщений."""
        self.running = False
        logger.info("Kafka consumer loop stopping") 