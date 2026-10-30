"""

Runner Registry Module
Manages CLI definitions, capability discovery, and command-line construction.
"""

import json
import os
import re
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set, Tuple

CHECK_FLAGS = [
    "--host",
    "--port",
    "--timeout",
    "--count",
    "--interval",
    "--duration",
    "--server-uri",
    "--domain-id",
    "--transport",
    "--mode",
]

_FLAGS_CACHE: Dict[str, Set[str]] = {}


@dataclass
class CLIEntry:
    name: str
    script: str
    group: str
    grace: float
    extra_args: List[str] = field(default_factory=list)
    default_port: Optional[int] = None
    supported_flags: Set[str] = field(default_factory=set)


def get_default_registry() -> List[CLIEntry]:
    """
    Returns the standard 13-item registry for all 13 protocol targets
    (12 simulator CLIs, with tcp_udp split into tcp and udp).
    """
    return [
        CLIEntry(
            name="http",
            script="sim/http_sim.py",
            group="port8080",
            grace=5.0,
            default_port=8080,
        ),
        CLIEntry(
            name="mqtt",
            script="sim/mqtt_sim.py",
            group="port1883",
            grace=5.0,
            default_port=1883,
        ),
        CLIEntry(
            name="coap",
            script="sim/coap_sim.py",
            group="udp5683",
            grace=5.0,
            default_port=5683,
        ),
        CLIEntry(
            name="xmpp",
            script="sim/xmpp_sim.py",
            group="port5222",
            grace=5.0,
            default_port=5222,
        ),
        CLIEntry(
            name="amqp",
            script="sim/amqp_sim.py",
            group="port5672",
            grace=5.0,
            default_port=5672,
        ),
        CLIEntry(
            name="dds",
            script="sim/dds_sim.py",
            group="multicast",
            grace=5.0,
            default_port=None,
        ),
        CLIEntry(
            name="lwm2m",
            script="sim/lwm2m_sim.py",
            group="udp5683",
            grace=15.0,  # lwm2m >= 15s grace for Leshan Java client lifecycle
            default_port=5683,
        ),
        CLIEntry(
            name="lorawan",
            script="sim/lorawan_sim.py",
            group="port1700",
            grace=5.0,
            default_port=1700,
        ),
        CLIEntry(
            name="websocket",
            script="sim/websocket_sim.py",
            group="port8080",
            grace=5.0,
            default_port=8080,
        ),
        CLIEntry(
            name="tcp",
            script="sim/tcp_udp_sim.py",
            group="port9000",
            grace=5.0,
            extra_args=["--transport", "tcp"],
            default_port=9000,
        ),
        CLIEntry(
            name="udp",
            script="sim/tcp_udp_sim.py",
            group="port9001",
            grace=5.0,
            extra_args=["--transport", "udp"],
            default_port=9001,
        ),
        CLIEntry(
            name="tftp",
            script="sim/tftp_sim.py",
            group="port69",
            grace=5.0,
            default_port=69,
        ),
        CLIEntry(
            name="ftp",
            script="sim/ftp_sim.py",
            group="port21",
            grace=5.0,
            default_port=21,
        ),
    ]


def detect_supported_flags(python_exe: str, script_path: str, cwd: str = ".") -> Set[str]:
    """
    Runs `<cli> --help` once and caches the set of recognized flags.
    """
    abs_script = os.path.abspath(os.path.join(cwd, script_path))
    if abs_script in _FLAGS_CACHE:
        return _FLAGS_CACHE[abs_script]

    supported = set()
    if not os.path.exists(abs_script):
        _FLAGS_CACHE[abs_script] = supported
        return supported

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        proc = subprocess.run(
            [python_exe, abs_script, "--help"],
            capture_output=True,
            text=True,
            timeout=8,
            cwd=cwd,
            env=env,
            encoding="utf-8",
            errors="replace",
        )
        help_text = (proc.stdout or "") + " " + (proc.stderr or "")
        found_flags = set(re.findall(r"(--[a-zA-Z0-9-]+)", help_text))
        supported = found_flags
    except Exception:
        pass

    _FLAGS_CACHE[abs_script] = supported
    return supported


def parse_custom_args(arg_list: Optional[List[str]]) -> Dict[str, List[str]]:
    """
    Parses repeated --arg PROTO="<raw>" into {proto: [tokens]} using shlex.split.
    """
    parsed: Dict[str, List[str]] = {}
    if not arg_list:
        return parsed

    for item in arg_list:
        if "=" not in item:
            continue
        proto_key, raw_args = item.split("=", 1)
        proto_key = proto_key.strip().lower()
        try:
            tokens = shlex.split(raw_args.strip("\"' "))
        except Exception:
            tokens = raw_args.strip("\"' ").split()
        if proto_key not in parsed:
            parsed[proto_key] = []
        parsed[proto_key].extend(tokens)

    return parsed


def build_cli_command(
    entry: CLIEntry,
    args,
    config: dict,
    cwd: str = ".",
    custom_args_map: Optional[Dict[str, List[str]]] = None,
) -> Tuple[List[str], List[str]]:
    """
    Constructs the exact command list to invoke a CLI simulator,
    ensuring only supported flags are passed, and returning any notes.
    """
    python_exe = sys.executable
    supported = detect_supported_flags(python_exe, entry.script, cwd=cwd)
    entry.supported_flags = supported

    cmd: List[str] = [python_exe, entry.script]
    notes: List[str] = []

    # 1. Mode
    if "--mode" in supported:
        cmd.extend(["--mode", args.mode])

    # 2. Host & Port Resolution
    target_host = args.host if getattr(args, "host", None) else config.get("global", {}).get("target_host", "127.0.0.1")
    
    # Resolve Port from config.yaml adapters section or entry default
    adapters_cfg = config.get("adapters", {})
    proto_port = entry.default_port
    if entry.name in adapters_cfg and isinstance(adapters_cfg[entry.name], dict) and "port" in adapters_cfg[entry.name]:
        proto_port = adapters_cfg[entry.name]["port"]
    elif entry.name == "tcp" and "tcp_udp" in adapters_cfg and "tcp_port" in adapters_cfg["tcp_udp"]:
        proto_port = adapters_cfg["tcp_udp"]["tcp_port"]
    elif entry.name == "udp" and "tcp_udp" in adapters_cfg and "udp_port" in adapters_cfg["tcp_udp"]:
        proto_port = adapters_cfg["tcp_udp"]["udp_port"]

    if entry.name == "lwm2m":
        if "--server-uri" in supported:
            port_to_use = proto_port or 5683
            server_uri = f"coap://{target_host}:{port_to_use}"
            cmd.extend(["--server-uri", server_uri])
    else:
        if "--host" in supported:
            cmd.extend(["--host", target_host])
        else:
            if target_host != "127.0.0.1" or getattr(args, "host", None) or entry.name == "dds":
                notes.append("host ignored")

        if "--port" in supported and proto_port is not None:
            cmd.extend(["--port", str(proto_port)])

    # 3. Timeout
    if "--timeout" in supported:
        timeout_val = getattr(args, "timeout", 5.0)
        if args.mode == "connect" and timeout_val < 3.0:
            timeout_val = 3.0
        cmd.extend(["--timeout", str(timeout_val)])

    # 4. Count, Interval, Duration
    if getattr(args, "count", None) is not None and "--count" in supported:
        cmd.extend(["--count", str(args.count)])
    if getattr(args, "interval", None) is not None and "--interval" in supported:
        cmd.extend(["--interval", str(args.interval)])
    if getattr(args, "duration", None) is not None and "--duration" in supported:
        cmd.extend(["--duration", str(args.duration)])

    # 4.1 Safety write barrier for TFTP / FTP
    if entry.name in ("tftp", "ftp") and args.mode in ("send", "both"):
        if getattr(args, "allow_write", False) and "--allow-write" in supported:
            cmd.append("--allow-write")

    # 4.2 Negative test auth-case handling
    if getattr(args, "auth_case", "valid") == "wrong":
        neg = config.get("negative_cases", {})
        inv_token = neg.get("invalid_token", "INVALID_TOKEN_TEST_XYZ")
        inv_pwd = neg.get("invalid_password", "WRONG_PASSWORD_XYZ")
        if entry.name == "http" and "--header-value" in supported:
            cmd.extend(["--header-value", inv_token])
            notes.append("auth-case: wrong")
        elif entry.name == "websocket" and "--header-value" in supported:
            cmd.extend(["--header-value", inv_token])
            notes.append("auth-case: wrong")
        elif entry.name in ("mqtt", "ftp", "amqp", "xmpp") and "--password" in supported:
            cmd.extend(["--password", inv_pwd])
            notes.append("auth-case: wrong")
        elif entry.name == "coap" and "--token" in supported:
            cmd.extend(["--token", inv_token])
            notes.append("auth-case: wrong")

    # 5. Extra predefined args (e.g. --transport tcp)
    if entry.extra_args:
        cmd.extend(entry.extra_args)

    # 6. User raw custom args via --arg PROTO="<raw>"
    if custom_args_map and entry.name.lower() in custom_args_map:
        cmd.extend(custom_args_map[entry.name.lower()])

    return cmd, notes


def get_protocol_payload_info(
    proto: str,
    config: dict,
    target_host: str = "127.0.0.1",
    mode: str = "send",
    custom_args_map: Optional[Dict[str, List[str]]] = None,
) -> Dict[str, Any]:
    """
    Returns structured information about the message and payload sent by the simulator.
    """
    adapters = config.get("adapters", {})
    global_cfg = config.get("global", {})
    device_id = global_cfg.get("device_id", "sim_device_001")

    info: Dict[str, Any] = {
        "proto": proto,
        "mode": mode,
        "transport": "",
        "target": "",
        "format": "",
        "headers": {},
        "payload": None,
        "description": "",
    }

    if proto == "http":
        cfg = adapters.get("http", {})
        port = cfg.get("port", 8080)
        path = cfg.get("path", "/api/v1/telemetry")
        token = cfg.get("access_token", "test_token_http")
        auth_cfg = cfg.get("auth", {})
        info["transport"] = "HTTP POST (REST)"
        info["target"] = f"http://{target_host}:{port}{path}"

        h_key = auth_cfg.get("header_key", "")
        h_val = auth_cfg.get("header_value", "")
        if not h_val and auth_cfg.get("header_value_env"):
            h_val = os.environ.get(auth_cfg["header_value_env"], "")

        if custom_args_map and "http" in custom_args_map:
            tokens = custom_args_map["http"]
            for i, tok in enumerate(tokens):
                if tok == "--header-key" and i + 1 < len(tokens):
                    h_key = tokens[i + 1]
                elif tok.startswith("--header-key="):
                    h_key = tok[13:]
                elif tok == "--header-value" and i + 1 < len(tokens):
                    h_val = tokens[i + 1]
                elif tok.startswith("--header-value="):
                    h_val = tok[15:]
                elif tok == "--token" and i + 1 < len(tokens):
                    token = tokens[i + 1]
                elif tok.startswith("--token="):
                    token = tok[8:]

        headers = {"Content-Type": "application/json"}
        if h_key and h_val:
            headers[h_key] = h_val
        else:
            headers["X-Authorization"] = f"Bearer {token}"

        extra = auth_cfg.get("extra_headers", {})
        if isinstance(extra, dict):
            headers.update(extra)

        if custom_args_map and "http" in custom_args_map:
            tokens = custom_args_map["http"]
            for i, tok in enumerate(tokens):
                if tok == "--header" and i + 1 < len(tokens):
                    kv = tokens[i + 1]
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        headers[k.strip('"\',` ')] = v.strip('"\',` ')
                elif tok.startswith("--header="):
                    kv = tok[9:]
                    if "=" in kv:
                        k, v = kv.split("=", 1)
                        headers[k.strip('"\',` ')] = v.strip('"\',` ')

        info["headers"] = headers
        info["format"] = "JSON"
        try:
            from sim.common import load_payload_config, generate_payload
            p_cfg = load_payload_config("http")
            if p_cfg and "attributes" in p_cfg:
                d_id = p_cfg.get("device_ids", [device_id])[0]
                info["payload"] = generate_payload(p_cfg["attributes"], device_id=d_id, seq=1)
            else:
                info["payload"] = {
                    "device_id": device_id,
                    "seq": 1,
                    "temperature": 25.5,
                    "humidity": 59.8,
                    "timestamp": 1793156222891,
                }
        except Exception:
            info["payload"] = {
                "device_id": device_id,
                "seq": 1,
                "temperature": 25.5,
                "humidity": 59.8,
                "timestamp": 1793156222891,
            }
        info["description"] = "Bản tin HTTP POST gửi dữ liệu cảm biến dạng JSON tới platform REST API"

    elif proto == "mqtt":
        cfg = adapters.get("mqtt", {})
        port = cfg.get("port", 1883)
        topic = cfg.get("topic", "v1/devices/me/telemetry")
        info["transport"] = "MQTT PUBLISH (QoS 1)"
        info["target"] = f"mqtt://{target_host}:{port} -> topic: '{topic}'"
        info["headers"] = {"client_id": cfg.get("client_id", "iot_test_client_001"), "qos": cfg.get("qos", 1)}
        info["format"] = "JSON"
        try:
            from sim.common import load_payload_config, generate_payload
            p_cfg = load_payload_config("mqtt")
            if p_cfg and "attributes" in p_cfg:
                d_id = p_cfg.get("device_ids", [device_id])[0]
                info["payload"] = generate_payload(p_cfg["attributes"], device_id=d_id, seq=1)
            else:
                info["payload"] = {
                    "device_id": device_id,
                    "seq": 1,
                    "temperature": 26.8,
                    "humidity": 54.9,
                    "status": "ONLINE",
                    "timestamp": 1793156222891,
                }
        except Exception:
            info["payload"] = {
                "device_id": device_id,
                "seq": 1,
                "temperature": 26.8,
                "humidity": 54.9,
                "status": "ONLINE",
                "timestamp": 1793156222891,
            }
        info["description"] = "Bản tin MQTT Publish telemetry dạng JSON vào telemetry topic"

    elif proto == "coap":
        cfg = adapters.get("coap", {})
        port = cfg.get("port", 5683)
        path = cfg.get("path", "api/v1/telemetry")
        info["transport"] = "CoAP POST (CON - Confirmable)"
        info["target"] = f"coap://{target_host}:{port}/{path}"
        info["headers"] = {"code": "0.02 (POST)", "type": "CON", "token": cfg.get("access_token", "test_token_coap")}
        info["format"] = "JSON (encoded UTF-8 bytes)"
        try:
            from sim.common import load_payload_config, generate_payload
            p_cfg = load_payload_config("coap")
            if p_cfg and "attributes" in p_cfg:
                d_id = p_cfg.get("device_ids", [device_id])[0]
                info["payload"] = generate_payload(p_cfg["attributes"], device_id=d_id, seq=1)
            else:
                info["payload"] = {
                    "device_id": device_id,
                    "seq": 1,
                    "temperature": 23.8,
                    "humidity": 64.7,
                    "timestamp": 1793156222891,
                }
        except Exception:
            info["payload"] = {
                "device_id": device_id,
                "seq": 1,
                "temperature": 23.8,
                "humidity": 64.7,
                "timestamp": 1793156222891,
            }
        info["description"] = "Bản tin CoAP POST CON mang dữ liệu telemetry dạng JSON"

    elif proto == "xmpp":
        cfg = adapters.get("xmpp", {})
        port = cfg.get("port", 5222)
        target_jid = cfg.get("target_adapter_jid", "adapter@iot.local")
        info["transport"] = "XMPP Chat Message Stanza"
        info["target"] = f"xmpp://{target_host}:{port} -> to: '{target_jid}'"
        info["headers"] = {"type": "chat", "from": cfg.get("client_jid", "sim_device_001@iot.local")}
        info["format"] = "JSON trong body của XMPP stanza"
        info["payload"] = {
            "device_id": device_id,
            "seq": 1,
            "temperature": 24.2,
            "humidity": 57.5,
            "timestamp": 1793156222891,
        }
        info["description"] = "Bản tin XMPP chat message stanza gửi telemetry JSON tới adapter JID"

    elif proto == "amqp":
        cfg = adapters.get("amqp", {})
        port = cfg.get("port", 5672)
        exchange = cfg.get("exchange", "iot.telemetry")
        routing_key = cfg.get("routing_key", "device.telemetry")
        info["transport"] = "AMQP 0-9-1 basic_publish"
        info["target"] = f"amqp://{target_host}:{port}/ -> exchange: '{exchange}', routing_key: '{routing_key}'"
        info["headers"] = {"content_type": "application/json", "delivery_mode": 2, "headers": {"device_id": device_id}}
        info["format"] = "JSON"
        info["payload"] = {
            "device_id": device_id,
            "seq": 1,
            "temperature": 23.2,
            "humidity": 61.7,
            "timestamp": 1793156222891,
        }
        info["description"] = "Bản tin AMQP basic_publish tới exchange và routing key của platform"

    elif proto == "dds":
        cfg = adapters.get("dds", {})
        domain_id = cfg.get("domain_id", 0)
        topic = cfg.get("topic_name", "IoTTelemetryTopic")
        info["transport"] = "CycloneDDS RTPS (Multicast)"
        info["target"] = f"DDS Domain {domain_id} -> topic: '{topic}'"
        info["headers"] = {"schema": "payloads/telemetry_schema.idl", "struct": "IoT::Telemetry"}
        info["format"] = "DDS CDR Binary (OMG IDL 4.2)"
        info["payload"] = {
            "device_id": device_id,
            "timestamp": 1793156222891,
            "temperature": 25.5,
            "humidity": 49.8,
            "status": "NORMAL",
        }
        info["description"] = "Mẫu dữ liệu nhị phân CDR ghi qua CycloneDDS DataWriter"

    elif proto == "lwm2m":
        cfg = adapters.get("lwm2m", {})
        port = cfg.get("port", 5683)
        endpoint = cfg.get("endpoint_name", "lwm2m_sim_device_001")
        info["transport"] = "OMA LwM2M v1.0/v1.1 (CoAP UDP)"
        info["target"] = f"coap://{target_host}:{port} -> endpoint: '{endpoint}'"
        info["headers"] = {"query": f"ep={endpoint}&lt=60&b=U", "link_format": "</1/0>,</2/0>,</3/0>,</3303/0>"}
        info["format"] = "OMA IPSO Smart Objects (TLV / Plain Text)"
        info["payload"] = {
            "Object_3_Device": {"Manufacturer": "Eclipse Leshan", "Model": "Test Harness Device", "Firmware": "1.0.0"},
            "Object_3303_Temperature": {"Resource_5700_SensorValue": 25.5, "Resource_5701_Unit": "Cel"},
        }
        info["description"] = "Bản tin đăng ký LwM2M CoAP POST Registration và các OMA IPSO Objects"

    elif proto == "lorawan":
        cfg = adapters.get("lorawan", {})
        port = cfg.get("port", 1700)
        gw_eui = cfg.get("gateway_eui", "AA555A0000000101")
        info["transport"] = "Semtech UDP Packet Forwarder v2"
        info["target"] = f"udp://{target_host}:{port} -> Gateway EUI: {gw_eui}"
        info["headers"] = {"version": "0x02", "identifier": "0x00 (PUSH_DATA)", "gateway_eui": gw_eui}
        info["format"] = "12-byte Binary Header + JSON rxpk (Base64 data)"
        info["payload"] = {
            "rxpk": [
                {
                    "time": "2026-10-28T03:55:00.000000Z",
                    "tmst": 12345678,
                    "freq": 868.1,
                    "datr": "SF7BW125",
                    "rssi": -65,
                    "size": 33,
                    "data": "gAEAAACAAAAABBEPAQIDBAUGBwgJCgsMDQ==",
                }
            ]
        }
        info["description"] = "Gói tin Semtech UDP PUSH_DATA chứa payload cảm biến mã hóa Base64"

    elif proto == "websocket":
        cfg = adapters.get("websocket", {})
        port = cfg.get("port", 8080)
        path = cfg.get("path", "/ws/telemetry")
        info["transport"] = "WebSocket Text Frame (RFC 6455)"
        info["target"] = f"ws://{target_host}:{port}{path}"
        info["headers"] = {"Upgrade": "websocket", "Connection": "Upgrade"}
        info["format"] = "JSON"
        try:
            from sim.common import load_payload_config, generate_payload
            p_cfg = load_payload_config("websocket")
            if p_cfg and "attributes" in p_cfg:
                d_id = p_cfg.get("device_ids", [device_id])[0]
                info["payload"] = generate_payload(p_cfg["attributes"], device_id=d_id, seq=1)
            else:
                info["payload"] = {
                    "device_id": device_id,
                    "seq": 1,
                    "temperature": 25.7,
                    "humidity": 51.6,
                    "timestamp": 1793156222891,
                }
        except Exception:
            info["payload"] = {
                "device_id": device_id,
                "seq": 1,
                "temperature": 25.7,
                "humidity": 51.6,
                "timestamp": 1793156222891,
            }
        info["description"] = "Khung truyền WebSocket text frame gửi telemetry dạng JSON"

    elif proto == "tcp":
        cfg = adapters.get("tcp_udp", {})
        port = cfg.get("tcp_port", 9000)
        info["transport"] = "Raw TCP Stream Socket"
        info["target"] = f"tcp://{target_host}:{port}"
        info["headers"] = {"delimiter": "\\n (Line feed)"}
        info["format"] = "JSON + Newline"
        info["payload"] = {
            "device_id": device_id,
            "proto": "TCP",
            "seq": 1,
            "temperature": 25.1,
            "timestamp": 1793156222891,
        }
        info["description"] = "Chuỗi JSON kết thúc bằng dấu xuống dòng gửi qua raw TCP socket"

    elif proto == "udp":
        cfg = adapters.get("tcp_udp", {})
        port = cfg.get("udp_port", 9001)
        info["transport"] = "Raw UDP Datagram Socket"
        info["target"] = f"udp://{target_host}:{port}"
        info["headers"] = {"transport": "UDP datagram", "encoding": "UTF-8"}
        info["format"] = "JSON Datagram"
        info["payload"] = {
            "device_id": device_id,
            "proto": "UDP",
            "seq": 1,
            "temperature": 26.2,
            "timestamp": 1793156222891,
        }
        info["description"] = "Gói tin datagram JSON gửi trực tiếp qua raw UDP socket"

    elif proto == "tftp":
        cfg = adapters.get("tftp", {})
        port = cfg.get("port", 69)
        file_path = cfg.get("local_upload_file", "payloads/sample_tftp_file.txt")
        info["transport"] = "TFTP Protocol (RFC 1350)"
        info["target"] = f"udp://{target_host}:{port} -> file: '{cfg.get('remote_filename', 'device_config_test.txt')}'"
        info["headers"] = {"block_size": cfg.get("block_size", 512), "source_file": file_path}
        info["format"] = "TFTP 512B Blocks (File INI Config)"
        info["payload"] = {
            "[device]": {"device_id": device_id, "firmware": "3.2.1"},
            "[sensors]": {"temperature.unit": "celsius", "humidity.unit": "percent"},
            "[telemetry]": {"report_interval_sec": 30, "topic": "v1/devices/me/telemetry"},
        }
        info["description"] = "File cấu hình thiết bị mẫu định dạng INI truyền qua các khối TFTP 512B"

    elif proto == "ftp":
        cfg = adapters.get("ftp", {})
        port = cfg.get("port", 21)
        remote_file = cfg.get("remote_filename", "device_telemetry_test.txt")
        local_file = cfg.get("local_upload_file", "payloads/sample_tftp_file.txt")
        user = cfg.get("username", "anonymous")
        pwd = cfg.get("password", "secret")
        info["transport"] = "FTP Protocol (RFC 959)"
        info["target"] = f"ftp://{target_host}:{port} -> remote file: '{remote_file}'"
        info["headers"] = {
            "user": user,
            "password": pwd if pwd else "(none)",
            "mode": "passive",
            "local_file": local_file,
        }
        info["format"] = "Binary / Text Stream (STOR upload)"
        info["payload"] = {
            "device_id": device_id,
            "upload_file": remote_file,
            "sample_content": "device_id=sim_device_001;temperature=25.5;humidity=60.0",
            "timestamp": 1793156222891,
        }
        info["description"] = "File dữ liệu telemetry mẫu tải lên FTP Server qua lệnh STOR"

    return info


def print_payload_info(
    proto: str,
    config: dict,
    target_host: str = "127.0.0.1",
    mode: str = "send",
    redactor: Optional[Any] = None,
    custom_args_map: Optional[Dict[str, List[str]]] = None,
) -> None:
    """
    Prints formatted message and payload structure to console.
    """
    info = get_protocol_payload_info(proto, config, target_host, mode, custom_args_map=custom_args_map)
    if redactor:
        info = redactor.redact_data(info)

    print(f"  +--- [BẢN TIN {proto.upper()}] ---")
    if mode == "connect":
        print("  | [LƯU Ý]: (chế độ connect không gửi bản tin; đây là bản tin MẪU sẽ gửi ở chế độ send/both)")
    print(f"  | Vận chuyển : {info['transport']}")
    print(f"  | Đích đến   : {info['target']}")
    if info.get("headers"):
        hdr_str = ", ".join(f"{k}: {v}" for k, v in info["headers"].items())
        print(f"  | Headers/Meta: {hdr_str}")
    print(f"  | Định dạng  : {info['format']}")
    if info.get("payload"):
        payload_json = json.dumps(info["payload"], indent=2, ensure_ascii=False)
        indented = "\n".join("  |   " + line for line in payload_json.splitlines())
        print(f"  | Cấu trúc bản tin mẫu:")
        print(indented)
    print(f"  +-------------------------------------------------------------")

