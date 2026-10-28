# Binary Tools & Version Registry

## 1. Eclipse Leshan LwM2M Client Demo

| Thuộc tính | Chi tiết thực tế |
|---|---|
| **Tên file** | `bin/leshan-client-demo.jar` |
| **Artifact** | `org.eclipse.leshan:leshan-demo-client` |
| **Phiên bản** | `2.0.0-M17` (Milestone 17) |
| **URL nguồn** | `https://repo1.maven.org/maven2/org/eclipse/leshan/leshan-demo-client/2.0.0-M17/leshan-demo-client-2.0.0-M17-jar-with-dependencies.jar` |
| **Dung lượng** | `8,113,629 bytes` (~7.74 MB) |
| **SHA-256 Checksum** | `9ceab2937b3a2a0e9022b80289f2511b3b35edaef4b81760825672d3ecb7f8b4` |
| **Yêu cầu Java** | Tối thiểu Java 11 (`Build-Jdk-Spec: 11` trong MANIFEST.MF). Đã kiểm thử thành công trên Java 22.0.2 |
| **Main Class** | `org.eclipse.leshan.demo.client.LeshanClientDemo` |
| **Giao thức hỗ trợ** | LwM2M v1.0 & v1.1 qua CoAP (UDP port 5683), CoAP/DTLS (UDP port 5684), CoAP/TCP |

### Chuỗi log thực tế khi vận hành (Verified from Bytecode & Runtime)

#### A. Log thực tế thu được khi chạy thử client vào địa chỉ trong `config.yaml` (`coap://127.0.0.1:5683`):
Lệnh chạy: `java -jar bin/leshan-client-demo.jar -u coap://127.0.0.1:5683 -n lwm2m_sim_device_001` (bắt 7 giây timeout khi server chưa bật):
```text
Leshan Client Demo Interactive Console :

Commands:
 help Display help information about the specified command.
 list List available Objects, Instances and Resources
 create Enable a new Object
 delete Disable a new object
 update Trigger a registration update.
 send Send data to server
 collect Collect data to send it later with 'send' command
 move Simulate client mouvement.

Press Ctl-C to exit.

 LeshanClient [INFO] Starting Leshan client ... 
oapOscoreClientEndpointFactory [WARN] Experimental OSCORE feature is enabled. 
 DefaultEndpointsManager [INFO] New CoAP over UDP endpoint based on Californium library with very experimental support of OSCORE created, for server coap://127.0.0.1:5683 at coap://[0:0:0:0:0:0:0:0]:55352. 
 LeshanClient [INFO] Leshan client[endpoint:lwm2m_sim_device_001] started. 
 DefaultRegistrationEngine [INFO] Trying to register to coap://127.0.0.1:5683 ... 
```
*Kết quả khi server chưa chạy:* Client dừng ở bước `Trying to register to coap://127.0.0.1:5683 ...` và chờ retransmit CoAP CON.

#### B. Log thực tế thu được khi kết nối vào Leshan Server Demo (Cổng 5683, web 8888):
Khi khởi chạy `bin/leshan-server-demo.jar` trên web port 8888 và CoAP port 5683, client `lwm2m_sim_device_001` kết nối thành công:
- **Đăng ký THÀNH CÔNG thực tế (Xác thực trên web UI và runtime log):**
 ```text
 [INFO] Registered with location '/rd/hsYyRDhF5S'
 ```
 *Registration ID:* `hsYyRDhF5S` (Location `/rd/hsYyRDhF5S`). Sau khi đăng ký thành công, client gửi bản tin Update định kỳ (`trigger a registration update`) tới server.
- **Trạng thái DTLS (Cổng 5684):** `ĐÃ CÀI, CHƯA XÁC MINH`. Do chưa cấu hình keystore / PSK credential đồng bộ giữa server và client trong thời gian kiểm thử giới hạn, chưa thực hiện bắt tay DTLS thực tế.

---

## 2. Eclipse Leshan LwM2M Server Demo

| Thuộc tính | Chi tiết thực tế |
|---|---|
| **Tên file** | `bin/leshan-server-demo.jar` |
| **Artifact** | `org.eclipse.leshan:leshan-demo-server` |
| **Phiên bản** | `2.0.0-M17` (Milestone 17) |
| **URL nguồn** | `https://repo1.maven.org/maven2/org/eclipse/leshan/leshan-demo-server/2.0.0-M17/leshan-demo-server-2.0.0-M17-jar-with-dependencies.jar` |
| **Dung lượng** | `16,197,756 bytes` (~15.4 MB) |
| **SHA-256 Checksum** | `0eb7f583376d6547f6214c836ec5e172c9ee46f6cb4e98684bcaf33e49b64f3a` |
| **Yêu cầu Java** | Tối thiểu Java 11. Đã kiểm thử thành công trên Java 22.0.2 |
| **Cổng mặc định** | CoAP UDP: 5683, CoAP DTLS: 5684, Web Console: `-wp <port>` (mặc định 8080, chuyển sang 8888 để tránh xung đột) |

---

## 3. LoRaWAN Forwarder Tooling

| Thuộc tính | Chi tiết thực tế |
|---|---|
| **Tên file** | `lorawan_udp_forwarder.py` |
| **Loại công cụ** | Pure Python Semtech UDP Gateway Forwarder Simulator |
| **Chuẩn giao thức** | Semtech UDP Packet Forwarder Protocol v2 (`PROTOCOL_VERSION = 0x02`) |
| **Header định dạng** | 12 bytes: `[0x02, Token_MSB, Token_LSB, 0x00 (PUSH_DATA), GW_EUI_8B]` |
| **Phản hồi mong đợi** | 4 bytes `PUSH_ACK`: `[0x02, Token_MSB, Token_LSB, 0x01]` |
| **Docker Alternative** | `ghcr.io/unict-arslab/lwn-simulator:latest` (profile `lorawan-sim` trong `docker-compose.yml`) |
