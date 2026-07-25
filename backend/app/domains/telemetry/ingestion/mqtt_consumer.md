"""
MQTT consumer cho telemetry messages.

File này implement MQTTConsumer class kết nối đến EMQX broker,
subscribe topic telemetry và đưa message hợp lệ vào asyncio.Queue.

**Luồng dữ liệu:**
```
Telematic Device → MQTT Broker → MQTTConsumer → asyncio.Queue → BatchWorker
```

**Cách sử dụng:**
```python
consumer = MQTTConsumer()
await consumer.connect()
await consumer.start_consuming()  # Blocking call
```

**Lưu ý:**
- Subscribe với QoS 0 (fire-and-forget)
- Message không hợp lệ được log warning và bỏ qua
- Queue max size: 10,000 messages
- Metrics được track bằng class Metrics đơn giản

**File liên quan:**
- `schemas.py`: Pydantic validation schema
- `batch_worker.py`: Xử lý queue (bước 8)
- `entrypoint.py`: Container entrypoint (bước 14)
"""
