flowchart TB
    %% ================= TÁC NHÂN BÊN NGOÀI (EXTERNAL ACTORS) =================
    subgraph ExternalActors ["Tác nhân bên ngoài - Thiết bị Phần cứng"]
        EV_HW["🚗 Phần cứng Xe Điện\n(Cảm biến, PIN/BMS, Định vị GPS)"]
        EVSE_HW["⚡ Phần cứng Trụ Sạc\n(Mạch công suất, Công tơ điện, Đầu đọc RFID)"]
        User["👤 Người dùng / Tài xế / Quản trị viên"]
    end

    %% ================= HỆ THỐNG PHẦN MỀM (THE SYSTEM) =================
    
    subgraph VehicleSoftware ["Phần mềm trên Xe"]
        EV_OS["📱 Telematics Client App\n(Đọc dữ liệu CAN bus, gửi lên Server)"]
    end

    subgraph ChargerSoftware ["Phần mềm trên Trụ sạc"]
        EVSE_OS["🔌 OCPP Client Core\n(Điều khiển cấp điện, giao tiếp Server)"]
    end

    subgraph UserApps ["Giao diện Người dùng & Trang web"]
        AppDriver["📱 App Di Động (Người lái xe)"]
        WebApp["💻 Web Quản trị Tổng\n(Giám sát đội xe, Cảnh báo, Thống kê)"]
    end

    subgraph ServerSystem ["Hệ thống Máy chủ (Server Cloud Backend)"]
        
        %% API / Connection Gateways
        subgraph Gateways ["Lớp Cổng Giao Tiếp"]
            IoTGW["📡 IoT Gateway (MQTT/AMQP)"]
            OCPPGW["🔌 CSMS / OCPP Gateway (Websocket)"]
            APIGW["🌐 API Gateway (REST/GraphQL)"]
        end

        %% Message Broker
        subgraph Broker ["Hàng đợi thông điệp"]
            Kafka{{"📨 Kafka / RabbitMQ<br>Đồng bộ & phân phối sự kiện realtime"}}
        end

        %% Microservices
        subgraph CoreServices ["Lớp Dịch Vụ Cốt Lõi"]
            VehicleSvc["🚙 Vehicle Telematics Service\n(Xử lý SOC, SOH, ODO, Lỗi, GPS)"]
            ChargingSvc["🔋 Charging Management Service\n(Giám sát trạng thái, Điều khiển sạc)"]
            SessionSvc["⏱️ Session & Billing Service\n(Quản lý phiên sạc, Tính cước sạc)"]
            FraudSvc["🛡️ Fraud & Anomaly Service\n(Đối chiếu dữ liệu chéo Xe & Trụ sạc)"]
            UserSvc["👤 Identity & Access Service\n(Xác thực tài khoản, Thẻ RFID)"]
            ReportSvc["📊 Dashboard & Map Service\n(Bản đồ trực quan, Tổng hợp báo cáo)"]
        end

        %% Databases
        subgraph Databases ["Lớp Lưu Trữ Dữ Liệu"]
            TSDB[("📈 Time-Series DB\n(Lịch sử Telematics, Hành trình xe)")]
            RDBMS[("🗄️ Relational DB (PostgreSQL)\n(Hóa đơn, User, Danh mục trụ, Cảnh báo)")]
            Cache[("⚡ Redis Cache\n(Trạng thái online/offline, Session sạc)")]
        end
    end

    %% ================= LUỒNG TƯƠNG TÁC DỮ LIỆU (CONNECTIONS) =================
    
    %% Tương tác phần cứng với phần mềm nhúng đầu cuối
    EV_HW <-->|"Đọc thông số PIN/BMS qua cổng CAN"| EV_OS
    EVSE_HW <-->|"Đọc chỉ số điện / Kích hoạt rơ-le sạc"| EVSE_OS
    
    %% Kết nối từ Thiết bị lên Server
    EV_OS --"Gửi Telemetry (30s/lần)"--> IoTGW
    EVSE_OS <-->|"Giao tiếp Websocket (OCPP 1.6/2.0.1)"| OCPPGW
    
    %% Phân phối thông điệp qua Broker
    IoTGW --> Kafka
    OCPPGW --> Kafka

    %% Tương tác của Người dùng với các Ứng dụng/Web
    User -->|"Tương tác vật lý & xem màn hình"| EV_OS
    User -->|"Sử dụng App di động"| AppDriver
    User -->|"Sử dụng Trình duyệt Web"| WebApp

    %% App kết nối qua API Gateway
    AppDriver --> APIGW
    WebApp --> APIGW

    %% Định tuyến từ Gateway vào Microservices
    APIGW --> UserSvc
    APIGW --> SessionSvc
    APIGW --> ChargingSvc
    APIGW --> ReportSvc
    APIGW --> VehicleSvc

    %% Xử lý hướng sự kiện từ Kafka
    Kafka -- "Consume Dữ liệu Xe" --> VehicleSvc
    Kafka -- "Consume Dữ liệu Trụ" --> ChargingSvc
    Kafka -- "Đối chiếu kép Xe & Trụ" --> FraudSvc

    %% Tương tác giữa các dịch vụ & Lưu trữ
    VehicleSvc --> TSDB
    VehicleSvc --> Cache
    ChargingSvc --> Cache
    ChargingSvc --> RDBMS
    SessionSvc --> RDBMS
    UserSvc --> RDBMS
    ReportSvc -. "Truy vấn tổng hợp" .-> TSDB
    ReportSvc -. "Truy vấn tổng hợp" .-> RDBMS
    FraudSvc -. "Lưu vết vi phạm / Cảnh báo" .-> RDBMS

    %% Luồng nghiệp vụ đặc thù
    SessionSvc -- "Yêu cầu Start/Stop sạc" --> ChargingSvc
    ChargingSvc -- "Gửi lệnh điều khiển" --> OCPPGW
    OCPPGW <-->|"Truyền tải bản tin điều khiển"| EVSE_OS
