#!/usr/bin/env python3
"""
MQTT IoT Protocol Simulator CLI
Protocol #2: MQTT Client (paho-mqtt CallbackAPIVersion.VERSION2)
Usage:
  python sim/mqtt_sim.py --mode connect
  python sim/mqtt_sim.py --mode connect --tls --insecure
  python sim/mqtt_sim.py --mode both --count 3 --duration 10
  python sim/mqtt_sim.py --mode send --count 5
  python sim/mqtt_sim.py --mode listen --duration 15
"""

import os
import sys
import ssl
import time
import json
import logging
import argparse
from datetime import datetime

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if base_dir not in sys.path:
    sys.path.insert(0, base_dir)

from sim.common import (
    EXIT_OK, EXIT_PROTO_ERROR, EXIT_REFUSED, EXIT_TIMEOUT, EXIT_AUTH_FAILED,
    STATUS_OK, STATUS_REFUSED, STATUS_TIMEOUT, STATUS_AUTH_FAILED, STATUS_PROTO_ERROR,
    format_result, classify_socket_error, tcp_preflight_probe, setup_simulator_logger
)

try:
    import yaml
except ImportError:
    yaml = None

try:
    import paho.mqtt.client as mqtt
except ImportError:
    print("[ERROR] paho-mqtt not installed. Install with: pip install paho-mqtt", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 1883,
        "port_tls": 8883,
        "topic": "v1/devices/me/telemetry",
        "username": "sim_device_001",
        "password": "secret",
        "client_id": "iot_sim_mqtt_001",
        "qos": 1,
        "keepalive": 60,
        "tls_enabled": False,
        "insecure_skip_verify": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                mq = raw.get("adapters", {}).get("mqtt", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = mq.get("port", cfg["port"])
                cfg["port_tls"] = mq.get("port_tls", tls.get("ports", {}).get("mqtt_tls", cfg["port_tls"]))
                cfg["topic"] = mq.get("topic", cfg["topic"])
                cfg["username"] = mq.get("username", cfg["username"])
                cfg["password"] = mq.get("password", cfg["password"])
                cfg["client_id"] = mq.get("client_id", cfg["client_id"])
                cfg["qos"] = mq.get("qos", cfg["qos"])
                cfg["keepalive"] = mq.get("keepalive", cfg["keepalive"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                cfg["insecure_skip_verify"] = tls.get("insecure_skip_verify", cfg["insecure_skip_verify"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("mqtt", args=args)

def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="MQTT IoT Protocol Simulator CLI (paho-mqtt CallbackAPIVersion.VERSION2)",
        epilog="Chế độ 'connect' chỉ gửi CONNECT và chờ CONNACK. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"Broker host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="Broker port (default: 1883 or 8883 if --tls)")
    parser.add_argument("--topic", default=cfg["topic"], help=f"Telemetry publish topic (default: {cfg['topic']})")
    parser.add_argument("--username", default=cfg["username"], help="MQTT username / device token")
    parser.add_argument("--password", default=cfg["password"], help="MQTT password")
    parser.add_argument("--client-id", default=cfg["client_id"], help="MQTT Client ID")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=cfg["qos"], help="QoS level (default: 1)")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable MQTT TLS")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False),
                        help="Skip TLS certificate verification (insecure)")
    parser.add_argument("--count", type=int, default=1, help="Telemetry count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listening duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Connection timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    logger.info(f"[*] Starting MQTT Simulator in '{args.mode}' mode (TLS: {args.tls})...")
    logger.info(f"    Broker: {args.host}:{args.port} | Client ID: {args.client_id} | Username: {args.username}")

    connected = False
    conn_rc = None
    disconnected_before_connack = False
    disconnect_reason = None
    messages_received = []

    # CallbackAPIVersion.VERSION2
    client = mqtt.Client(
        callback_api_version=mqtt.CallbackAPIVersion.VERSION2,
        client_id=args.client_id
    )

    if args.tls:
        if args.insecure:
            logger.warning("[!] WARNING: TLS certificate verification is disabled (--insecure)")
            client.tls_set(cert_reqs=ssl.CERT_NONE)
            client.tls_insecure_set(True)
        else:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    if args.username:
        client.username_pw_set(args.username, args.password)

    def on_connect(client, userdata, flags, reason_code, properties):
        nonlocal connected, conn_rc
        conn_rc = reason_code
        logger.info(f"[{datetime.now().isoformat()}] <--- Raw CONNACK: reason_code={reason_code} ({reason_code.getName() if hasattr(reason_code, 'getName') else reason_code})")
        if reason_code == 0:
            connected = True
            if args.mode in ["listen", "both"]:
                sub_topics = [
                    ("v1/devices/me/attributes", args.qos),
                    ("v1/devices/me/rpc/request/+", args.qos),
                    ("v1/devices/me/downlink", args.qos)
                ]
                for st, q in sub_topics:
                    res, mid = client.subscribe(st, qos=q)
                    logger.info(f"[{datetime.now().isoformat()}] ---> Subscribing to: '{st}' (QoS {q}) | MID: {mid}")
        else:
            logger.error(f"[!] MQTT connection rejected with code: {reason_code}")

    def on_disconnect(client, userdata, flags, reason_code, properties):
        nonlocal disconnected_before_connack, disconnect_reason
        if conn_rc is None:
            disconnected_before_connack = True
            disconnect_reason = reason_code
            logger.warning(f"[{datetime.now().isoformat()}] <--- Socket closed before CONNACK: {reason_code}")
        else:
            logger.debug(f"[{datetime.now().isoformat()}] <--- Disconnected: {reason_code}")

    def on_publish(client, userdata, mid, reason_code, properties):
        logger.info(f"[{datetime.now().isoformat()}] <--- Raw PUBACK received: MID={mid}, RC={reason_code}")

    def on_message(client, userdata, msg):
        topic = msg.topic
        payload_raw = msg.payload
        try:
            payload_str = payload_raw.decode("utf-8")
        except UnicodeDecodeError:
            payload_str = payload_raw.hex()

        logger.info(f"[{datetime.now().isoformat()}] <=== [DOWNLINK MESSAGE] Topic: '{topic}', QoS: {msg.qos}, Retain: {msg.retain}")
        logger.info(f"     Raw Payload: {payload_str}")
        messages_received.append((topic, payload_str))

        if topic.startswith("v1/devices/me/rpc/request/"):
            req_id = topic.split("/")[-1]
            resp_topic = f"v1/devices/me/rpc/response/{req_id}"
            reply_payload = json.dumps({"result": "ok", "status": "executed", "timestamp": int(time.time()*1000)})
            r_info = client.publish(resp_topic, reply_payload, qos=args.qos)
            logger.info(f"[{datetime.now().isoformat()}] ===> [AUTO RPC REPLY] Topic: '{resp_topic}' | Reply: {reply_payload} | MID: {r_info.mid}")

    client.on_connect = on_connect
    client.on_disconnect = on_disconnect
    client.on_publish = on_publish
    client.on_message = on_message

    # Pre-flight TCP probe for consistent timing, elapsed reporting, and RST detection
    is_connected, probe_status, probe_detail, probe_elapsed = tcp_preflight_probe(
        args.host, args.port, timeout=args.timeout
    )
    if not is_connected:
        logger.error(f"[!] TCP connection failed to {args.host}:{args.port}: {probe_detail}")
        return format_result("mqtt", args.mode, probe_status, probe_detail, elapsed=probe_elapsed)

    try:
        client.connect(args.host, args.port, keepalive=cfg.get("keepalive", 60))
        client.loop_start()
    except ssl.SSLError as se:
        logger.error(f"[!] TLS handshake error: {se}")
        return format_result("mqtt", args.mode, STATUS_AUTH_FAILED, f"SSLError: {se}", stage="tls_handshake")
    except (ConnectionRefusedError, OSError) as e:
        status, detail, code = classify_socket_error(e, stage="tcp_connect")
        logger.error(f"[!] Socket connection error to {args.host}:{args.port}: {e}")
        return format_result("mqtt", args.mode, status, detail)
    except Exception as e:
        logger.error(f"[!] Unexpected error during MQTT connect: {e}")
        return format_result("mqtt", args.mode, STATUS_PROTO_ERROR, f"Unexpected error: {e}", stage="tcp_connect")

    # Wait for CONNACK or early disconnection
    t0 = time.time()
    while conn_rc is None and not disconnected_before_connack and (time.time() - t0 < args.timeout):
        time.sleep(0.05)

    if conn_rc is None:
        client.loop_stop()
        if disconnected_before_connack:
            logger.error("[!] Broker closed connection immediately after CONNECT without CONNACK.")
            return format_result("mqtt", args.mode, STATUS_PROTO_ERROR,
                                 f"Broker closed connection immediately after CONNECT without CONNACK (disconnect_rc={disconnect_reason}, possible causes: authentication/ACL rejected, client ID rejected, TLS required, protocol mismatch)",
                                 stage="handshake")
        else:
            logger.error(f"[!] MQTT Connection timeout after {args.timeout}s waiting for CONNACK.")
            return format_result("mqtt", args.mode, STATUS_TIMEOUT,
                                 f"Timeout waiting for CONNACK after {args.timeout}s",
                                 stage="handshake")

    if hasattr(conn_rc, "value"):
        rc_val = int(conn_rc.value)
    else:
        try:
            rc_val = int(conn_rc)
        except Exception:
            rc_val = 0 if str(conn_rc).lower() in ("success", "0") else 1
    rc_name = conn_rc.getName() if hasattr(conn_rc, "getName") else str(conn_rc)

    if rc_val != 0:
        client.loop_stop()
        # Bad Username/Password (4) or Not Authorized (5) for v3.1.1; 134/135 (0x86/0x87) for v5
        if rc_val in (4, 5, 134, 135):
            return format_result("mqtt", args.mode, STATUS_AUTH_FAILED,
                                 f"CONNACK rc={rc_val} ({rc_name})", stage="auth")
        elif rc_val in (1, 2, 130, 132, 133):
            return format_result("mqtt", args.mode, STATUS_PROTO_ERROR,
                                 f"CONNACK rc={rc_val} ({rc_name})", stage="handshake")
        else:
            return format_result("mqtt", args.mode, STATUS_REFUSED,
                                 f"CONNACK rc={rc_val} ({rc_name})", stage="handshake")

    # Mode: CONNECT only
    if args.mode == "connect":
        client.loop_stop()
        client.disconnect()
        logger.info("[*] MQTT handshake successful. Exiting connect mode.")
        return format_result("mqtt", "connect", STATUS_OK, "CONNACK rc=0 (Success)", stage="handshake")

    # Mode: SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Publishing {args.count} telemetry message(s) to topic '{args.topic}' with QoS {args.qos}...")
        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.client_id,
                "seq": i,
                "temperature": round(26.5 + i * 0.3, 2),
                "humidity": round(55.0 - i * 0.1, 2),
                "status": "ONLINE",
                "timestamp": int(time.time() * 1000)
            }
            body = json.dumps(telemetry)
            info = client.publish(args.topic, body, qos=args.qos)
            logger.info(f"[{datetime.now().isoformat()}] ---> PUBLISH topic='{args.topic}' | MID={info.mid} | QoS={args.qos} | Payload: {body}")
            info.wait_for_publish(timeout=args.timeout)
            if not info.is_published():
                logger.warning(f"[!] PUBACK not received within {args.timeout}s for MID {info.mid}")
            if i < args.count:
                time.sleep(args.interval)

    # Mode: LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Listening for downlinks for {args.duration}s...")
        t_start = time.time()
        while time.time() - t_start < args.duration:
            time.sleep(0.5)

        logger.info(f"[*] Listen phase ended. Total messages received: {len(messages_received)}")
        if len(messages_received) == 0:
            logger.info(f"[*] Khong nhan duoc ban tin downlink nao sau {args.duration} giay (Platform chua gui downlink).")

    client.loop_stop()
    client.disconnect()
    logger.info("[*] MQTT Simulator finished cleanly.")
    if args.mode == "listen":
        if len(messages_received) == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{len(messages_received)} message(s) received"
    elif args.mode == "both":
        if len(messages_received) == 0:
            detail = f"Sent:{args.count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{args.count}, Recv:{len(messages_received)}"
    else:
        detail = "Completed cleanly"
    return format_result("mqtt", args.mode, STATUS_OK, detail, stage="channel")


if __name__ == "__main__":
    sys.exit(main())
