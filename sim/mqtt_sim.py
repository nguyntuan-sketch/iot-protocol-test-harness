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
import threading
from typing import Tuple, List, Optional, Dict
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
    format_result, classify_socket_error, tcp_preflight_probe, setup_simulator_logger,
    load_merged_config, resolve_password_from_file,
    load_payload_config, generate_payload, run_devices_parallel
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
    raw = load_merged_config(base_dir)
    target = raw.get("global", {})
    mq = raw.get("adapters", {}).get("mqtt", {})
    tls = raw.get("tls", {})
    auth_cfg = mq.get("auth", {})

    cfg = {
        "host": target.get("target_host", "127.0.0.1"),
        "port": mq.get("port", 1883),
        "port_tls": mq.get("port_tls", tls.get("ports", {}).get("mqtt_tls", 8883)),
        "topic": mq.get("topic", "v1/devices/me/telemetry"),
        "username": auth_cfg.get("username") or mq.get("username", "sim_device_001"),
        "password": auth_cfg.get("password") or mq.get("password", "secret"),
        "username_env": auth_cfg.get("username_env", ""),
        "password_env": auth_cfg.get("password_env", ""),
        "password_file": auth_cfg.get("password_file", ""),
        "client_id": mq.get("client_id", "iot_test_client_001"),
        "qos": mq.get("qos", 1),
        "keepalive": mq.get("keepalive", 60),
        "tls_enabled": tls.get("enabled", False),
        "insecure_skip_verify": tls.get("insecure_skip_verify", False),
        "ca_cert": tls.get("ca_cert_path", "")
    }
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
    parser.add_argument("--username", default=None, help="MQTT username / device token (priority: CLI > env > config)")
    parser.add_argument("--username-env", default=None, help="Environment variable name for MQTT username")
    parser.add_argument("--password", default=None, help="MQTT password (priority: CLI > env > file > config)")
    parser.add_argument("--password-env", default=None, help="Environment variable name for MQTT password")
    parser.add_argument("--password-file", default=None, help="Path to text file containing MQTT password")
    parser.add_argument("--client-id", default=cfg["client_id"], help="MQTT Client ID")
    parser.add_argument("--qos", type=int, choices=[0, 1, 2], default=cfg["qos"], help="QoS level (default: 1)")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable MQTT TLS")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False),
                        help="Skip TLS certificate verification (insecure)")
    parser.add_argument("--ca-cert", default=cfg.get("ca_cert", None), help="Path to CA certificate for TLS verification")
    parser.add_argument("--count", type=int, default=None, help="Telemetry count to send (default: from payload config or 1)")
    parser.add_argument("--interval", type=float, default=None, help="Interval between sends in seconds (default: from payload config or 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listening duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Connection timeout in seconds (default: 3.0)")

    args = parser.parse_args()

    # Resolve Username: CLI > env > config.auth > config.username
    resolved_username = None
    if args.username is not None:
        resolved_username = args.username
    elif args.username_env:
        resolved_username = os.environ.get(args.username_env, "")
    elif cfg["username_env"]:
        resolved_username = os.environ.get(cfg["username_env"], "")
    else:
        resolved_username = cfg["username"]

    # Resolve Password: CLI > env > file > config.auth > config.password
    resolved_password = None
    if args.password is not None:
        resolved_password = args.password
    elif args.password_env:
        resolved_password = os.environ.get(args.password_env, "")
    elif args.password_file:
        resolved_password = resolve_password_from_file(args.password_file) or ""
    elif cfg["password_env"]:
        resolved_password = os.environ.get(cfg["password_env"], "")
    elif cfg["password_file"]:
        resolved_password = resolve_password_from_file(cfg["password_file"]) or ""
    else:
        resolved_password = cfg["password"]

    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    logger.info(f"[*] Starting MQTT Simulator in '{args.mode}' mode (TLS: {args.tls})...")
    logger.info(f"    Broker: {args.host}:{args.port} | Client ID: {args.client_id} | Username: {resolved_username}")

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
        elif args.ca_cert and os.path.isfile(args.ca_cert):
            client.tls_set(ca_certs=args.ca_cert, cert_reqs=ssl.CERT_REQUIRED)
        else:
            client.tls_set(cert_reqs=ssl.CERT_REQUIRED)

    if resolved_username:
        client.username_pw_set(resolved_username, resolved_password if resolved_password is not None else None)

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

    if args.mode in ["send", "both"]:
        payload_cfg = load_payload_config("mqtt", logger=logger)
        if payload_cfg:
            device_ids = payload_cfg.get("device_ids") or [cfg["client_id"]]
            attributes = payload_cfg.get("attributes", [])
            default_count = payload_cfg.get("count", 1)
            default_interval = payload_cfg.get("interval", 1.0)
        else:
            device_ids = [cfg["client_id"]]
            attributes = None
            default_count = 1
            default_interval = 1.0

        count = args.count if args.count is not None else default_count
        interval = args.interval if args.interval is not None else default_interval

        logger.info(f"[*] [MQTT SEND] Broker: {args.host}:{args.port} | Devices: {len(device_ids)} | Count: {count} | Interval: {interval}s")

        def _mqtt_worker(dev_id: str, dev_logger: logging.Logger) -> Tuple[str, str]:
            dev_client = mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2, client_id=dev_id)
            if resolved_username:
                dev_client.username_pw_set(resolved_username, password=resolved_password)
            if args.tls:
                context = ssl.create_default_context()
                if args.insecure:
                    context.check_hostname = False
                    context.verify_mode = ssl.CERT_NONE
                elif cfg.get("ca_cert") and os.path.isfile(cfg["ca_cert"]):
                    context.load_verify_locations(cfg["ca_cert"])
                dev_client.tls_set_context(context)

            conn_ev = threading.Event()
            dev_conn_rc = [None]
            dev_disc_before_conn = [False]

            def _on_c(c, u, flags, rc, props=None):
                dev_conn_rc[0] = rc
                conn_ev.set()

            def _on_d(c, u, flags, rc, props=None):
                if dev_conn_rc[0] is None:
                    dev_disc_before_conn[0] = True
                conn_ev.set()

            dev_client.on_connect = _on_c
            dev_client.on_disconnect = _on_d

            try:
                dev_client.connect(args.host, args.port, keepalive=cfg.get("keepalive", 60))
                dev_client.loop_start()
            except ssl.SSLError as se:
                dev_logger.error(f"[!] TLS handshake error: {se}")
                return STATUS_AUTH_FAILED, f"AUTH_FAILED (SSLError: {se})"
            except (ConnectionRefusedError, OSError) as e:
                st, det, _ = classify_socket_error(e, stage="tcp_connect")
                dev_logger.error(f"[!] Socket connection error to {args.host}:{args.port}: {e}")
                return st, f"{st} ({e})"
            except Exception as e:
                dev_logger.error(f"[!] Unexpected error during MQTT connect: {e}")
                return STATUS_PROTO_ERROR, f"PROTO_ERROR ({e})"

            # Wait for CONNACK
            if not conn_ev.wait(timeout=args.timeout):
                try:
                    dev_client.disconnect()
                except Exception:
                    pass
                dev_client.loop_stop()
                dev_logger.error(f"[!] MQTT Connection timeout waiting for CONNACK")
                return STATUS_TIMEOUT, f"TIMEOUT after {args.timeout}s"

            if dev_disc_before_conn[0] and dev_conn_rc[0] is None:
                try:
                    dev_client.disconnect()
                except Exception:
                    pass
                dev_client.loop_stop()
                dev_logger.error(f"[!] Broker closed connection immediately without CONNACK")
                return STATUS_PROTO_ERROR, "PROTO_ERROR (closed before CONNACK)"

            rc = dev_conn_rc[0]
            rc_val = getattr(rc, "value", rc)
            try:
                rc_val = int(rc_val)
            except Exception:
                rc_val = 0 if str(rc_val).lower() in ("success", "0") else 1

            if rc_val != 0:
                try:
                    dev_client.disconnect()
                except Exception:
                    pass
                dev_client.loop_stop()
                if rc_val in (4, 5, 134, 135):
                    return STATUS_AUTH_FAILED, f"AUTH_FAILED (rc={rc_val})"
                elif rc_val in (1, 2, 130, 132, 133):
                    return STATUS_PROTO_ERROR, f"PROTO_ERROR (rc={rc_val})"
                else:
                    return STATUS_REFUSED, f"REFUSED (rc={rc_val})"

            # Publish loop
            pub_count = 0
            dev_t0 = time.time()
            i = 0
            while True:
                i += 1
                if count is not None and i > count:
                    break
                if count is None and (time.time() - dev_t0 >= args.duration):
                    break

                if attributes is not None:
                    telemetry = generate_payload(attributes, device_id=dev_id, seq=i, logger=dev_logger)
                else:
                    telemetry = {
                        "device_id": dev_id,
                        "seq": i,
                        "temperature": round(26.5 + i * 0.3, 2),
                        "humidity": round(55.0 - i * 0.1, 2),
                        "status": "ONLINE",
                        "timestamp": int(time.time() * 1000)
                    }
                body = json.dumps(telemetry)
                inf = dev_client.publish(args.topic, body, qos=args.qos)
                dev_logger.info(f"[{datetime.now().isoformat()}] ---> PUBLISH topic='{args.topic}' | MID={inf.mid} | QoS={args.qos} | Payload: {body}")
                inf.wait_for_publish(timeout=args.timeout)
                if inf.is_published():
                    pub_count += 1
                else:
                    dev_logger.warning(f"[!] PUBACK not received within {args.timeout}s for MID {inf.mid}")

                if count is not None and i < count:
                    time.sleep(interval)
                elif count is None:
                    time.sleep(interval)

            try:
                dev_client.disconnect()
            except Exception:
                pass
            dev_client.loop_stop()
            return STATUS_OK, f"OK x{pub_count}"

        max_dev_timeout = (args.timeout * (count or 5) + (interval * (count or 5)) + 5.0) if count else (args.duration + 5.0)
        return run_devices_parallel("mqtt", args.mode, device_ids, _mqtt_worker, timeout_per_device=max_dev_timeout, args=args, logger=logger)

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
