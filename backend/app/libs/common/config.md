# Cấu hình ứng dụng

`config.py` khai báo cấu hình dùng chung và đọc giá trị từ biến môi trường bằng
Pydantic Settings.

- `MQTTConfig` đọc nhóm biến `MQTT_*`, gồm địa chỉ broker, client ID, thông tin
  đăng nhập và QoS. QoS mặc định là `0` cho luồng telemetry MVP.
- `Settings` chứa cấu hình ứng dụng, database và một đối tượng `MQTTConfig`.
- `get_settings()` cache cấu hình để các module dùng chung một instance.
