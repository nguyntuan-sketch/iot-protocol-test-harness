# IoT Protocol Test Harness & Simulators

Bộ công cụ giả lập phía thiết bị (IoT Device Simulators) độc lập phục vụ kiểm thử tích hợp kết nối cho 11 giao thức IoT phổ biến (12 mục tiêu kiểm thử bao gồm TCP và UDP thô riêng biệt) tới IoT Platform của người dùng.

---

## 1. Giới thiệu

### 1.1. Công cụ này là gì?
- Là bộ công cụ dòng lệnh (CLI) độc lập đóng vai trò thiết bị IoT (phía Client / Sensor / Gateway), chủ động khởi tạo kết nối, gửi dữ liệu telemetry mẫu và lắng nghe lệnh downlink gửi tới IoT Platform.
- Hỗ trợ đầy đủ 11 giao thức IoT phổ biến: HTTP, MQTT, CoAP, XMPP, AMQP, DDS, LwM2M, LoRaWAN, WebSocket, TCP thô, UDP thô và TFTP.
- Đi kèm bộ điều phối chạy gộp [run_sims.py](run_sims.py) giúp kiểm tra tự động toàn bộ 12 mục tiêu kết nối một cách tuần tự, an toàn và tổng hợp báo cáo trực quan.

### 1.2. Công cụ này KHÔNG phải là gì?
- **KHÔNG phải là IoT Platform:** Bộ công cụ không lưu trữ dữ liệu, không có cơ sở dữ liệu thời gian thực và không quản lý vòng đời thiết bị.
- **KHÔNG tự dựng server hay broker giả lập nội bộ:** Các simulator đóng vai trò client bắn vào platform thật của bạn (ngoại lệ duy nhất: file demo server [bin/leshan-server-demo.jar](bin/leshan-server-demo.jar) được cung cấp kèm theo chỉ để bạn thử nghiệm LwM2M cục bộ khi cần).
- **KHÔNG gán nhãn Pass/Fail:** Runner không kết luận Pass/Fail tùy tiện mà phân loại trung thực 5 trạng thái mạng/giao thức: `OK`, `REFUSED`, `TIMEOUT`, `AUTH_FAILED`, `PROTO_ERROR`.
- **KHÔNG chứng minh dữ liệu đã được lưu trữ:** Trạng thái kết nối thành công chỉ chứng minh tầng mạng và bắt tay giao thức đã hoàn tất; việc dữ liệu có được platform bóc tách, lưu trữ vào cơ sở dữ liệu hay kích hoạt rule-engine hay không cần được kiểm chứng bằng REST API hoặc giao diện quản trị của chính platform đó.

---

## 2. Danh sách giao thức hỗ trợ

Bảng đối chiếu thông số mặc định từ mã nguồn và cấu hình [config/config.yaml](config/config.yaml):

| Giao thức | CLI thực thi | Cổng mặc định | Tầng vận chuyển | Thư viện chính | "connect OK" chứng minh điều gì | Lưu ý quan trọng |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **HTTP** | [sim/http_sim.py](sim/http_sim.py) | 8080 (8443 nếu TLS) | TCP | `requests` | HTTP endpoint phản hồi mã trạng thái HTTP (2xx, 3xx, kể cả 404/401 là server web đang tồn tại và trả lời) | Cổng 8080 thường bị đụng độ dịch vụ khác trên máy phát triển. |
| **MQTT** | [sim/mqtt_sim.py](sim/mqtt_sim.py) | 1883 (8883 nếu TLS) | TCP | `paho-mqtt` | Hoàn thành bắt tay TCP và nhận gói `CONNACK` thành công từ MQTT broker | Sử dụng paho-mqtt v2 (`CallbackAPIVersion.VERSION2`). |
| **CoAP** | [sim/coap_sim.py](sim/coap_sim.py) | 5683 (5684 nếu DTLS) | UDP | `aiocoap` | Gửi bản tin CoAP CON và nhận lại phản hồi ACK / RST từ CoAP Server | Dùng chung cổng 5683 với LwM2M theo mặc định. |
| **XMPP** | [sim/xmpp_sim.py](sim/xmpp_sim.py) | 5222 (5223 nếu TLS) | TCP | `slixmpp` | Hoàn thành mở XML stream và xác thực SASL thành công với XMPP server | Không báo lỗi không chứng minh adapter JID đang online. |
| **AMQP** | [sim/amqp_sim.py](sim/amqp_sim.py) | 5672 (5671 nếu TLS) | TCP | `pika` | Mở kết nối AMQP 0-9-1 và tạo channel thành công với AMQP broker | RabbitMQ mặc định dùng virtual host `/`. |
| **DDS** | [sim/dds_sim.py](sim/dds_sim.py) | Dynamic (7400+) | UDP Multicast | `cyclonedds` | Phát hiện ít nhất 1 Domain Participant khác trong cùng `domain_id` trong thời gian timeout | Không dùng cờ `--host`/`--port`, dùng Multicast SPDP. |
| **LwM2M** | [sim/lwm2m_sim.py](sim/lwm2m_sim.py) | 5683 | UDP | Java Eclipse Leshan | Client Java khởi chạy và nhận phản hồi đăng ký (Registration) từ LwM2M server | Cần JDK Java 17+, cần giám sát tiến trình java mồ côi. |
| **LoRaWAN** | [sim/lorawan_sim.py](sim/lorawan_sim.py) | 1700 | UDP | `socket`, `struct` | Gửi gói Semtech UDP `PUSH_DATA` (0x02) và nhận lại `PUSH_ACK` (0x01) đúng Token | Chỉ chứng minh Gateway reach Network Server, không chứng minh MIC. |
| **WebSocket** | [sim/websocket_sim.py](sim/websocket_sim.py) | 8080 (8443 nếu TLS) | TCP | `websockets` | Bắt tay HTTP Upgrade (101 Switching Protocols) và mở kết nối WebSocket thành công | Chia sẻ cổng 8080 với HTTP theo mặc định. |
| **TCP thô** | [sim/tcp_udp_sim.py](sim/tcp_udp_sim.py) | 9000 | TCP | `socket` (stdlib) | Hoàn thành bắt tay 3 bước TCP (3-way handshake) tới cổng lắng nghe | Chỉ là bắt tay socket, không chứng minh ứng dụng phía sau hiểu payload. |
| **UDP thô** | [sim/tcp_udp_sim.py](sim/tcp_udp_sim.py) | 9001 | UDP | `socket` (stdlib) | Gửi datagram thăm dò và nhận được phản hồi từ server | Nếu server im lặng không trả gói sẽ báo TIMEOUT. |
| **TFTP** | [sim/tftp_sim.py](sim/tftp_sim.py) | 69 | UDP | `tftpy` | Gửi gói RRQ thăm dò file và nhận gói TFTP ERROR hợp lệ (Error Code 1 - File not found) | Chứng minh TFTP daemon đang trực và tuân thủ RFC 1350. |

---

## 3. Yêu cầu hệ thống

- **Hệ điều hành:** Đã kiểm chứng đầy đủ trên Windows 11 (64-bit).
  * *Lưu ý môi trường Linux/macOS:* Đánh dấu (chưa kiểm chứng). Cơ chế dọn dẹp tiến trình con trong [runner/execute.py](runner/execute.py) hiện tại sử dụng Windows Job Object (`JOBOBJECT_EXTENDED_LIMIT_INFORMATION` với cờ `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`) và lệnh `taskkill /T /F`. Khi chạy trên Linux/macOS, người dùng cần điều chỉnh sang quản lý Process Group (`os.killpg`).
- **Python:** Yêu cầu Python phiên bản 3.10 đến 3.12 (đã kiểm chứng thực tế trên Python 3.12.16 trong môi trường Conda `iot-test`).
- **Java (cho giao thức LwM2M):** Yêu cầu Java Development Kit (JDK) phiên bản 17 trở lên (đã kiểm chứng trên Oracle OpenJDK 22).
  * *Cảnh báo quan trọng về Java trên Windows:* Tuyệt đối tránh dùng đường dẫn shim mặc định của Oracle (`C:\Program Files\Common Files\Oracle\Java\javapath\java.exe`). Binary shim này khởi chạy một tiến trình con trung gian nhưng không quản lý vòng đời, dễ dẫn đến hiện tượng sót tiến trình `java.exe` nền mồ côi. Hãy cài JDK chính thức và trỏ biến môi trường `JAVA_HOME` trực tiếp vào thư mục cài đặt JDK thật.
- **Docker:** KHÔNG CẦN CÀI ĐẶT DOCKER. Toàn bộ 11 giao thức đều sử dụng thư viện Python thuần hoặc file JAR Java đóng gói độc lập trong thư mục `bin/`.

---

## 4. Hướng dẫn cài đặt từng bước

### Bước 1: Khởi tạo môi trường Python

Khuyến nghị sử dụng Conda hoặc virtualenv độc lập để tránh xung đột thư viện:

* **Sử dụng Conda (Khuyến nghị):**
  ```powershell
  conda create -n iot-test python=3.12 -y
  conda activate iot-test
  ```

* **Hoặc sử dụng Python venv:**
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\Activate.ps1
  ```

### Bước 2: Cài đặt các thư viện phụ thuộc

Dự án cung cấp 2 file khai báo phụ thuộc:
- [requirements.lock.txt](requirements.lock.txt) (Khuyến nghị): Chứa phiên bản chính xác 100% đã được kiểm chứng hoạt động ổn định trên môi trường thử nghiệm.
- [requirements.txt](requirements.txt): Chứa phiên bản tối thiểu (`>=`) nếu bạn cần linh hoạt nâng cấp.

Thực hiện cài đặt:
```powershell
pip install -r requirements.lock.txt
```

### Bước 3: Chạy script kiểm tra môi trường

Thực thi script kiểm tra toàn diện trước khi vận hành:
```powershell
.\setup_environment.ps1
```

*(Lưu ý: Đối với môi trường Linux/macOS, sử dụng `./setup_environment.sh` - chưa kiểm chứng trên Windows).*

### Bước 4: Cách đọc kết quả kiểm tra
Khi chạy xong `setup_environment.ps1`, bạn sẽ nhận được báo cáo gồm 6 phần (41 hạng mục kiểm tra):
1. **System Prerequisites:** Báo `[PASS]` cho Python, pip, Java. Cảnh báo `[WARN] Docker not installed` là bình thường vì dự án không dùng Docker.
2. **Directory Structure & Required Files:** Xác nhận đủ các thư mục và file cấu hình, payload.
3. **Python Libraries:** Xác nhận nạp thành công 14 thư viện lõi (`requests`, `paho-mqtt`, `aiocoap`, `slixmpp`, `pika`, `cyclonedds`, `websockets`, `tftpy`, `yaml`, v.v.).
4. **Group B Tools & Checksums:** Kiểm tra tính toàn vẹn SHA-256 của file `bin/leshan-client-demo.jar` khớp với [bin/VERSIONS.md](bin/VERSIONS.md).
5. **Target Ports & Reachability:** Quét cổng platform. Nếu thấy báo `UNREACHABLE` hoặc `UNKNOWN (UDP)` thì đây là trạng thái bình thường khi bạn chưa khởi động IoT Platform đích.
6. **Tổng kết:** Phải đạt `Passed checks: 41` và `Failed checks: 0`.

---

## 5. Cấu hình hệ thống

### 5.1. File cấu hình chính: [config/config.yaml](config/config.yaml)

File cấu hình trung tâm được chia làm các khối chức năng rõ ràng:

| Tên khối | Các khóa chính | Tình trạng sử dụng trong mã nguồn |
| :--- | :--- | :--- |
| `global` | `target_host`, `timeout_sec`, `device_id`, `telemetry_payload` | **Đang được đọc và sử dụng:** `target_host` và `timeout_sec` được `run_sims.py` và các CLI dùng làm giá trị mặc định. |
| `adapters` | 11 mục: `http`, `mqtt`, `coap`, `xmpp`, `amqp`, `dds`, `lwm2m`, `lorawan`, `websocket`, `tcp_udp`, `tftp` | **Đang được đọc và sử dụng:** Từng simulator trong `sim/` trực tiếp đọc cổng, path, topic, username/password từ khối giao thức tương ứng. |
| `verification` | `platform_api_url`, `platform_api_token`, `query_timeout_sec` | **Khai báo trước (chưa đọc tự động bởi CLI):** Dùng để cấu hình điểm cuối REST API phục vụ việc viết test suite đối soát dữ liệu trên platform. |
| `seed_requirements` | `device_id`, `http_token`, `coap_token`, `amqp_exchange`, v.v. | **Khai báo tài liệu:** Bản ghi danh mục dữ liệu khởi tạo (seed data) cần cấu hình trước trên platform. |
| `tls` | `enabled`, `ca_cert_path`, `client_cert_path`, `ports` | **Khai báo cấu hình:** Danh mục cổng bảo mật và chứng chỉ (các CLI hỗ trợ cờ `--tls` và `--insecure`). |
| `negative_cases` | `invalid_token`, `malformed_json_payload`, v.v. | **Khai báo mẫu:** Dữ liệu mẫu dùng cho các bài kiểm tra tiêu cực. |

### 5.2. File kế hoạch chạy: [config/run_plan.yaml](config/run_plan.yaml)
Được `run_sims.py` đọc để xác định nhóm cổng thực thi tuần tự, cảnh báo cổng 8080, thời gian ân hạn (`grace` tối thiểu 15s cho LwM2M) và các quy tắc kiểm tra an toàn.

### 5.3. Việc phải làm trước khi chạy lần đầu
1. **Kiểm tra xung đột cổng 8080:** Chạy lệnh `netstat -ano | findstr :8080`. Nếu phát hiện có tiến trình khác đang chiếm cổng 8080 (ví dụ web server nội bộ), hãy mở [config/config.yaml](config/config.yaml) và sửa cổng của `adapters.http.port` và `adapters.websocket.port` sang đúng cổng của IoT Platform.
2. **Cập nhật thông tin xác thực:** Điền token thiết bị vào `adapters.http.access_token` và `adapters.coap.access_token`; cập nhật `username`/`password` cho MQTT và AMQP; điền JID hợp lệ cho XMPP.
3. **Xác nhận kiểu xác thực HTTP:** Mặc định là gửi token qua Header (`auth_mode: "header"`). Nếu platform của bạn yêu cầu token nằm trên URL query (`/api/v1/{token}/telemetry`), hãy đổi sang `auth_mode: "url_token"`.

---

## 6. Bắt đầu nhanh

Sau khi cài đặt xong môi trường, bạn có thể thực hiện kiểm tra nhanh theo 4 bước:

* **Bước 1: Xem trước các lệnh sẽ chạy (Dry-run):**
  ```powershell
  python run_sims.py --dry-run
  ```
  Lệnh này in ra danh sách 12 dòng lệnh kèm tham số đầy đủ nhưng không gửi bất kỳ gói tin nào ra mạng.

* **Bước 2: Chạy kiểm tra kết nối tới toàn bộ 12 mục tiêu:**
  ```powershell
  python run_sims.py --mode connect
  ```
  Runner sẽ lần lượt thử bắt tay với từng dịch vụ trong thời gian tối đa 5 giây mỗi giao thức.

* **Bước 3: Đọc bảng tổng hợp kết quả trên màn hình:**
  ```text
  | PROTO      | MODE     | RESULT          | EXIT   | TIME     | DETAIL
  |------------|----------|-----------------|--------|----------|----------------------------------------
  | http       | connect  | OK              | 0      | 0.40s    | [stage:http_handshake] HTTP 200 OK ...
  | mqtt       | connect  | REFUSED         | 2      | 2.34s    | [stage:tcp_connect] [WinError 10061]...
  ...
  Total: 12 | OK: 1 | REFUSED: 8 | TIMEOUT: 2 | AUTH_FAILED: 0 | PROTO_ERROR: 1
  ```

* **Bước 4: Mở thư mục log kết quả:**
  Runner tự động tạo báo cáo chi tiết trong thư mục `logs/runs/<thời_điểm>/` gồm:
  - `results.json`: Dữ liệu JSON có cấu trúc đầy đủ.
  - `results.md`: Báo cáo định dạng Markdown scannable.
  - `<proto>.txt`: Log thô đầu ra của từng simulator.

---

## 7. Hướng dẫn sử dụng runner: `run_sims.py`

### 7.1. Bảng tham số đầy đủ

| Tham số | Giá trị mặc định | Giải thích chi tiết |
| :--- | :--- | :--- |
| `--mode` | `connect` | Chế độ thực thi: `connect` (bắt tay), `send` (gửi mẫu), `listen` (nghe downlink), `both` (gửi và nghe). |
| `--only` | Rỗng (chạy hết) | Danh sách giao thức cần chạy, ngăn cách bởi dấu phẩy (vd: `--only mqtt,http,tcp`). |
| `--skip` | Rỗng | Danh sách giao thức cần bỏ qua, ngăn cách bởi dấu phẩy (vd: `--skip dds,lorawan`). |
| `--host` | Đọc từ config (`127.0.0.1`) | Ghi đè địa chỉ IP của IoT Platform đích. |
| `--timeout` | `5.0` (giây) | Thời gian chờ tối đa cho mỗi CLI. Trong chế độ `connect`, runner tự động nâng tối thiểu lên `3.0s` để an toàn với loopback Windows. |
| `--count` | `1` | Số lượng bản tin telemetry gửi (dùng cho chế độ `send` hoặc `both`). |
| `--interval` | `1.0` (giây) | Khoảng thời gian giãn cách giữa các lần gửi bản tin. |
| `--duration` | `5.0` (giây) | Thời gian lắng nghe downlink (dùng cho chế độ `listen` hoặc `both`). |
| `--arg` | Không có | Bổ sung tham số thô cho từng CLI cụ thể (vd: `--arg 'mqtt="--port 1884"'`). |
| `--dry-run` | `False` | Chỉ in lệnh thực thi dự kiến, không chạy tiến trình thật. |
| `--yes` | `False` | **Chốt an toàn:** Bắt buộc phải có khi chạy chế độ `--mode send` hoặc `--mode both`. |
| `--allow-write` | `False` | **Chốt an toàn:** Bắt buộc phải có nếu chạy TFTP ở chế độ `send` hoặc `both` để cho phép ghi file lên server. |
| `--out-dir` | `logs/runs/<thời_điểm>` | Ghi đè thư mục lưu trữ log và báo cáo kết quả. |

### 7.2. Các ví dụ sử dụng thực tế

* **Chỉ kiểm tra kết nối MQTT và HTTP:**
  ```powershell
  python run_sims.py --mode connect --only mqtt,http
  ```

* **Kiểm tra platform trên máy chủ khác trong mạng LAN:**
  ```powershell
  python run_sims.py --mode connect --host 192.168.1.100 --timeout 6.0
  ```

* **Gửi 5 bản tin telemetry mẫu qua MQTT (yêu cầu cờ `--yes`):**
  ```powershell
  python run_sims.py --mode send --only mqtt --count 5 --interval 2.0 --yes
  ```

* **Lắng nghe downlink trong 10 giây qua TCP và UDP:**
  ```powershell
  python run_sims.py --mode listen --only tcp,udp --duration 10.0
  ```

* **Ghi đè cổng MQTT riêng biệt:**
  ```powershell
  python run_sims.py --mode connect --only mqtt --arg 'mqtt="--port 1884"'
  ```

### 7.3. Các chốt an toàn (Safety Barriers)
1. **Chặn gửi dữ liệu ngoài ý muốn:** Khi bạn chạy `--mode send` hoặc `--mode both`, runner sẽ dừng lại và thoát mã 10 nếu bạn không truyền cờ `--yes`. Điều này ngăn chặn việc vô tình bắn dữ liệu rác vào platform đang vận hành.
2. **Bảo vệ ghi đè TFTP:** Giao thức TFTP truyền file nhị phân. Nếu chạy send/both mà thiếu cờ `--allow-write`, runner sẽ tự động đánh dấu TFTP là `SKIPPED` để tránh ghi đè file trên máy chủ.
3. **Kẹp timeout tối thiểu 3 giây:** Loopback trên Windows khi gặp cổng đóng cần khoảng 2 giây để hoàn thành chu trình thử nghiệm socket; nếu đặt timeout dưới 3s, kết quả sẽ bị nhầm lẫn thành `TIMEOUT` thay vì `REFUSED`.

### 7.4. Mã thoát (Exit Code) của runner
- `0`: Thực thi thành công toàn bộ các simulator theo kế hoạch (kể cả khi các simulator báo REFUSED/TIMEOUT).
- `10`: Lỗi môi trường hoặc tham số không hợp lệ (ví dụ chạy ngoài môi trường Conda thiếu thư viện, thiếu cờ `--yes`, danh sách `--only` rỗng).
- `11`: Cảnh báo rò rỉ tiến trình nền (phát hiện có tiến trình `java.exe` mồ côi chưa được giải phóng sau khi chạy xong).

---

## 8. Hướng dẫn sử dụng từng CLI độc lập

Mỗi simulator trong thư mục `sim/` có thể được gọi độc lập bằng dòng lệnh. Cả 11 CLI đều hỗ trợ 4 chế độ thông qua cờ `--mode`:
- `connect`: Thử bắt tay/kết nối tối thiểu rồi thoát ngay lập tức (không gửi dữ liệu telemetry).
- `send`: Kết nối và gửi `--count` bản tin mẫu.
- `listen`: Lắng nghe gói tin hoặc downlink từ platform trong `--duration` giây.
- `both`: Kết hợp vừa gửi vừa lắng nghe.

### Danh sách lệnh kiểm tra thực tế cho 11 giao thức:

1. **HTTP ([sim/http_sim.py](sim/http_sim.py)):**
   ```powershell
   python sim/http_sim.py --mode connect --host 127.0.0.1 --port 8080 --timeout 5.0
   ```
2. **MQTT ([sim/mqtt_sim.py](sim/mqtt_sim.py)):**
   ```powershell
   python sim/mqtt_sim.py --mode connect --host 127.0.0.1 --port 1883 --timeout 5.0
   ```
3. **CoAP ([sim/coap_sim.py](sim/coap_sim.py)):**
   ```powershell
   python sim/coap_sim.py --mode connect --host 127.0.0.1 --port 5683 --timeout 5.0
   ```
4. **XMPP ([sim/xmpp_sim.py](sim/xmpp_sim.py)):**
   ```powershell
   python sim/xmpp_sim.py --mode connect --host 127.0.0.1 --port 5222 --timeout 5.0
   ```
5. **AMQP ([sim/amqp_sim.py](sim/amqp_sim.py)):**
   ```powershell
   python sim/amqp_sim.py --mode connect --host 127.0.0.1 --port 5672 --timeout 5.0
   ```
6. **DDS ([sim/dds_sim.py](sim/dds_sim.py)):**
   ```powershell
   python sim/dds_sim.py --mode connect --timeout 5.0
   ```
7. **LwM2M ([sim/lwm2m_sim.py](sim/lwm2m_sim.py)):**
   ```powershell
   python sim/lwm2m_sim.py --mode connect --server-uri coap://127.0.0.1:5683 --timeout 5.0
   ```
   > **CẢNH BÁO QUAN TRỌNG:** Khi chạy riêng lẻ `sim/lwm2m_sim.py`, nếu bạn ngắt bằng `Ctrl+C`, tiến trình Java nền có thể không kịp đóng. Hãy chạy lệnh `tasklist | findstr /i java` để kiểm tra. Nếu còn tồn tại PID java, hãy dọn bằng script [scripts/teardown.ps1](scripts/teardown.ps1).
8. **LoRaWAN ([sim/lorawan_sim.py](sim/lorawan_sim.py)):**
   ```powershell
   python sim/lorawan_sim.py --mode connect --host 127.0.0.1 --port 1700 --timeout 5.0
   ```
9. **WebSocket ([sim/websocket_sim.py](sim/websocket_sim.py)):**
   ```powershell
   python sim/websocket_sim.py --mode connect --host 127.0.0.1 --port 8080 --timeout 5.0
   ```
10. **TCP / UDP thô ([sim/tcp_udp_sim.py](sim/tcp_udp_sim.py)):**
    - TCP: `python sim/tcp_udp_sim.py --transport tcp --mode connect --host 127.0.0.1 --port 9000 --timeout 5.0`
    - UDP: `python sim/tcp_udp_sim.py --transport udp --mode connect --host 127.0.0.1 --port 9001 --timeout 5.0`
11. **TFTP ([sim/tftp_sim.py](sim/tftp_sim.py)):**
    ```powershell
    python sim/tftp_sim.py --mode connect --host 127.0.0.1 --port 69 --timeout 5.0
    ```

---

## 9. Hướng dẫn đọc và giải thích kết quả

### 9.1. Bảng trạng thái (Statuses)

| Trạng thái | Mã exit CLI | Ý nghĩa kỹ thuật | Nguyên nhân thường gặp | Việc cần xử lý |
| :--- | :--- | :--- | :--- | :--- |
| **`OK`** | `0` | Bắt tay hoặc trao đổi gói tin hợp lệ theo chuẩn giao thức. | Cổng dịch vụ đang mở và phản hồi đúng chuẩn. | Sẵn sàng cho việc kiểm thử gửi dữ liệu. |
| **`REFUSED`** | `2` | Kết nối bị từ chối ở tầng mạng (nhận TCP RST hoặc ICMP Port Unreachable). | Dịch vụ trên platform chưa được bật hoặc tường lửa chặn cổng. | Khởi động adapter tương ứng trên platform; mở firewall. |
| **`TIMEOUT`** | `3` | Không nhận được bất kỳ phản hồi nào trong khoảng thời gian quy định. | Cổng UDP không có dịch vụ lắng nghe; gói tin bị drop; hoặc DDS không thấy đối tác. | Kiểm tra IP đích; kiểm tra cấu hình mạng Multicast (DDS). |
| **`AUTH_FAILED`**| `4` | Kết nối mạng thành công nhưng bị server từ chối ở bước đăng nhập. | Sai access token, mật khẩu, JID hoặc client không có quyền kết nối. | Kiểm tra thông tin tài khoản trong [config/config.yaml](config/config.yaml). |
| **`PROTO_ERROR`**| `1` | Lỗi vi phạm giao thức (ví dụ HTTP 404/500, WebSocket không nhận header Upgrade 101). | Sai đường dẫn API endpoint, hoặc trỏ nhầm cổng dịch vụ khác. | Kiểm tra lại path API trong config; kiểm tra đụng độ cổng 8080. |
| **`NO_RESULT_LINE`**| (Khác) | Tiến trình CLI bị crash hoặc không in dòng tổng kết chuẩn. | Lỗi cú pháp mã lệnh, thiếu module hoặc lỗi ngoại lệ hệ thống. | Đọc file log thô trong `logs/runs/<thời_điểm>/<proto>.txt`. |
| **`RUNNER_TIMEOUT`**| `-1` | Tiến trình bị treo quá lâu và bị runner cưỡng chế ngắt (kill). | Simulator bị kẹt vòng lặp vô hạn hoặc socket chặn luồng. | Kiểm tra cấu hình timeout và thông số mạng. |
| **`SKIPPED`** | (None) | Simulator được chủ động bỏ qua không thực thi. | Sử dụng cờ `--skip` hoặc TFTP thiếu cờ `--allow-write`. | Truyền thêm cờ nếu muốn thực thi simulator này. |

### 9.2. Định dạng dòng kết quả chuẩn
Mỗi simulator khi kết thúc bắt buộc in ra một dòng duy nhất theo định dạng:
```text
RESULT: <giao_thức> [mode=<CHẾ_ĐỘ> | <TRẠNG_THÁI>] detail=<chi_tiết_chẩn_đoán>
```
*Ví dụ:* `RESULT: mqtt connect=REFUSED detail=[stage:tcp_connect] [WinError 10061] Connection refused`

### 9.3. Các quy tắc quan trọng khi phân tích kết quả
1. **TIMEOUT ở UDP và DDS mang nghĩa "chưa biết", không khẳng định là lỗi:** Giao thức UDP không hướng kết nối; khi server không chạy, nếu router/hệ điều hành không trả ICMP Port Unreachable thì socket chỉ có thể chờ đến khi hết hạn (timeout). Tương tự, DDS dùng Multicast discovery, không thấy participant nghĩa là chưa phát hiện đối tác.
2. **OK ở TCP chỉ là bắt tay socket:** Trạng thái OK trên cổng TCP thô chỉ chứng minh việc thiết lập kết nối 3 bước (SYN -> SYN/ACK -> ACK) thành công, không khẳng định ứng dụng phía sau hiểu được cấu trúc bản tin.
3. **LoRaWAN ACK không chứng minh tính hợp lệ của MIC hay payload:** Phản hồi `PUSH_ACK` từ Semtech Gateway protocol chỉ xác nhận Network Server đã nhận gói tin UDP từ Gateway forwarder; việc giải mã session key, kiểm tra MIC và bóc tách dữ liệu sensor diễn ra ở tầng ứng dụng sâu hơn.
4. **XMPP không báo lỗi không có nghĩa là tin nhắn đã tới người nhận:** Việc xác thực SASL thành công chỉ chứng minh tài khoản hợp lệ với XMPP server, không bảo đảm adapter JID nhận tin đang online.
5. **Độ trễ loopback trên Windows:** Cổng đóng trên `127.0.0.1` của Windows có thể mất từ 1.5 đến 2.2 giây để hoàn tất chu trình kiểm tra socket. Do đó luôn duy trì timeout tối thiểu từ 3.0 giây trở lên và tránh dùng tên miền `localhost` (dễ bị trễ thêm do phân giải IPv6 `::1`).

---

## 10. Thử nghiệm LwM2M cục bộ (Tùy chọn)

Nếu bạn chưa có sẵn LwM2M Server trên platform, dự án có sẵn demo server chính thức của Eclipse Leshan trong thư mục `bin/` để bạn tự kiểm chứng vòng đời LwM2M Client.

### 10.1. Khởi chạy Leshan Server Demo
Mở một cửa sổ terminal mới và thực thi:
```powershell
java -jar bin/leshan-server-demo.jar --coap-port 5683 --web-port 8088
```
*(Lưu ý: Bắt buộc truyền `--web-port 8088` để tránh đụng độ với cổng 8080 nếu đang có dịch vụ web khác chạy trên máy).*

### 10.2. Chạy thử nghiệm LwM2M Simulator
Trên cửa sổ terminal chính, thực hiện kết nối:
```powershell
python sim/lwm2m_sim.py --mode connect --server-uri coap://127.0.0.1:5683 --timeout 5.0
```
Kết quả mong đợi: `RESULT: lwm2m connect=OK detail=[stage:registration] Registered successfully with location: /rd/...`

### 10.3. Dừng và dọn dẹp
1. Nhấn `Ctrl + C` trên cửa sổ chạy server để tắt server.
2. Kiểm tra sạch tiến trình nền bằng PowerShell:
   ```powershell
   cmd.exe /c "tasklist | findstr /i java"
   ```
   Nếu còn tiến trình `java.exe` chạy ngầm, thực thi script dọn dẹp:
   ```powershell
   .\scripts\teardown.ps1
   ```

---

## 11. Bảng xử lý sự cố thường gặp (Troubleshooting)

| Triệu chứng | Nguyên nhân khả dĩ | Hướng dẫn xử lý |
| :--- | :--- | :--- |
| **HTTP báo `OK` nhưng mã 404, WebSocket báo `PROTO_ERROR`** | Cổng 8080 đang bị chiếm bởi dịch vụ web khác của máy (ví dụ web server nội bộ, Embedthis-http), không phải IoT Platform. | Chạy `netstat -ano \| findstr :8080` để tìm PID. Đổi cấu hình `adapters.http.port` và `adapters.websocket.port` trong [config/config.yaml](config/config.yaml) trỏ đúng cổng của platform. |
| **Runner cảnh báo mã thoát 11 (rò rỉ Java)** | Simulator `lwm2m_sim.py` bị ngắt giữa chừng khiến tiến trình con `java.exe` không được giải phóng. | Chạy ngay script dọn dẹp [scripts/teardown.ps1](scripts/teardown.ps1) hoặc lệnh `taskkill /F /IM java.exe`. |
| **Đặt timeout 1s - 2s bị báo `TIMEOUT` dù cổng đóng** | Windows loopback socket khi kết nối tới cổng đóng mất ~2 giây để thử nghiệm ngăn xếp mạng trước khi trả lỗi Connection Refused. | Luôn duy trì timeout từ `3.0s` trở lên và sử dụng IP `127.0.0.1` thay vì `localhost`. |
| **MQTT Broker ngắt kết nối im lặng (Connection closed)** | Trùng `client_id` với một client khác đang kết nối vào broker, hoặc username/password chưa được cấp quyền trong ACL. | Đổi `client_id` trong [config/config.yaml](config/config.yaml) thành chuỗi ngẫu nhiên (ví dụ `client_id: "test_client_999"`). |
| **DDS báo `TIMEOUT` không thấy participant từ xa** | Mạng Multicast bị chặn bởi Firewall Windows, VPN đang bật, hoặc CycloneDDS tự chọn card mạng ảo (WSL/Hyper-V/VMware). | Tắt tạm thời VPN; mở inbound rule cho UDP; hoặc gán biến môi trường `CYCLONEDDS_URI` trỏ thẳng vào IP của card mạng vật lý đang kết nối platform. |
| **CoAP DTLS không hoạt động trên Windows** | Thư viện `aiocoap` trên Windows thiếu backend OpenSSL DTLSSocket tương thích natively. | Chạy chế độ CoAP không mã hóa (cổng 5683) trên Windows; hoặc chuyển sang môi trường Linux nếu bắt buộc kiểm thử DTLS (coaps). |
| **Chạy runner lập tức văng mã lỗi 10** | Bạn đang chạy bằng Python base của hệ điều hành, thiếu các module bắt buộc (`pika`, `cyclonedds`, `yaml`). | Kích hoạt đúng môi trường Conda trước khi chạy: `conda activate iot-test`. |
| **Xuất hiện "thiết bị ma" (phantom device) LwM2M** | Tiến trình Leshan client cũ bị sót và định kỳ tự động gửi bản tin update registration lên server. | Dọn dẹp triệt để tiến trình Java mồ côi bằng lệnh: `taskkill /F /IM java.exe`. |

---

## 12. Cấu trúc thư mục dự án

Toàn bộ 42 file trong repository được tổ chức tinh gọn và chuẩn hóa như sau:

```text
iot-protocol-test-harness/
|-- .gitignore                     # Bộ lọc chặn commit file log, bytecode pyc, file tạm
|-- Californium3.client.properties # Cấu hình CoAP/OSCORE của Eclipse Californium (Client)
|-- Californium3.server.properties # Cấu hình CoAP/OSCORE của Eclipse Californium (Server)
|-- README.md                      # Tài liệu hướng dẫn sử dụng và bàn giao hệ thống
|-- check_libs.py                  # Script kiểm tra khả năng nạp 14 module Python
|-- dds_compat.py                  # Module shim hỗ trợ tương thích DDS (dự phòng)
|-- lorawan_udp_forwarder.py       # Bộ giả lập Semtech Packet Forwarder v2 thuần Python
|-- requirements.lock.txt          # Danh sách thư viện Python với phiên bản cố định
|-- requirements.txt               # Danh sách thư viện Python tối thiểu
|-- run_sims.py                    # Script điều phối chạy gộp 11 CLI (12 mục tiêu)
|-- sample.bin                     # File nhị phân mẫu phục vụ kiểm thử dữ liệu thô
|-- setup_environment.ps1          # Script kiểm tra điều kiện tiên quyết môi trường (PowerShell)
|-- setup_environment.sh           # Script kiểm tra điều kiện tiên quyết môi trường (Bash/Linux)
|
|-- bin/
|   |-- VERSIONS.md                # Bảng đăng ký phiên bản và mã băm SHA-256 của các binary JAR
|   |-- leshan-client-demo.jar     # Client demo LwM2M chính thức của Eclipse Leshan (2.0.0-M17)
|   \-- leshan-server-demo.jar     # Server demo LwM2M chính thức của Eclipse Leshan (2.0.0-M17)
|
|-- config/
|   |-- config.yaml                # Cấu hình kết nối trung tâm cho toàn bộ 11 giao thức
|   \-- run_plan.yaml              # Kế hoạch gom nhóm cổng và thứ tự thực thi của runner
|
|-- logs/
|   \-- .gitkeep                   # Giữ thư mục logs rỗng sạch sẽ trên Git
|
|-- payloads/
|   |-- lorawan_push_data.json     # Bản tin mẫu Semtech PUSH_DATA định dạng JSON
|   |-- sample_tftp_file.txt       # File văn bản mẫu 1419 bytes phục vụ kiểm thử TFTP
|   \-- telemetry_schema.idl       # Schema định nghĩa cấu trúc dữ liệu CycloneDDS (IDL)
|
|-- runner/
|   |-- __init__.py                # Package init của runner
|   |-- execute.py                 # Quản lý thực thi tiến trình con và dọn dẹp Windows Job Object
|   |-- parse.py                   # Phân tích cú pháp dòng RESULT và mã lỗi trả về
|   |-- registry.py                # Danh bạ 12 mục tiêu và tự động phát hiện cờ dòng lệnh
|   \-- report.py                  # Xuất báo cáo bảng console, kết quả JSON và Markdown
|
|-- scripts/
|   |-- check_ports.py             # Script kiểm tra khả năng kết nối tới các cổng đích
|   |-- teardown.ps1               # Script dọn dẹp tiến trình treo trên Windows (PowerShell)
|   \-- teardown.sh                # Script dọn dẹp tiến trình treo trên Linux (Bash)
|
\-- sim/
    |-- common.py                  # Module dùng chung chứa hằng số mã thoát và định dạng kết quả
    |-- http_sim.py                # Simulator giao thức HTTP (#1)
    |-- mqtt_sim.py                # Simulator giao thức MQTT (#2)
    |-- coap_sim.py                # Simulator giao thức CoAP (#3)
    |-- xmpp_sim.py                # Simulator giao thức XMPP (#4)
    |-- amqp_sim.py                # Simulator giao thức AMQP (#5)
    |-- dds_sim.py                 # Simulator giao thức DDS (#6)
    |-- lwm2m_sim.py               # Simulator giao thức LwM2M (#7)
    |-- lorawan_sim.py             # Simulator giao thức LoRaWAN (#8)
    |-- websocket_sim.py           # Simulator giao thức WebSocket (#9)
    |-- tcp_udp_sim.py             # Simulator giao thức TCP và UDP thô (#10)
    \-- tftp_sim.py                # Simulator giao thức TFTP (#11)
```

---

## 13. Các giới hạn đã biết

1. **Mức độ kiểm chứng với Platform thực tế:**
   - Bộ công cụ mới được kiểm chứng trên môi trường loopback cục bộ (`127.0.0.1`) và qua các server demo độc lập. Người dùng cần chủ động kiểm tra thực tế với IoT Platform đích.
2. **Hiện trạng hỗ trợ TLS / DTLS:**
   - Mã nguồn các CLI đã có sẵn tham số và cờ `--tls`, `--insecure` cho các giao thức (HTTP, MQTT, CoAP, XMPP, AMQP, WebSocket).
   - Tuy nhiên, phần lớn các bài kiểm thử tự động hiện tại mới chạy ở chế độ không mã hóa (Plaintext). Việc kiểm thử với chứng chỉ bảo mật (x509 CA cert, Client cert, PSK DTLS) cần được cấu hình bổ sung đường dẫn cert thật trong khối `tls` của [config/config.yaml](config/config.yaml) và đánh dấu là *(chưa kiểm chứng trên hạ tầng bảo mật thực)*.
3. **Mức độ kiểm chứng chế độ `send` và `listen`:**
   - Các chế độ `send` và `listen` đã được kiểm chứng cú pháp và đóng gói bản tin, nhưng chưa thực hiện đo lường hiệu năng tải lớn (stress test) hay kiểm tra mất gói trên đường truyền mạng diện rộng.
4. **Phạm vi kiểm thử:**
   - Bộ công cụ chỉ dừng lại ở việc kiểm tra tính tương thích ở tầng giao thức (Protocol & Transport Layer). Công cụ không có khả năng tự động xác thực xem dữ liệu cảm biến sau khi đến platform có được giải mã đúng quy tắc hay được lưu trữ vào cơ sở dữ liệu hay không.

---

## 14. Lưu ý 

1. **Thận trọng khi gửi dữ liệu thật:**
   Chế độ `--mode send` và `--mode both` sẽ trực tiếp đẩy dữ liệu telemetry vào broker hoặc server đích. Điều này có thể kích hoạt cảnh báo, ghi log hoặc làm sai lệch số liệu thống kê trên platform. Luôn luôn kiểm tra kỹ host đích và bắt buộc phải truyền cờ xác nhận `--yes`.
2. **Cảnh báo ghi đè file với TFTP:**
   Simulator [sim/tftp_sim.py](sim/tftp_sim.py) ở chế độ send sẽ đẩy file lên thư mục lưu trữ của TFTP server. Bắt buộc phải có cờ `--allow-write` và hãy đảm bảo tên file tải lên không ghi đè vào các file cấu hình quan trọng của hệ thống.
3. **Mật khẩu và Token mẫu:**
   Mọi token (`test_token_http`, `test_token_coap`), mật khẩu (`secret`, `guest`) trong [config/config.yaml](config/config.yaml) đều là giá trị ví dụ (mock). Trước khi kết nối vào platform thực tế, người vận hành bắt buộc phải thay thế bằng thông tin tài khoản và access token do platform cấp phát.
4. **Quy trình vận hành chuẩn khuyến nghị:**
   - Luôn chạy `python run_sims.py --dry-run` trước để kiểm tra danh sách lệnh và tham số.
   - Luôn chạy `python run_sims.py --mode connect` trước để xác minh toàn bộ các cổng dịch vụ đã sẵn sàng tiếp nhận kết nối.
   - Sau khi các cổng đã báo `OK`, mới tiến hành chạy `--mode send` để kiểm thử truyền nhận dữ liệu.
