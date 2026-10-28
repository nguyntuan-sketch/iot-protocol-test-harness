#!/usr/bin/env python3
"""
AMQP IoT Protocol Simulator CLI
Protocol #5: AMQP 0-9-1 Client (pika)
Usage:
  python sim/amqp_sim.py --mode connect
  python sim/amqp_sim.py --mode connect --tls
  python sim/amqp_sim.py --mode both --count 3 --duration 8
  python sim/amqp_sim.py --mode send --count 5
  python sim/amqp_sim.py --mode listen --duration 10
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
    import pika
    from pika.exceptions import (
        ProbableAuthenticationError,
        ProbableAccessDeniedError,
        ChannelClosedByBroker,
        UnroutableError,
        NackError,
        AMQPConnectionError,
        IncompatibleProtocolError
    )
except ImportError:
    print("[ERROR] pika not installed. Install with: pip install pika", file=sys.stderr)
    sys.exit(1)


def get_default_config():
    cfg_path = os.path.join(base_dir, "config", "config.yaml")
    cfg = {
        "host": "127.0.0.1",
        "port": 5672,
        "port_tls": 5671,
        "vhost": "/",
        "username": "guest",
        "password": "guest",
        "exchange": "iot.telemetry",
        "routing_key": "device.telemetry",
        "queue": "iot.telemetry.queue",
        "downlink_queue": "iot.downlink.queue",
        "tls_enabled": False,
        "insecure_skip_verify": False
    }
    if yaml and os.path.isfile(cfg_path):
        try:
            with open(cfg_path, "r", encoding="utf-8") as f:
                raw = yaml.safe_load(f)
                target = raw.get("target", {})
                aq = raw.get("adapters", {}).get("amqp", {})
                tls = raw.get("tls", {})
                cfg["host"] = target.get("host", cfg["host"])
                cfg["port"] = aq.get("port", cfg["port"])
                cfg["vhost"] = aq.get("vhost", cfg["vhost"])
                cfg["username"] = aq.get("username", cfg["username"])
                cfg["password"] = aq.get("password", cfg["password"])
                cfg["exchange"] = aq.get("exchange", cfg["exchange"])
                cfg["routing_key"] = aq.get("routing_key", cfg["routing_key"])
                cfg["queue"] = aq.get("queue", cfg["queue"])
                cfg["tls_enabled"] = tls.get("enabled", cfg["tls_enabled"])
                cfg["insecure_skip_verify"] = tls.get("insecure_skip_verify", cfg["insecure_skip_verify"])
                ports = tls.get("ports", {})
                cfg["port_tls"] = ports.get("amqp_tls", cfg["port_tls"])
        except Exception as e:
            sys.stderr.write(f"[WARN] Failed to read config.yaml: {e}\n")
    return cfg


def setup_logger(args=None):
    return setup_simulator_logger("amqp", args=args)


def extract_amqp_error_detail(e: Exception) -> str:
    """Extract informative description from pika exceptions where str(e) is empty."""
    msg = str(e).strip()
    if not msg and hasattr(e, "args") and e.args:
        parts = []
        for a in e.args:
            s = str(a).strip() or repr(a).strip()
            if s:
                parts.append(s)
        msg = ", ".join(parts)
    if not msg:
        msg = repr(e)
    return msg


def main():
    cfg = get_default_config()
    parser = argparse.ArgumentParser(
        description="AMQP IoT Protocol Simulator CLI (pika AMQP 0-9-1)",
        epilog="Chế độ 'connect' mở cả connection và channel. CẢNH BÁO WINDOWS: trên Windows loopback, cổng đóng có thể mất ~2s mới báo refused (WinError 10061); khuyến nghị dùng --timeout >= 3.0 cho connect."
    )
    parser.add_argument("--mode", choices=["connect", "send", "listen", "both"], default="connect",
                        help="Operating mode: 'connect', 'send', 'listen', or 'both' (default: connect)")
    parser.add_argument("--host", default=cfg["host"], help=f"AMQP Broker host (default: {cfg['host']})")
    parser.add_argument("--port", type=int, default=None, help="AMQP Broker port (default: 5672 or 5671 if --tls)")
    parser.add_argument("--vhost", default=cfg["vhost"], help=f"Virtual host (default: {cfg['vhost']})")
    parser.add_argument("--username", default=cfg["username"], help="Username")
    parser.add_argument("--password", default=cfg["password"], help="Password")
    parser.add_argument("--exchange", default=cfg["exchange"], help=f"Target Exchange (default: {cfg['exchange']})")
    parser.add_argument("--routing-key", default=cfg["routing_key"], help=f"Routing Key (default: {cfg['routing_key']})")
    parser.add_argument("--queue", default=cfg["queue"], help=f"Telemetry Queue (default: {cfg['queue']})")
    parser.add_argument("--downlink-queue", default=cfg["downlink_queue"], help="Downlink Queue name")
    parser.add_argument("--device-id", default="sim_amqp_001", help="Device ID")
    parser.add_argument("--tls", action="store_true", default=cfg.get("tls_enabled", False), help="Enable AMQP TLS/SSL")
    parser.add_argument("--insecure", action="store_true", default=cfg.get("insecure_skip_verify", False),
                        help="Skip TLS certificate verification (insecure)")
    parser.add_argument("--count", type=int, default=1, help="Message count to send (default: 1)")
    parser.add_argument("--interval", type=float, default=1.0, help="Interval between sends in seconds (default: 1.0)")
    parser.add_argument("--duration", type=float, default=5.0, help="Listening duration in seconds (default: 5.0)")
    parser.add_argument("--timeout", type=float, default=3.0, help="Connection timeout in seconds (default: 3.0)")

    args = parser.parse_args()
    logger = setup_logger(args)

    if args.port is None:
        args.port = cfg["port_tls"] if args.tls else cfg["port"]

    logger.info(f"[*] Starting AMQP Simulator in '{args.mode}' mode (TLS: {args.tls})...")
    logger.info(f"    Broker: {args.host}:{args.port} | VHost: {args.vhost} | User: {args.username}")

    # Pre-flight TCP Connect Check
    is_connected, probe_status, probe_detail, probe_elapsed = tcp_preflight_probe(
        args.host, args.port, timeout=args.timeout
    )
    if not is_connected:
        logger.error(f"[!] TCP connection refused / failed to {args.host}:{args.port}: {probe_detail}")
        return format_result("amqp", args.mode, probe_status, probe_detail, elapsed=probe_elapsed)

    credentials = pika.PlainCredentials(args.username, args.password)
    ssl_options = None
    if args.tls:
        context = ssl.create_default_context()
        if args.insecure:
            logger.warning("[!] WARNING: TLS certificate verification is disabled (--insecure)")
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
        else:
            context.verify_mode = ssl.CERT_REQUIRED
        ssl_options = pika.SSLOptions(context)

    parameters = pika.ConnectionParameters(
        host=args.host,
        port=args.port,
        virtual_host=args.vhost,
        credentials=credentials,
        ssl_options=ssl_options,
        connection_attempts=1,
        retry_delay=1,
        socket_timeout=args.timeout
    )

    try:
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.confirm_delivery()
        logger.info(f"[{datetime.now().isoformat()}] <--- [AMQP CONNECTED] Connection & Channel opened successfully.")
    except (ProbableAuthenticationError, ProbableAccessDeniedError) as pae:
        err_msg = extract_amqp_error_detail(pae)
        logger.error(f"[!] AMQP Authentication / Authorization Failed: {err_msg}")
        return format_result("amqp", args.mode, STATUS_AUTH_FAILED, err_msg, stage="auth")
    except ChannelClosedByBroker as cbe:
        err_msg = extract_amqp_error_detail(cbe)
        logger.error(f"[!] AMQP Channel closed by broker: {err_msg}")
        if "403" in err_msg or "access_refused" in err_msg.lower():
            return format_result("amqp", args.mode, STATUS_AUTH_FAILED, err_msg, stage="auth")
        return format_result("amqp", args.mode, STATUS_PROTO_ERROR, err_msg, stage="channel")
    except IncompatibleProtocolError as ipe:
        err_msg = extract_amqp_error_detail(ipe)
        logger.error(f"[!] AMQP Incompatible Protocol: {err_msg}")
        return format_result("amqp", args.mode, STATUS_PROTO_ERROR, err_msg, stage="handshake")
    except AMQPConnectionError as ace:
        err_msg = extract_amqp_error_detail(ace)
        logger.error(f"[!] AMQP Connection Error: {err_msg}")
        status, detail, code = classify_socket_error(ace, stage="tcp_connect")
        if status == STATUS_PROTO_ERROR and ("10061" in err_msg or "refused" in err_msg.lower()):
            status = STATUS_REFUSED
        elif status == STATUS_PROTO_ERROR and ("timeout" in err_msg.lower() or "timed out" in err_msg.lower()):
            status = STATUS_TIMEOUT
        return format_result("amqp", args.mode, status, err_msg or detail, stage="tcp_connect")
    except (ConnectionRefusedError, OSError) as ce:
        err_msg = extract_amqp_error_detail(ce)
        logger.error(f"[!] Cannot connect to AMQP Broker at {args.host}:{args.port}: {err_msg}")
        status, detail, code = classify_socket_error(ce, stage="tcp_connect")
        return format_result("amqp", args.mode, status, err_msg or detail)
    except Exception as e:
        err_msg = extract_amqp_error_detail(e)
        logger.error(f"[!] AMQP unexpected connection error: {err_msg}")
        return format_result("amqp", args.mode, STATUS_PROTO_ERROR, err_msg, stage="tcp_connect")

    # Mode: CONNECT only
    if args.mode == "connect":
        try:
            channel.close()
            connection.close()
        except Exception:
            pass
        logger.info("[*] AMQP handshake & channel open successful. Exiting connect mode.")
        return format_result("amqp", "connect", STATUS_OK, "AMQP 0-9-1 connection & channel opened", stage="channel")

    messages_received = []

    # SEND or BOTH
    if args.mode in ["send", "both"]:
        logger.info(f"[*] Publishing {args.count} telemetry message(s) to Exchange '{args.exchange}' (rk='{args.routing_key}') with mandatory=True...")
        try:
            channel.exchange_declare(exchange=args.exchange, exchange_type='direct', durable=True)
            channel.queue_declare(queue=args.queue, durable=True)
            channel.queue_bind(queue=args.queue, exchange=args.exchange, routing_key=args.routing_key)
        except Exception as e:
            logger.warning(f"[!] Note on declare/bind: {e}")

        for i in range(1, args.count + 1):
            telemetry = {
                "device_id": args.device_id,
                "seq": i,
                "temperature": round(22.8 + i * 0.4, 2),
                "humidity": round(62.0 - i * 0.3, 2),
                "timestamp": int(time.time() * 1000)
            }
            body = json.dumps(telemetry)
            props = pika.BasicProperties(
                content_type='application/json',
                delivery_mode=pika.DeliveryMode.Persistent,
                headers={"device_id": args.device_id, "timestamp": telemetry["timestamp"]}
            )
            try:
                channel.basic_publish(
                    exchange=args.exchange,
                    routing_key=args.routing_key,
                    body=body.encode('utf-8'),
                    properties=props,
                    mandatory=True
                )
                logger.info(f"[{datetime.now().isoformat()}] ---> AMQP BASIC_PUBLISH [CONFIRMED ACK] rk='{args.routing_key}' | Payload: {body}")
            except UnroutableError as ue:
                logger.error(f"[!] AMQP UnroutableError (mandatory message was not routed to any queue): {ue}")
                connection.close()
                return format_result("amqp", args.mode, STATUS_PROTO_ERROR,
                                     f"UnroutableError: Message not routed (no matching queue bound to exchange '{args.exchange}')",
                                     stage="channel")
            except NackError as ne:
                logger.error(f"[!] AMQP NackError (Broker negatively acknowledged message): {ne}")
                connection.close()
                return format_result("amqp", args.mode, STATUS_PROTO_ERROR, f"NackError: {ne}", stage="channel")
            except Exception as pe:
                err_msg = extract_amqp_error_detail(pe)
                logger.error(f"[!] Publish failed: {err_msg}")
                connection.close()
                return format_result("amqp", args.mode, STATUS_PROTO_ERROR, f"Publish error: {err_msg}", stage="channel")

            if i < args.count:
                time.sleep(args.interval)

    # LISTEN or BOTH
    if args.mode in ["listen", "both"]:
        logger.info(f"[*] Listening on downlink queue '{args.downlink_queue}' for {args.duration}s...")
        try:
            channel.queue_declare(queue=args.downlink_queue, durable=True)
        except Exception:
            pass

        t0 = time.time()
        while time.time() - t0 < args.duration:
            method_frame, header_frame, body = channel.basic_get(queue=args.downlink_queue, auto_ack=True)
            if method_frame:
                body_str = body.decode('utf-8', errors='replace')
                logger.info(f"[{datetime.now().isoformat()}] <=== [AMQP DOWNLINK RECEIVED] tag={method_frame.delivery_tag} | Payload: {body_str}")
                messages_received.append(body_str)
            else:
                time.sleep(0.5)

        if len(messages_received) == 0:
            logger.info(f"[*] Khong nhan duoc ban tin AMQP downlink nao sau {args.duration} giay (Platform chua gui message).")

    try:
        connection.close()
    except Exception:
        pass

    logger.info("[*] AMQP Simulator finished cleanly.")
    if args.mode == "listen":
        if len(messages_received) == 0:
            detail = f"Recv:0 (no downlink received) after {args.duration}s"
        else:
            detail = f"Recv:{len(messages_received)} message(s) received"
    elif args.mode == "both":
        if len(messages_received) == 0:
            detail = f"Sent:{send_count}, Recv:0 (no downlink received)"
        else:
            detail = f"Sent:{send_count}, Recv:{len(messages_received)}"
    else:
        detail = "Completed cleanly"
    return format_result("amqp", args.mode, STATUS_OK, detail, stage="channel")


if __name__ == "__main__":
    sys.exit(main())
